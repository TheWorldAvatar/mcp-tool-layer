#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

usage() {
  cat <<'USAGE'
Usage:
  scripts/rebuild_pipeline_artifacts.sh [--model MODEL] [--direct] [--test] [--ontology ONTO] [--main-only] [--no-promote] [--no-rewire-mcp]

What this does:
  - Bootstraps required gitignored folders
  - Regenerates ALL candidate artefacts used by the pipeline:
      - iterations.json
      - prompts
      - generated MCP scripts (main.py + *_creation.py)
      - configs/generated_ontology_mcps.json
  - Generates top-entity SPARQL (writes directly to ai_generated_contents/)
  - (Default) Promotes candidate prompts+iterations into ai_generated_contents/ (pipeline default)
  - (Default) Rewires MCP config JSONs the pipeline uses to point at the newly generated MCP servers:
      - configs/run_created_mcp.json (llm_created_mcp -> ai_generated_contents_candidate.scripts.ontosynthesis.main)
      - configs/extension.json (mops_extension/ontospecies_extension -> candidate scripts)

Notes:
  - This script does NOT run the extraction pipeline; it only regenerates and wires artefacts.
  - Requires valid LLM credentials in your environment for generation steps.
  - --test runs the same flow but only for ONE ontology (default: ontosynthesis), to reduce cost/time.
  - --main-only skips everything and regenerates ONLY ai_generated_contents_candidate/scripts/<ontology>/main.py
    using existing candidate scripts (checks/base/relationships/entities_*.py). This is the fastest way to iterate on main.py.

Examples:
  scripts/rebuild_pipeline_artifacts.sh
  scripts/rebuild_pipeline_artifacts.sh --model gpt-5
  scripts/rebuild_pipeline_artifacts.sh --direct --model gpt-4o
  scripts/rebuild_pipeline_artifacts.sh --model gpt-5.2 --test
  scripts/rebuild_pipeline_artifacts.sh --test --ontology ontosynthesis
  scripts/rebuild_pipeline_artifacts.sh --test --ontology ontosynthesis --model gpt-4.1 --main-only
  scripts/rebuild_pipeline_artifacts.sh --no-rewire-mcp
USAGE
}

MODEL=""
DIRECT="false"
TEST_MODE="false"
ONTOLOGY="all"
MAIN_ONLY="false"
PROMOTE="true"
REWIRE_MCP="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="${2:-}"
      if [[ -z "${MODEL:-}" ]]; then
        echo "[ERROR] --model requires a value (e.g., --model gpt-5.2)" >&2
        usage
        exit 2
      fi
      shift 2
      ;;
    --direct)
      DIRECT="true"
      shift 1
      ;;
    --test)
      TEST_MODE="true"
      shift 1
      ;;
    --ontology)
      ONTOLOGY="${2:-}"
      if [[ -z "${ONTOLOGY:-}" ]]; then
        echo "[ERROR] --ontology requires a value (ontosynthesis|ontomops|ontospecies|all)" >&2
        usage
        exit 2
      fi
      shift 2
      ;;
    --main-only)
      MAIN_ONLY="true"
      shift 1
      ;;
    --no-promote)
      PROMOTE="false"
      shift 1
      ;;
    --no-rewire-mcp)
      REWIRE_MCP="false"
      shift 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "$TEST_MODE" == "true" ]]; then
  # default one-ontology test
  if [[ "$ONTOLOGY" == "all" || -z "$ONTOLOGY" ]]; then
    ONTOLOGY="ontosynthesis"
  fi
fi

case "$ONTOLOGY" in
  all|ontosynthesis|ontomops|ontospecies)
    ;;
  *)
    echo "[ERROR] --ontology must be one of: all, ontosynthesis, ontomops, ontospecies (got: $ONTOLOGY)" >&2
    exit 2
    ;;
esac

if [[ "$ONTOLOGY" == "all" ]]; then
  GEN_ARGS=(--all)
else
  GEN_ARGS=(--"$ONTOLOGY")
fi
if [[ -n "$MODEL" ]]; then
  GEN_ARGS+=(--model "$MODEL")
