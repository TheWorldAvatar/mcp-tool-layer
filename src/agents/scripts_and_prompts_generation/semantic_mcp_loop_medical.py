#!/usr/bin/env python3
"""Medical semantic MCP closed loop.

Outer loop: regenerate medical MCP → Level-1 ruff/contract repair → mock OP note →
full ReAct extract + KG building (default) → HermiT A-Box/T-Box gate → feedback regenerate.

Usage:
  python -m src.agents.scripts_and_prompts_generation.semantic_mcp_loop_medical \\
    --max-outer 2 --fixture tests/fixtures/medical_semantic_mock.json

Offline harness-only (no pipeline LLM):
  ... --abox-mode harness --no-llm --fixture tests/fixtures/medical_semantic_mock.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rdflib import Graph
from rdflib.namespace import RDF

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    run_agentic_generation_experiment,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _import_generated_main_module,
)
from src.agents.scripts_and_prompts_generation.fix_package_structure import (
    create_init_files,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    invoke_json,
    level1_repair_loop,
)
from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
    run_llm_artifact_editor,
)
from src.agents.scripts_and_prompts_generation.semantic_loop_core import (
    load_semantic_loop_config,
)
from src.pipelines.utils.hash import generate_hash

ROOT = Path(__file__).resolve().parents[3]
LOOP_CONFIG = load_semantic_loop_config(
    ROOT / "configs/semantic_loops/medical.json",
    repository_root=ROOT,
)
DEFAULT_META_TASK = LOOP_CONFIG.meta_task_config
DEFAULT_TBOX = LOOP_CONFIG.tbox_paths[0]
DEFAULT_OUTPUT_ROOT = LOOP_CONFIG.output_root
REQUIRED_COVERAGE = list(LOOP_CONFIG.required_coverage)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _load_reasoner_module():
    path = ROOT / "scripts" / "validate_abox_with_reasoner.py"
    spec = importlib.util.spec_from_file_location("validate_abox_with_reasoner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load reasoner module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unwrap_tool(fn: Any) -> Callable[..., Any] | None:
    if callable(fn):
        return fn
    inner = getattr(fn, "fn", None)
    if callable(inner):
        return inner
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Fixture must be a JSON object: {path}")
    return data


def _fixture_prompt(context: AgenticGenerationContext) -> str:
    classes = sorted((context.parsed.get("classes") or {}).keys())
    required = context.contract.get("required_links") or []
    return textwrap.dedent(
        f"""
        You generate a medical operative-report mock fixture for semantic MCP testing.

        Return only a valid JSON object with this exact shape:
        {{
          "document_md": "<German OP note markdown>",
          "hints": {{"<ClassLocal>": {{"label": "...", "<datatype>": "..."}}}},
          "coverage": ["MedicalCase", "PatientInfo", ...]
        }}

        Hard requirements:
        - `document_md` is a short fictional German thoracic OP note (markdown).
        - Every scalar value in `hints` must be grounded in `document_md`.
        - `hints` keys are ontology class local names from: {classes}.
        - Cover all shell required-link classes and also Diagnosis + PathologyOutcome.
        - Required links from meta-task: {json.dumps(required, ensure_ascii=False)}.
        - Required coverage classes: {REQUIRED_COVERAGE}.
        - SurgicalApproach: set exactly one of offen/VATS/RATS to "1"; others "-" or omit.
        - Checklist fields use "1" / "-" strings, never JSON booleans.
        - Include PatientInfo.Fall_Nr, CaseTimeline.OP_Datum, SurgicalTeam.Operateur_in,
          at least one Procedure flag, one Diagnosis flag, and PathologyOutcome.R0 or Stadium.
        - Do not invent class or property names outside the T-Box class list above.
        - Return only JSON. No markdown fences, no explanations.
        """
    ).strip()


def generate_mock_fixture(
    *,
    context: AgenticGenerationContext,
    model: str,
    dest: Path,
) -> dict[str, Any]:
    result = invoke_json(model, _fixture_prompt(context))
    data = result.data
    if not isinstance(data.get("hints"), dict):
        raise ValueError("Fixture LLM response missing object `hints`")
    if not isinstance(data.get("document_md"), str) or not data["document_md"].strip():
        raise ValueError("Fixture LLM response missing `document_md`")
    coverage = data.get("coverage") or REQUIRED_COVERAGE
    data["coverage"] = list(coverage)
    _write_json(dest, data)
    return data


def _coverage_present_in_graph(ttl_text: str, coverage: list[str]) -> dict[str, bool]:
    graph = Graph()
    graph.parse(data=ttl_text, format="turtle")
    found_locals: set[str] = set()
    for _, _, obj in graph.triples((None, RDF.type, None)):
        text = str(obj)
        local = text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        if local:
            found_locals.add(local)
    return {name: name in found_locals for name in coverage}


def run_mcp_harness(
    *,
    scripts_dir: Path,
    fixture: dict[str, Any],
    abox_path: Path,
    doi: str = "semantic-mock-doi",
    top_name: str = "top",
    entity_label: str = "Semantic Mock MedicalCase",
) -> dict[str, Any]:
    """In-process materialize_hints (or tool_calls fallback) → write abox.ttl."""
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="semantic_mcp_harness_") as tmp_dir:
            os.environ["TWA_AGENTIC_DATA_DIR"] = tmp_dir
            module = _import_generated_main_module(scripts_dir, "medical")
            materialize = _unwrap_tool(getattr(module, "materialize_hints", None))
            tool_calls = fixture.get("tool_calls")
            ttl = ""
            status = "error"
            message = ""
            created: Any = None

            if materialize is not None and isinstance(fixture.get("hints"), dict):
                raw = materialize(
                    doi,
                    top_name,
                    entity_label,
                    json.dumps(fixture["hints"], ensure_ascii=False),
                )
                try:
                    result = json.loads(str(raw or "{}"))
                except json.JSONDecodeError as exc:
                    return {
                        "ok": False,
                        "mode": "materialize_hints",
                        "error": f"non-JSON harness result: {exc}",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                status = str(result.get("status") or "")
                message = str(result.get("message") or "")
                ttl = str(result.get("ttl") or "")
                created = result.get("created")
                if status != "ok" or not ttl.strip():
                    return {
                        "ok": False,
                        "mode": "materialize_hints",
                        "status": status,
                        "message": message,
                        "created": created,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
            elif isinstance(tool_calls, list) and tool_calls:
                call_log: list[dict[str, Any]] = []
                for call in tool_calls:
                    name = str((call or {}).get("tool") or "").strip()
                    args = (call or {}).get("args") or {}
                    if not isinstance(args, dict):
                        args = {}
                    fn = _unwrap_tool(getattr(module, name, None))
                    if fn is None:
                        return {
                            "ok": False,
                            "mode": "tool_calls",
                            "error": f"missing tool {name}",
                            "call_log": call_log,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        }
                    raw = fn(**args)
                    call_log.append({"tool": name, "raw": str(raw)[:500]})
                    if name in {"export_memory", "materialize_hints"}:
                        try:
                            parsed = json.loads(str(raw or "{}"))
                            if isinstance(parsed, dict) and parsed.get("ttl"):
                                ttl = str(parsed["ttl"])
                            elif isinstance(raw, str) and ("@" in raw or "rdf:" in raw):
                                ttl = str(raw)
                        except json.JSONDecodeError:
                            if isinstance(raw, str) and len(raw) > 20:
                                ttl = str(raw)
                if not ttl.strip():
                    export_fn = _unwrap_tool(getattr(module, "export_memory", None))
                    if export_fn is not None:
                        ttl = str(export_fn())
                if not ttl.strip():
                    return {
                        "ok": False,
                        "mode": "tool_calls",
                        "error": "tool_calls produced no TTL",
                        "call_log": call_log,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                status = "ok"
            else:
                return {
                    "ok": False,
                    "mode": "none",
                    "error": "fixture has neither usable hints nor tool_calls, "
                    "or materialize_hints is missing",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }

            abox_path.parent.mkdir(parents=True, exist_ok=True)
            abox_path.write_text(ttl, encoding="utf-8")
            coverage = list(fixture.get("coverage") or REQUIRED_COVERAGE)
            present = _coverage_present_in_graph(ttl, coverage)
            return {
                "ok": True,
                "mode": "materialize_hints" if materialize is not None else "tool_calls",
                "status": status,
                "message": message,
                "created": created,
                "abox_path": str(abox_path),
                "coverage_present": present,
                "coverage_ok": all(present.values()),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        if previous_data_dir is None:
            os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
        else:
            os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir


def run_reasoner_gate(
    *,
    tbox_path: Path,
    abox_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """OWL-RL checks + hard HermiT consistency (required)."""
    reasoner = _load_reasoner_module()
    report = reasoner.validate(
        [tbox_path],
        [abox_path],
        run_hermit=True,
    )
    hermit = report.get("hermit") or {}
    hermit_available = bool(hermit.get("available")) and "error" not in hermit
    hermit_consistent = hermit.get("consistent")
    warnings: list[str] = []
    failures_extra: list[str] = []
    owlrl_ok = bool(report.get("ok"))

    if not hermit_available:
        msg = "HermiT required but unavailable"
        if hermit.get("reason"):
            msg += f": {hermit['reason']}"
        if hermit.get("error"):
            msg += f": {hermit['error']}"
        failures_extra.append(msg)
        warnings.append(msg)
        hermit_hard_fail = True
        ok = False
    elif hermit_consistent is False:
        failures_extra.append("HermiT reported ontology inconsistency")
        hermit_hard_fail = True
        ok = False
    else:
        hermit_hard_fail = False
        ok = owlrl_ok and hermit_consistent is True

    merged_failures = list(report.get("failures") or []) + failures_extra
    out = {
        **report,
        "ok": ok,
        "failures": merged_failures,
        "gate_warnings": warnings,
        "hermit_hard_fail": hermit_hard_fail,
        "hermit_required": True,
        "owlrl_ok": owlrl_ok,
    }
    _write_json(report_path, out)
    return out


def _write_medical_mcp_launcher(artifact_root: Path) -> Path:
    """Launcher so stdio MCP can import package-relative generated medical scripts."""
    create_init_files(artifact_root)
    launcher = artifact_root / "launch_medical_mcp.py"
    launcher.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import runpy
            import sys
            from pathlib import Path

            ROOT = Path(__file__).resolve().parent
            SCRIPTS = ROOT / "scripts"
            if str(SCRIPTS) not in sys.path:
                sys.path.insert(0, str(SCRIPTS))
            runpy.run_module("medical.main", run_name="__main__")
            """
        ),
        encoding="utf-8",
    )
    return launcher


