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
  scripts/cleanup_results_and_raw_data.sh [--keep-hash HASH] [--real]

Default behavior:
  - Dry-run (prints what would be deleted)
  - keep-hash defaults to 0c57bac8

Actions:
  1) Remove pipeline "results" folders in data/:
     - Deletes directories matching data/<8-hex-hash>/
     - Deletes data/pipeline_run_*.json timing files (if present)
     - Keeps non-result assets like:
         data/doi_to_hash.json, data/resource.db, data/ontologies/, data/grounding_cache/, ...

  2) Remove all PDFs in raw_data/ except the DOI mapped to the kept hash:
     - Keeps <doi>.pdf and <doi>_si.pdf for the DOI mapped to --keep-hash
     - Prints the DOI (underscore form and slash form)

  3) Clean evaluation run artefacts:
     - Deletes evaluation/data/result/ and evaluation/data/full_result/
     - In evaluation/data/merged_tll/, deletes all <8-hex-hash>/ dirs except --keep-hash
     - Deletes evaluation/ontomops_derivation_evaluation/reports/ (if present)
     - Deletes evaluation/__pycache__/ and evaluation/**/*.pyc (if present)

Examples:
  scripts/cleanup_results_and_raw_data.sh
  scripts/cleanup_results_and_raw_data.sh --keep-hash 0c57bac8 --real
USAGE
}

KEEP_HASH="0c57bac8"
REAL="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-hash)
      KEEP_HASH="${2:-}"
      shift 2
      ;;
    --real)
      REAL="true"
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

if [[ ! "$KEEP_HASH" =~ ^[0-9a-fA-F]{8}$ ]]; then
  echo "[ERROR] --keep-hash must be 8 hex chars (got: $KEEP_HASH)" >&2
  exit 2
fi

echo "[INFO] Resolving DOI for hash: $KEEP_HASH"
DOI_US="$(KEEP_HASH="$KEEP_HASH" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

hv = os.environ["KEEP_HASH"].strip().lower()
mp = json.loads(Path("data/doi_to_hash.json").read_text(encoding="utf-8"))
hits = [doi for doi, h in mp.items() if str(h).strip().lower() == hv]
if not hits:
    raise SystemExit(3)
print(hits[0])
PY
)" || {
  echo "[ERROR] Could not find hash $KEEP_HASH in data/doi_to_hash.json" >&2
  exit 3
}
DOI_SLASH="${DOI_US/_//}"
echo "[OK] keep-hash DOI:"
echo "  - underscore: $DOI_US"
echo "  - slash:      $DOI_SLASH"

echo
echo "[INFO] Scanning data/ for hash result folders..."

# Identify hash-like result directories under data/
mapfile -t DATA_HASH_DIRS < <(find data -maxdepth 1 -mindepth 1 -type d -regextype posix-extended -regex 'data/[0-9a-fA-F]{8}' | sort || true)