fi

# Script generation is direct-by-default in generation_main now.
# Keep this flag as an explicit opt-in for older branches / clarity.
if [[ "$DIRECT" == "true" ]]; then
  GEN_ARGS+=(--direct)
fi

echo "[INFO] Bootstrapping repo folders..."
"$PYTHON_BIN" scripts/bootstrap_repo.py

validate_import() {
  # Validate that a module can be imported without starting any server.
  # Runs in a subprocess and times out (so it won't hang forever).
  local module="$1"
  if command -v timeout >/dev/null 2>&1; then
    timeout 15s "$PYTHON_BIN" -c "import importlib; importlib.import_module('${module}')"
  else
    "$PYTHON_BIN" -c "import importlib; importlib.import_module('${module}')"
  fi
}

validate_server_start() {
  # Start an MCP server module briefly, then terminate.
  local module="$1"
  local out
  out="$(mktemp)"
  set +e
  if command -v timeout >/dev/null 2>&1; then
    timeout 8s "$PYTHON_BIN" -m "$module" >"$out" 2>&1
    rc=$?
  else
    "$PYTHON_BIN" -m "$module" >"$out" 2>&1 &
    pid=$!
    sleep 3
    kill "$pid" >/dev/null 2>&1
    wait "$pid" >/dev/null 2>&1
    rc=$?
  fi
  set -e

  if grep -q "Traceback (most recent call last)" "$out"; then
    echo "[FAIL] Server crashed on startup: $module"
    tail -n 80 "$out" >&2
    rm -f "$out"
    return 1
  fi
  if grep -q "Starting MCP server" "$out"; then
    echo "[OK] Server started (log detected): $module"
    rm -f "$out"
    return 0
  fi
  if [[ "${rc:-0}" -eq 124 ]]; then
    echo "[OK] Server stayed alive until timeout (no crash): $module"
    rm -f "$out"
    return 0
  fi
  echo "[WARN] Server start check inconclusive (no startup log, rc=${rc:-?}): $module"
  tail -n 40 "$out" >&2
  rm -f "$out"
  return 0
}

patch_generated_main_py() {
  # Apply small deterministic fixes to generated MCP main.py to avoid common runtime issues.
  local f="$1"
  "$PYTHON_BIN" - <<PY
from pathlib import Path

p = Path("${f}")
if not p.exists():
    raise SystemExit(0)

txt = p.read_text(encoding="utf-8")

# Avoid future-annotations in MCP server modules (prevents pydantic eval issues in some stacks)
txt = txt.replace("from __future__ import annotations\\n\\n", "")

# Ensure Optional is available for annotations if used
if "Optional[" in txt and "from typing import Optional" not in txt:
    lines = txt.splitlines()
    insert_at = 0
    for i, line in enumerate(lines[:80]):
        if line.startswith("from fastmcp import") or line.startswith("import fastmcp"):
            insert_at = i + 1
            break
    lines.insert(insert_at, "from typing import Optional")
    txt = "\\n".join(lines)

p.write_text(txt + ("" if txt.endswith("\\n") else "\\n"), encoding="utf-8")
print(f"[OK] Patched generated MCP main: {p}")
PY
}