def _write_react_mcp_config(
    *,
    artifact_root: Path,
    config_path: Path,
    data_dir: Path,
) -> str:
    """Write MCP stdio config with a full env so DATA_DIR is not lost.

    langchain-mcp-adapters replaces the process environment when ``env`` is set,
    so we must pass a complete ``os.environ`` copy plus our overrides.
    """
    launcher = _write_medical_mcp_launcher(artifact_root)
    env = dict(os.environ)
    env["TWA_AGENTIC_DATA_DIR"] = str(data_dir.resolve())
    env["TWA_GENERATED_ARTIFACT_ROOT"] = str(artifact_root.resolve())
    # Ensure PATH survives for python/java (HermiT not needed in MCP, but PATH is).
    env.setdefault("PATH", os.environ.get("PATH", ""))
    payload = {
        "medical_mcp": {
            "command": sys.executable,
            "args": [str(launcher.resolve())],
            "transport": "stdio",
            "cwd": str(ROOT),
            "env": env,
        }
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config_path.name


def _merge_ttl_files(ttl_paths: list[Path], dest: Path) -> dict[str, Any]:
    graph = Graph()
    loaded: list[str] = []
    for path in ttl_paths:
        if not path.is_file():
            continue
        graph.parse(str(path), format="turtle")
        loaded.append(str(path))
    if not loaded:
        return {"ok": False, "error": "no TTL files to merge", "loaded": []}
    dest.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(dest), format="turtle")
    return {
        "ok": True,
        "loaded": loaded,
        "triples": len(graph),
        "abox_path": str(dest),
    }