echo "[INFO] data/ hash result dirs: ${#DATA_HASH_DIRS[@]}"
if [[ ${#DATA_HASH_DIRS[@]} -gt 0 ]]; then
  echo "Will delete (data/):"
  for d in "${DATA_HASH_DIRS[@]}"; do
    echo "  - $d"
  done
fi

mapfile -t PIPELINE_RUN_FILES < <(ls -1 data/pipeline_run_*.json 2>/dev/null | sort || true)
if [[ ${#PIPELINE_RUN_FILES[@]} -gt 0 ]]; then
  echo "Will delete (timing):"
  for f in "${PIPELINE_RUN_FILES[@]}"; do
    echo "  - $f"
  done
fi

echo
echo "[INFO] Scanning raw_data/ for PDFs to keep/delete..."

KEEP_PDF="raw_data/${DOI_US}.pdf"
KEEP_SI="raw_data/${DOI_US}_si.pdf"

mapfile -t RAW_PDFS < <(find raw_data -maxdepth 1 -type f -name '*.pdf' | sort || true)

DEL_RAW=()
for f in "${RAW_PDFS[@]}"; do
  if [[ "$f" == "$KEEP_PDF" || "$f" == "$KEEP_SI" ]]; then
    continue
  fi
  DEL_RAW+=("$f")
done

echo "[INFO] raw_data PDFs total: ${#RAW_PDFS[@]}"
echo "[INFO] raw_data PDFs to delete: ${#DEL_RAW[@]}"
echo "[INFO] raw_data PDFs to keep:"
echo "  - $KEEP_PDF"
echo "  - $KEEP_SI"

echo
echo "[INFO] Scanning evaluation/ for run artefacts to delete..."

EVAL_DELETE_DIRS=()
if [[ -d "evaluation/data/result" ]]; then
  EVAL_DELETE_DIRS+=("evaluation/data/result")
fi
if [[ -d "evaluation/data/full_result" ]]; then
  EVAL_DELETE_DIRS+=("evaluation/data/full_result")
fi
if [[ -d "evaluation/ontomops_derivation_evaluation/reports" ]]; then
  EVAL_DELETE_DIRS+=("evaluation/ontomops_derivation_evaluation/reports")
fi
if [[ -d "evaluation/__pycache__" ]]; then
  EVAL_DELETE_DIRS+=("evaluation/__pycache__")
fi

mapfile -t EVAL_PYC < <(find evaluation -type f -name '*.pyc' 2>/dev/null | sort || true)

# evaluation/data/merged_tll/<hash> folders
EVAL_MERGED_BASE="evaluation/data/merged_tll"
EVAL_MERGED_HASH_DIRS=()
if [[ -d "$EVAL_MERGED_BASE" ]]; then
  mapfile -t EVAL_MERGED_HASH_DIRS < <(
    find "$EVAL_MERGED_BASE" -maxdepth 1 -mindepth 1 -type d -regextype posix-extended -regex "${EVAL_MERGED_BASE}/[0-9a-fA-F]{8}" 2>/dev/null | sort || true
  )
fi

EVAL_MERGED_TO_DELETE=()
for d in "${EVAL_MERGED_HASH_DIRS[@]}"; do
  bn="$(basename "$d")"
  if [[ "${bn,,}" == "${KEEP_HASH,,}" ]]; then
    continue
  fi
  EVAL_MERGED_TO_DELETE+=("$d")
done

echo "[INFO] evaluation dirs to delete: ${#EVAL_DELETE_DIRS[@]}"
for d in "${EVAL_DELETE_DIRS[@]}"; do
  echo "  - $d"
done

echo "[INFO] evaluation merged_tll hash dirs to delete: ${#EVAL_MERGED_TO_DELETE[@]}"
if [[ ${#EVAL_MERGED_TO_DELETE[@]} -gt 0 ]]; then
  for d in "${EVAL_MERGED_TO_DELETE[@]}"; do
    echo "  - $d"
  done
fi

echo "[INFO] evaluation .pyc files to delete: ${#EVAL_PYC[@]}"
if [[ ${#EVAL_PYC[@]} -gt 0 ]]; then
  for f in "${EVAL_PYC[@]}"; do
    echo "  - $f"
  done
fi

if [[ "$REAL" != "true" ]]; then
  echo
  echo "[DRY-RUN] No files were deleted. Re-run with --real to apply."
  exit 0
fi

echo
echo "[REAL] Deleting data/ hash result folders..."
for d in "${DATA_HASH_DIRS[@]}"; do
  rm -rf "$d"
done

echo "[REAL] Deleting data/pipeline_run_*.json (if any)..."
for f in "${PIPELINE_RUN_FILES[@]}"; do
  rm -f "$f"
done

echo "[REAL] Deleting raw_data PDFs except kept DOI..."
for f in "${DEL_RAW[@]}"; do
  rm -f "$f"
done

echo "[REAL] Deleting evaluation run artefacts..."
for d in "${EVAL_DELETE_DIRS[@]}"; do
  rm -rf "$d"
done
for d in "${EVAL_MERGED_TO_DELETE[@]}"; do
  rm -rf "$d"
done
for f in "${EVAL_PYC[@]}"; do
  rm -f "$f"
done

echo "[OK] Cleanup complete."