if [[ "$MAIN_ONLY" == "true" ]]; then
  if [[ "$ONTOLOGY" == "all" ]]; then
    echo "[ERROR] --main-only requires a single ontology (use --ontology ontosynthesis|ontomops|ontospecies)" >&2
    exit 2
  fi

  # Avoid confusion from stale entrypoints: move any existing main.py out of the way first.
  MAIN_PY="ai_generated_contents_candidate/scripts/${ONTOLOGY}/main.py"
  if [[ -f "$MAIN_PY" ]]; then
    ts="$(date +%Y%m%d_%H%M%S 2>/dev/null || true)"
    if [[ -z "${ts:-}" ]]; then ts="backup"; fi
    mv "$MAIN_PY" "${MAIN_PY}.bak_${ts}"
    echo "[INFO] Moved existing main.py to ${MAIN_PY}.bak_${ts}"
  fi

  echo "[INFO] MAIN-ONLY: Regenerating only ai_generated_contents_candidate/scripts/${ONTOLOGY}/main.py ..."
  "$PYTHON_BIN" -m src.agents.scripts_and_prompts_generation.generation_main "${GEN_ARGS[@]}" --main-only

  MCP_MODULE="ai_generated_contents_candidate.scripts.${ONTOLOGY}.main"
  if [[ -f "$MAIN_PY" ]]; then
    echo "[INFO] Validating MCP entrypoint importability (no server run)..."
    set +e
    validate_import "$MCP_MODULE"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
      echo "[WARN] MCP entrypoint import failed; applying patch + retry (max 3)..."
      for i in 1 2 3; do
        patch_generated_main_py "$MAIN_PY"
        set +e
        validate_import "$MCP_MODULE"
        rc=$?
        set -e
        if [[ $rc -eq 0 ]]; then
          echo "[OK] MCP entrypoint import succeeded after patch."
          break
        fi
        echo "[WARN] Retry $i/3 failed."
      done
      if [[ $rc -ne 0 ]]; then
        echo "[ERROR] MCP entrypoint is still not importable after retries: $MCP_MODULE" >&2
        exit 1
      fi
    else
      echo "[OK] MCP entrypoint importable: $MCP_MODULE"
    fi

    echo "[INFO] Smoke-checking MCP server startup (start -> wait -> terminate)..."
    validate_server_start "$MCP_MODULE"
  fi

  echo "[OK] MAIN-ONLY complete."
  exit 0
fi

echo "[INFO] Generating candidate artefacts (scripts/prompts/iterations/config)..."
"$PYTHON_BIN" -m src.agents.scripts_and_prompts_generation.generation_main "${GEN_ARGS[@]}"

echo "[INFO] Ensuring generated scripts are importable packages..."
"$PYTHON_BIN" -m src.agents.scripts_and_prompts_generation.fix_package_structure

echo "[INFO] Generating top-entity parsing SPARQL (writes to ai_generated_contents/)..."
SPARQL_MODEL="${MODEL:-gpt-4o}"
"$PYTHON_BIN" -m src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent --ontosynthesis --model "$SPARQL_MODEL"

validate_import() {
  # Validate that a module can be imported without starting any server.
  # Runs in a subprocess and times out (so it won't hang forever).
  local module="$1"
  if command -v timeout >/dev/null 2>&1; then
    timeout 15s "$PYTHON_BIN" -c "import importlib; importlib.import_module('${module}')"
  else
    # Fallback without timeout (should still terminate for plain imports)
    "$PYTHON_BIN" -c "import importlib; importlib.import_module('${module}')"
  fi
}

validate_server_start() {
  # Start an MCP server module briefly, then terminate.
  # This is intentionally NOT a full E2E test; it just catches crash-on-start issues.
  # Success condition: no traceback and we either see a "Starting MCP server" log line
  # or the process stays alive until timeout.
  local module="$1"
  local out
  out="$(mktemp)"
  set +e
  if command -v timeout >/dev/null 2>&1; then
    timeout 8s "$PYTHON_BIN" -m "$module" >"$out" 2>&1
    rc=$?
  else
    "$PYTHON_BIN" -m "$module" >"$out" 2>&1 &
    pid=$!
    sleep 3
    kill "$pid" >/dev/null 2>&1
    wait "$pid" >/dev/null 2>&1
    rc=$?
  fi
  set -e

  if grep -q "Traceback (most recent call last)" "$out"; then
    echo "[FAIL] Server crashed on startup: $module"
    tail -n 80 "$out" >&2
    rm -f "$out"
    return 1
  fi
  if grep -q "Starting MCP server" "$out"; then
    echo "[OK] Server started (log detected): $module"
    rm -f "$out"
    return 0
  fi
  # timeout returns 124 when it kills a long-running process; that's acceptable here
  if [[ "${rc:-0}" -eq 124 ]]; then
    echo "[OK] Server stayed alive until timeout (no crash): $module"
    rm -f "$out"
    return 0
  fi
  # If it exited quickly with no traceback, treat as suspicious but not fatal.
  echo "[WARN] Server start check inconclusive (no startup log, rc=${rc:-?}): $module"
  tail -n 40 "$out" >&2
  rm -f "$out"
  return 0
}