def run_react_pipeline_against_mock(
    *,
    artifact_root: Path,
    meta_task_config: Path,
    fixture: dict[str, Any],
    abox_path: Path,
    runtime_root: Path,
    doi: str = "semantic-mock-medical-case",
) -> dict[str, Any]:
    """Stage mock OP markdown and run top/main extract + ReAct KG building."""
    from src.pipelines.main_kg_building.build import run_step as main_kg
    from src.pipelines.main_ontology_extractions.extract import run_step as main_extract
    from src.pipelines.top_entity_extraction.extract import run_step as top_extract
    from src.pipelines.top_entity_kg_building.build import run_step as top_kg

    started = time.perf_counter()
    document_md = str(fixture.get("document_md") or "").strip()
    if not document_md:
        return {
            "ok": False,
            "mode": "react",
            "error": "fixture missing document_md",
            "elapsed_seconds": 0.0,
        }

    doi_hash = generate_hash(doi)
    data_dir = runtime_root.resolve()
    case_dir = data_dir / doi_hash
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    stitched = case_dir / f"{doi_hash}_stitched.md"
    stitched.write_text(document_md + "\n", encoding="utf-8")

    mcp_config_name = _write_react_mcp_config(
        artifact_root=artifact_root,
        config_path=ROOT / "configs" / "test_mcp_config_medical_semantic_loop.json",
        data_dir=data_dir,
    )
    cfg = {
        "data_dir": str(data_dir),
        "project_root": str(ROOT),
        "meta_task_config": str(meta_task_config),
        "test_mcp_config": mcp_config_name,
        "force_react_kg": True,
        "skip_materialize_hints": True,
    }

    previous_artifact_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT")
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    step_results: dict[str, bool] = {}
    try:
        os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(artifact_root.resolve())
        os.environ["TWA_AGENTIC_DATA_DIR"] = str(data_dir)
        steps = (
            ("top_entity_extraction", top_extract),
            ("top_entity_kg_building", top_kg),
            ("main_ontology_extractions", main_extract),
            ("main_kg_building", main_kg),
        )
        for name, fn in steps:
            _log(f"[react] step {name} hash={doi_hash}")
            ok = bool(fn(doi_hash, cfg))
            step_results[name] = ok
            if not ok:
                return {
                    "ok": False,
                    "mode": "react",
                    "doi_hash": doi_hash,
                    "stitched_path": str(stitched),
                    "step_results": step_results,
                    "error": f"pipeline step failed: {name}",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }

        medical_output = case_dir / "medical_output"
        ttl_paths = sorted(medical_output.glob("*.ttl")) if medical_output.is_dir() else []
        if not ttl_paths:
            # Fallback intermediates sometimes used before publish.
            for pattern in ("iteration_1.ttl", "memory/*.ttl", "exports/*.ttl"):
                ttl_paths.extend(sorted(case_dir.glob(pattern)))
        merge = _merge_ttl_files(ttl_paths, abox_path)
        if not merge.get("ok"):
            return {
                "ok": False,
                "mode": "react",
                "doi_hash": doi_hash,
                "stitched_path": str(stitched),
                "step_results": step_results,
                "medical_output": str(medical_output),
                "error": merge.get("error") or "failed to merge A-Box TTL",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }

        ttl_text = abox_path.read_text(encoding="utf-8")
        coverage = list(fixture.get("coverage") or REQUIRED_COVERAGE)
        present = _coverage_present_in_graph(ttl_text, coverage)
        return {
            "ok": True,
            "mode": "react",
            "doi_hash": doi_hash,
            "stitched_path": str(stitched),
            "step_results": step_results,
            "medical_output": str(medical_output),
            "ttl_sources": merge.get("loaded"),
            "triples": merge.get("triples"),
            "abox_path": str(abox_path),
            "coverage_present": present,
            "coverage_ok": all(present.values()),
            "mcp_config": mcp_config_name,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "react",
            "doi_hash": doi_hash,
            "stitched_path": str(stitched),
            "step_results": step_results,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        if previous_artifact_root is None:
            os.environ.pop("TWA_GENERATED_ARTIFACT_ROOT", None)
        else:
            os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = previous_artifact_root
        if previous_data_dir is None:
            os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
        else:
            os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir


def package_semantic_feedback(
    *,
    abox_build: dict[str, Any],
    reasoner: dict[str, Any] | None,
    coverage: list[str],
) -> str:
    lines = [
        "# Semantic MCP feedback (feed into next medical MCP regeneration)",
        "",
        "## A-Box build",
        f"- ok: {abox_build.get('ok')}",
        f"- mode: {abox_build.get('mode')}",
        f"- error/message: {abox_build.get('error') or abox_build.get('message') or ''}",
    ]
    if abox_build.get("step_results"):
        lines.append(f"- pipeline steps: {json.dumps(abox_build.get('step_results'))}")
    present = abox_build.get("coverage_present") or {}
    missing = [name for name in coverage if not present.get(name)]
    if missing:
        lines.append(f"- missing coverage classes in A-Box: {', '.join(missing)}")
    if reasoner is not None:
        lines.extend(
            [
                "",
                "## Reasoner (OWL-RL + HermiT required)",
                f"- ok: {reasoner.get('ok')}",
                f"- owlrl_ok: {reasoner.get('owlrl_ok')}",
                f"- hermit_hard_fail: {reasoner.get('hermit_hard_fail')}",
            ]
        )
        failures = list(reasoner.get("failures") or [])[:40]
        if failures:
            lines.append("- failures:")
            lines.extend(f"  - {item}" for item in failures)
        hermit = reasoner.get("hermit") or {}
        inconsistent = hermit.get("inconsistent_classes") or []
        if inconsistent:
            lines.append("- HermiT inconsistent classes:")
            lines.extend(f"  - {item}" for item in inconsistent[:20])
        for warning in reasoner.get("gate_warnings") or []:
            lines.append(f"- warning: {warning}")
    lines.extend(
        [
            "",
            "## Repair guidance",
            "- Ensure create_*/add_* tools and ReAct KG prompts emit only T-Box classes/properties.",
            "- Fix domain/range mismatches (wrong typed subjects/objects on object properties).",
            "- Preserve required MedicalCase shell links and mutual exclusion for offen/VATS/RATS.",
            "- Do not assert domain-namespaced Thing; use concrete medical classes only.",
        ]
    )
    return "\n".join(lines) + "\n"


def apply_semantic_feedback_repairs(
    *,
    context: AgenticGenerationContext,
    feedback_text: str,
    model: str,
    max_repairs: int,
    allow_llm: bool,
) -> list[dict[str, Any]]:
    if not allow_llm or max_repairs <= 0 or not feedback_text.strip():
        return []
    scripts_dir = Path(context.scripts_dir)
    targets = [
        path
        for path in sorted(scripts_dir.glob("*.py"))
        if not path.name.startswith("main_part_") and "_attempt_" not in path.name
    ]
    _log("[semantic] plain LLM transactional repair from reasoner feedback")
    report = run_llm_artifact_editor(
        model_name=model,
        output_root=Path(context.output_root),
        targets=targets,
        task_prompt=(
            "Diagnose the semantic/reasoner failures and decide which generated Python "
            "files require changes. Produce the smallest coherent repair using only T-Box "
            "classes/properties and the generation contract. Preserve required MedicalCase "
            "links and mutual-exclusion semantics. The orchestrator deliberately does not "
            "route failures to files for you.\n\nReasoner feedback:\n"
            + feedback_text
        ),
        max_attempts=5,
    )
    return [report]


def exercise_semantic_fail(scripts_dir: Path) -> list[str]:
    """Mutate a fixture-used datatype local so the next A-Box has an unknown property."""
    changed: list[str] = []
    entities = scripts_dir / "medical_creation_entities.py"
    if not entities.is_file():
        return changed
    text = entities.read_text(encoding="utf-8")
    # Deterministic scripts pass local names into _add_literal(..., "OP_Datum", ...);
    # renaming that local makes materialize_hints emit an unknown T-Box property.
    needle = '_add_literal(str(iri), "OP_Datum", OP_Datum)'
    replacement = '_add_literal(str(iri), "BogusSemanticFailProp", OP_Datum)'
    if needle not in text:
        # Fallback: first _add_literal local-name argument.
        pattern = re.compile(
            r'(_add_literal\(str\(iri\),\s*")([A-Za-z0-9_]+)(")'
        )
        match = pattern.search(text)
        if not match:
            return changed
        text = pattern.sub(
            r'\1BogusSemanticFailProp\3',
            text,
            count=1,
        )
    else:
        text = text.replace(needle, replacement, 1)
    entities.write_text(text, encoding="utf-8")
    changed.append(str(entities))
    return changed


def regenerate_medical_mcp(
    *,
    output_root: Path,
    meta_task_config: Path,
    feedback_path: Path | None,
) -> AgenticGenerationContext:
    output_root.mkdir(parents=True, exist_ok=True)
    if feedback_path and feedback_path.is_file():
        sticky = output_root / "semantic_feedback.md"
        sticky.write_text(feedback_path.read_text(encoding="utf-8"), encoding="utf-8")
        _log(f"[regen] copied semantic feedback → {sticky}")

    summary = run_agentic_generation_experiment(
        ["medical"],
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        generate_scripts=True,
        generate_prompts=True,
        repair_loop=True,
        max_repair_iterations=2,
    )
    _write_json(output_root / "regen_summary.json", summary)
    context = build_agentic_generation_context(
        ontology_name="medical",
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        write_files=False,
    )
    return context


def run_outer_loop(
    *,
    output_root: Path,
    meta_task_config: Path,
    tbox_path: Path,
    max_outer: int,
    max_ruff_repairs: int,
    model: str,
    fixture_path: Path | None,
    allow_llm: bool,
    exercise_semantic: bool,
    abox_mode: str = "react",
) -> dict[str, Any]:
    if abox_mode not in {"react", "harness"}:
        raise ValueError(f"Unsupported abox_mode: {abox_mode}")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    iterations: list[dict[str, Any]] = []
    feedback_path: Path | None = None
    overall_ok = False

    for outer in range(max(1, max_outer)):
        iter_dir = run_dir / f"iter_{outer}"
        if iter_dir.exists():
            shutil.rmtree(iter_dir)
        iter_dir.mkdir(parents=True, exist_ok=True)
        _log(f"[outer {outer}] regenerate medical MCP → {iter_dir}")

        context = regenerate_medical_mcp(
            output_root=iter_dir,
            meta_task_config=meta_task_config,
            feedback_path=feedback_path,
        )
        scripts_dir = Path(context.scripts_dir)
        create_init_files(iter_dir)

        semantic_repairs: list[dict[str, Any]] = []
        if feedback_path and feedback_path.is_file():
            semantic_repairs = apply_semantic_feedback_repairs(
                context=context,
                feedback_text=feedback_path.read_text(encoding="utf-8"),
                model=model,
                max_repairs=max(1, max_ruff_repairs),
                allow_llm=allow_llm,
            )

        _log(f"[outer {outer}] Level-1 ruff/contract repair")
        level1 = level1_repair_loop(
            context=context,
            model=model,
            max_ruff_repairs=max_ruff_repairs,
            allow_llm=allow_llm,
            log=_log,
        )
        if not level1.get("ok"):
            iter_report = {
                "outer": outer,
                "ok": False,
                "stage_failed": "level1",
                "level1": level1,
                "semantic_repairs": semantic_repairs,
            }
            iterations.append(iter_report)
            _write_json(iter_dir / "iter_report.json", iter_report)
            feedback_path = iter_dir / "semantic_feedback.md"
            feedback_path.write_text(
                "# Level-1 failures blocked semantic stage\n\n"
                + "\n".join(
                    f"- {f}"
                    for f in (level1.get("validation") or {}).get("failures") or []
                )
                + "\n",
                encoding="utf-8",
            )
            continue

        poisoned: list[str] = []
        if exercise_semantic and outer == 0:
            poisoned = exercise_semantic_fail(scripts_dir)
            _log(f"[outer {outer}] exercise-semantic-fail mutated: {poisoned}")

        fixture_dest = iter_dir / "fixture.json"
        if fixture_path is not None:
            fixture = _load_fixture(fixture_path)
            _write_json(fixture_dest, fixture)
            fixture_source = "file"
        elif allow_llm:
            _log(f"[outer {outer}] generating mock fixture via LLM")
            fixture = generate_mock_fixture(
                context=context, model=model, dest=fixture_dest
            )
            fixture_source = "llm"
        else:
            raise ValueError("No --fixture provided and LLM disabled (--no-llm)")

        abox_path = iter_dir / "abox.ttl"
        if abox_mode == "react":
            _log(f"[outer {outer}] ReAct extract+KG against mock doc → {abox_path}")
            abox_build = run_react_pipeline_against_mock(
                artifact_root=iter_dir,
                meta_task_config=meta_task_config,
                fixture=fixture,
                abox_path=abox_path,
                runtime_root=iter_dir / "runtime",
            )
        else:
            _log(f"[outer {outer}] MCP harness materialize → {abox_path}")
            abox_build = run_mcp_harness(
                scripts_dir=scripts_dir,
                fixture=fixture,
                abox_path=abox_path,
            )

        reasoner_report: dict[str, Any] | None = None
        if abox_build.get("ok"):
            _log(f"[outer {outer}] HermiT reasoner gate")
            reasoner_report = run_reasoner_gate(
                tbox_path=tbox_path,
                abox_path=abox_path,
                report_path=iter_dir / "reasoner_report.json",
            )
        _write_json(iter_dir / "abox_build_report.json", abox_build)

        coverage = list(fixture.get("coverage") or REQUIRED_COVERAGE)
        semantic_ok = bool(abox_build.get("ok")) and bool(
            reasoner_report and reasoner_report.get("ok")
        )
        feedback_text = package_semantic_feedback(
            abox_build=abox_build,
            reasoner=reasoner_report,
            coverage=coverage,
        )
        feedback_path = iter_dir / "semantic_feedback.md"
        feedback_path.write_text(feedback_text, encoding="utf-8")

        iter_report = {
            "outer": outer,
            "ok": semantic_ok,
            "abox_mode": abox_mode,
            "fixture_source": fixture_source,
            "fixture_path": str(fixture_dest),
            "level1": {
                "ok": level1.get("ok"),
                "validation_failures": (level1.get("validation") or {}).get("failures"),
            },
            "semantic_repairs": semantic_repairs,
            "exercise_semantic_fail": poisoned,
            "abox_build": abox_build,
            "reasoner": {
                "ok": None if reasoner_report is None else reasoner_report.get("ok"),
                "owlrl_ok": None
                if reasoner_report is None
                else reasoner_report.get("owlrl_ok"),
                "failures": None
                if reasoner_report is None
                else reasoner_report.get("failures"),
                "hermit_hard_fail": None
                if reasoner_report is None
                else reasoner_report.get("hermit_hard_fail"),
                "report_path": str(iter_dir / "reasoner_report.json")
                if reasoner_report is not None
                else None,
            },
            "feedback_path": str(feedback_path),
        }
        iterations.append(iter_report)
        _write_json(iter_dir / "iter_report.json", iter_report)

        if semantic_ok:
            overall_ok = True
            _log(f"[outer {outer}] PASS")
            break
        _log(f"[outer {outer}] FAIL — feedback ready for next regenerate")

    summary = {
        "ok": overall_ok,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "abox_mode": abox_mode,
        "hermit_required": True,
        "outer_iterations": len(iterations),
        "max_outer": max_outer,
        "meta_task_config": str(meta_task_config),
        "tbox": str(tbox_path),
        "iterations": iterations,
    }
    _write_json(run_dir / "summary.json", summary)
    _log(f"[done] ok={overall_ok} summary={run_dir / 'summary.json'}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Medical semantic MCP closed loop "
            "(regenerate → ruff → ReAct extract/KG on mock doc → HermiT reasoner)."
        )
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Sandbox root for run directories.",
    )
    parser.add_argument(
        "--meta-task-config",
        default=str(DEFAULT_META_TASK),
        help="Medical meta-task config (v4 one-iter by default).",
    )
    parser.add_argument(
        "--tbox",
        default=str(DEFAULT_TBOX),
        help="Medical T-Box TTL for reasoner checks.",
    )
    parser.add_argument("--max-outer", type=int, default=2, help="Outer semantic regenerate budget.")
    parser.add_argument(
        "--max-ruff-repairs",
        type=int,
        default=2,
        help="Level-1 LLM repair attempts per file/round.",
    )
    parser.add_argument("--model", default="gpt-5.2", help="LLM model for fixture/repairs.")
    parser.add_argument(
        "--fixture",
        help="Path to canned fixture JSON (skips mock-document LLM).",
    )
    parser.add_argument(
        "--abox-mode",
        choices=["react", "harness"],
        default="react",
        help=(
            "How to build the A-Box from the mock document. "
            "react (default): full top/main extract + ReAct KG building. "
            "harness: in-process materialize_hints only (offline-friendly)."
        ),
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Disable LLM for fixture generation and Level-1/semantic script repairs "
            "(requires --fixture). ReAct mode still needs pipeline LLM credentials."
        ),
    )
    parser.add_argument(
        "--exercise-semantic-fail",
        action="store_true",
        help="On outer=0 after Level-1, poison a property so reasoner/HermiT fails.",
    )
    parser.add_argument("--json", action="store_true", help="Print summary JSON to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    allow_llm = not args.no_llm
    fixture_path = Path(args.fixture) if args.fixture else None
    if not allow_llm and fixture_path is None:
        print("error: --no-llm requires --fixture", file=sys.stderr)
        return 2
    summary = run_outer_loop(
        output_root=Path(args.output_root),
        meta_task_config=Path(args.meta_task_config),
        tbox_path=Path(args.tbox),
        max_outer=max(1, args.max_outer),
        max_ruff_repairs=max(0, args.max_ruff_repairs),
        model=args.model,
        fixture_path=fixture_path,
        allow_llm=allow_llm,
        exercise_semantic=bool(args.exercise_semantic_fail),
        abox_mode=str(args.abox_mode),
    )
    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