patch_generated_main_py() {
  # Apply small deterministic fixes to generated MCP main.py to avoid common runtime issues.
  local f="$1"
  "$PYTHON_BIN" - <<PY
from pathlib import Path

p = Path("${f}")
if not p.exists():
    raise SystemExit(0)

txt = p.read_text(encoding="utf-8")

# 1) Avoid future-annotations in MCP server modules (prevents pydantic eval issues in some stacks)
txt = txt.replace("from __future__ import annotations\\n\\n", "")

# 2) Ensure Optional is available for annotations if used
if "Optional[" in txt and "from typing import Optional" not in txt:
    lines = txt.splitlines()
    insert_at = 0
    for i, line in enumerate(lines[:50]):
        if line.startswith("from fastmcp import") or line.startswith("import fastmcp"):
            insert_at = i + 1
            break
    lines.insert(insert_at, "from typing import Optional")
    txt = "\\n".join(lines)

# 3) FastMCP instruction compat
needle = "mcp.set_initial_instructions(INSTRUCTION_PROMPT)"
if needle in txt and "hasattr(mcp, \"set_initial_instructions\")" not in txt:
    txt = txt.replace(
        needle,
        "if hasattr(mcp, \\"set_initial_instructions\\"):\\n"
        "    mcp.set_initial_instructions(INSTRUCTION_PROMPT)\\n"
        "else:\\n"
        "    @mcp.prompt(name=\\"instruction\\")\\n"
        "    def instruction_prompt():\\n"
        "        return INSTRUCTION_PROMPT"
    )

p.write_text(txt + ("" if txt.endswith("\\n") else "\\n"), encoding="utf-8")
print(f"[OK] Patched generated MCP main: {p}")
PY
}

# Post-generation health check: generated MCP entrypoint must be importable.
# This does NOT start the server; it only imports the module and fails fast on errors.
MCP_MODULE="ai_generated_contents_candidate.scripts.ontosynthesis.main"
MAIN_PY="ai_generated_contents_candidate/scripts/ontosynthesis/main.py"
if [[ -f "$MAIN_PY" ]]; then
  echo "[INFO] Validating MCP entrypoint importability (no server run)..."
  set +e
  validate_import "$MCP_MODULE"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    echo "[WARN] MCP entrypoint import failed; applying patch + retry (max 3)..."
    for i in 1 2 3; do
      patch_generated_main_py "$MAIN_PY"
      set +e
      validate_import "$MCP_MODULE"
      rc=$?
      set -e
      if [[ $rc -eq 0 ]]; then
        echo "[OK] MCP entrypoint import succeeded after patch."
        break
      fi
      echo "[WARN] Retry $i/3 failed."
    done
    if [[ $rc -ne 0 ]]; then
      echo "[ERROR] MCP entrypoint is still not importable after retries: $MCP_MODULE" >&2
      exit 1
    fi
  else
    echo "[OK] MCP entrypoint importable: $MCP_MODULE"
  fi

  echo "[INFO] Smoke-checking MCP server startup (start -> wait -> terminate)..."
  validate_server_start "$MCP_MODULE"
fi

if [[ "$PROMOTE" == "true" ]]; then
  echo "[INFO] Promoting candidate prompts+iterations into ai_generated_contents/ (production tree)..."
  if [[ "$ONTOLOGY" == "all" ]]; then
    "$PYTHON_BIN" ai_generated_contents_candidate/transfer_and_overwrite.py --ontology all --real
  else
    "$PYTHON_BIN" ai_generated_contents_candidate/transfer_and_overwrite.py --ontology "$ONTOLOGY" --real
  fi
else
  echo "[INFO] Skipping promotion step (--no-promote)."
fi

backup_json() {
  local f="$1"
  if [[ -f "$f" ]]; then
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    cp -f "$f" "${f}.bak.${ts}"
  fi
}

if [[ "$REWIRE_MCP" == "true" ]]; then
  echo "[INFO] Rewiring MCP config JSONs to use newly generated MCP servers..."

  # Only rewire servers that were actually generated.
  # (generation_main returns non-zero on failure, but this also protects manual/partial runs.)
  HAVE_ONTO="false"
  HAVE_ONTOMOPS="false"
  HAVE_ONTOSPECIES="false"
  [[ -f "ai_generated_contents_candidate/scripts/ontosynthesis/main.py" ]] && HAVE_ONTO="true"
  [[ -f "ai_generated_contents_candidate/scripts/ontomops/main.py" ]] && HAVE_ONTOMOPS="true"
  [[ -f "ai_generated_contents_candidate/scripts/ontospecies/main.py" ]] && HAVE_ONTOSPECIES="true"

  if [[ "$HAVE_ONTO" != "true" ]]; then
    echo "[WARN] Missing ai_generated_contents_candidate/scripts/ontosynthesis/main.py; will NOT rewire configs/run_created_mcp.json"
  fi
  if [[ "$HAVE_ONTOMOPS" != "true" ]]; then
    echo "[WARN] Missing ai_generated_contents_candidate/scripts/ontomops/main.py; will NOT rewire configs/extension.json (mops_extension)"
  fi
  if [[ "$HAVE_ONTOSPECIES" != "true" ]]; then
    echo "[WARN] Missing ai_generated_contents_candidate/scripts/ontospecies/main.py; will NOT rewire configs/extension.json (ontospecies_extension)"
  fi

  backup_json "configs/run_created_mcp.json"
  backup_json "configs/extension.json"

  HAVE_ONTO="$HAVE_ONTO" HAVE_ONTOMOPS="$HAVE_ONTOMOPS" HAVE_ONTOSPECIES="$HAVE_ONTOSPECIES" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

def load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

def dump(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

have_onto = os.environ.get("HAVE_ONTO", "false").lower() == "true"
have_ontomops = os.environ.get("HAVE_ONTOMOPS", "false").lower() == "true"
have_ontospecies = os.environ.get("HAVE_ONTOSPECIES", "false").lower() == "true"

# 1) Main ontology MCP config used by pipeline (see configs/meta_task/meta_task_config.json)
if have_onto:
    run_created = Path("configs/run_created_mcp.json")
    rc = load(run_created)
    rc.setdefault("llm_created_mcp", {})
    rc["llm_created_mcp"].update({
        "command": "python",
        "args": ["-m", "ai_generated_contents_candidate.scripts.ontosynthesis.main"],
        "transport": "stdio",
    })
    dump(run_created, rc)
    print("[OK] Updated configs/run_created_mcp.json (llm_created_mcp -> candidate ontosynthesis MCP)")
else:
    print("[SKIP] Not updating configs/run_created_mcp.json (ontosynthesis MCP not generated)")

# 2) Extension MCP config used by pipeline for ontomops/ontospecies
ext = Path("configs/extension.json")
ec = load(ext)
did = False
if have_ontomops:
    ec.setdefault("mops_extension", {})
    ec["mops_extension"].update({
        "command": "python",
        "args": ["-m", "ai_generated_contents_candidate.scripts.ontomops.main"],
        "transport": "stdio",
    })
    did = True
else:
    print("[SKIP] Not updating configs/extension.json:mops_extension (ontomops MCP not generated)")

if have_ontospecies:
    ec.setdefault("ontospecies_extension", {})
    ec["ontospecies_extension"].update({
        "command": "python",
        "args": ["-m", "ai_generated_contents_candidate.scripts.ontospecies.main"],
        "transport": "stdio",
    })
    did = True
else:
    print("[SKIP] Not updating configs/extension.json:ontospecies_extension (ontospecies MCP not generated)")

if did:
    dump(ext, ec)
    print("[OK] Updated configs/extension.json (candidate MCPs where available)")
PY
else
  echo "[INFO] Skipping MCP config rewiring (--no-rewire-mcp)."
fi

echo "[OK] Done."


