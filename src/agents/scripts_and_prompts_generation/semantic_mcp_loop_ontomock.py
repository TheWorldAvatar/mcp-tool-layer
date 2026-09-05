#!/usr/bin/env python3
"""Offline OntoMock semantic harness over the generated capability package."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _import_generated_main_module,
)
from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.semantic_loop_core import (
    load_semantic_loop_config,
)


ROOT = Path(__file__).resolve().parents[3]
DOMAIN_CONFIG = ROOT / "configs" / "domains" / "ontomock.json"
FIXTURE = ROOT / "tests" / "fixtures" / "ontomock_semantic_mock.json"
LOOP_CONFIG = load_semantic_loop_config(
    ROOT / "configs" / "semantic_loops" / "ontomock.json",
    repository_root=ROOT,
)
TBOX_PATHS = list(LOOP_CONFIG.tbox_paths)
OM2_DURATION = "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"


def _planner(model: str, prompt: str) -> dict[str, Any]:
    if model != "gpt-5" or "Select the single top entity class" not in prompt:
        raise ValueError("OntoMock offline planner only accepts top-entity selection")
    return {
        "class_local": "ProcessRun",
        "rationale": "ProcessRun is the complete source-described process root.",
        "evidence": ["ProcessRun", "hasAction", "hasInput"],
    }


def _load_reasoner_module() -> Any:
    path = ROOT / "scripts" / "validate_abox_with_reasoner.py"
    spec = importlib.util.spec_from_file_location(
        "validate_abox_with_reasoner_ontomock", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load reasoner module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_tool_result(raw: Any, *, tool: str) -> dict[str, Any]:
    try:
        result = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{tool} returned non-JSON output") from exc
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise ValueError(f"{tool} failed: {result}")
    return result


def _tool(module: Any, name: str) -> Callable[..., Any]:
    candidate = getattr(module, name, None)
    if not callable(candidate):
        candidate = getattr(module, f"_{name}", None)
    if not callable(candidate):
        raise AttributeError(f"Generated package does not expose {name}")
    return candidate


def build_generated_package(output_root: Path) -> tuple[Any, Path]:
    """Compile the tracked T-Box bundle into a deterministic generated package."""
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=output_root,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generate_deterministic_script_slice(context)
    return context, Path(context.scripts_dir)


def materialize_fixture(
    *,
    scripts_dir: Path,
    fixture: dict[str, Any],
    abox_path: Path,
) -> dict[str, Any]:
    """Materialize one fixture solely through generated package capabilities."""
    run = fixture.get("process_run") or {}
    if not isinstance(run, dict):
        raise ValueError("fixture.process_run must be an object")
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    with tempfile.TemporaryDirectory(prefix="ontomock_harness_") as data_dir:
        os.environ["TWA_AGENTIC_DATA_DIR"] = data_dir
        try:
            module = _import_generated_main_module(scripts_dir, "ontomock")
            doi = "ontomock-fixture"
            scope = str(run.get("label") or "PR-001")
            _parse_tool_result(
                _tool(module, "init_memory")(doi, scope),
                tool="init_memory",
            )

            def create(name: str, *args: Any, **kwargs: Any) -> str:
                result = _parse_tool_result(
                    _tool(module, f"create_{name}")(*args, **kwargs),
                    tool=f"create_{name}",
                )
                iri = str(result.get("iri") or "")
                if not iri:
                    raise ValueError(f"create_{name} returned no IRI")
                return iri

            def link(name: str, subject: str, obj: str) -> None:
                _parse_tool_result(
                    _tool(module, f"add_{name}")(subject, obj),
                    tool=f"add_{name}",
                )

            top = create("ProcessRun", scope)
            foundation = run["foundation_input"]
            foundation_iri = create(
                "Input",
                str(foundation["label"]),
                hasAlias=str(foundation["alias"]),
            )
            output = run["output"]
            output_iri = create(
                "Output",
                str(output["label"]),
                hasTitle=str(output["title"]),
            )
            source_iri = create("SourceDoc", str(run["source_doc"]["label"]))
            vendor_iri = create("Vendor", str(run["vendor"]["label"]))
            input_iris = {
                str(foundation["label"]): foundation_iri,
                **{
                    str(item["label"]): create("Input", str(item["label"]))
                    for item in run.get("action_inputs") or []
                },
            }
            tool_iri = create("Tool", str(run["tool"]["label"]))
            metric_iri = create(
                "ExternalMetric", str(run["external_metric"]["label"])
            )

            link("hasInput", top, foundation_iri)
            link("hasOutput", top, output_iri)
            link("retrievedFrom", top, source_iri)
            link("suppliedBy", foundation_iri, vendor_iri)
            link("hasMetric", top, metric_iri)

            action_iris: list[str] = []
            duration_iris: list[str] = []
            for action in run.get("actions") or []:
                class_local = str(action["class_local"])
                kwargs: dict[str, Any] = {"hasOrder": int(action["order"])}
                if class_local == "DoStep":
                    kwargs["isEnabled"] = bool(action["is_enabled"])
                action_iri = create(class_local, str(action["label"]), **kwargs)
                action_iris.append(action_iri)
                link("hasAction", top, action_iri)
                link(
                    "usesInput",
                    action_iri,
                    input_iris[str(action["uses_input"])],
                )
                link("usesTool", action_iri, tool_iri)
                duration = action.get("duration")
                if isinstance(duration, dict):
                    duration_iri = create(
                        "om2_quantity",
                        str(duration.get("class_iri") or OM2_DURATION),
                        str(duration["label"]),
                    )
                    duration_iris.append(duration_iri)
                    link("hasDuration", action_iri, duration_iri)

            exported = _parse_tool_result(
                _tool(module, "export_memory")(doi, scope),
                tool="export_memory",
            )
            ttl = str(exported.get("ttl") or "")
            if not ttl.strip():
                raise ValueError("export_memory returned no Turtle")
            abox_path.parent.mkdir(parents=True, exist_ok=True)
            abox_path.write_text(ttl, encoding="utf-8")
            return {
                "ok": True,
                "mode": "harness",
                "abox_path": str(abox_path),
                "triple_count": exported.get("triple_count"),
                "iris": {
                    "process_run": top,
                    "foundation_input": foundation_iri,
                    "inputs": input_iris,
                    "output": output_iri,
                    "source_doc": source_iri,
                    "vendor": vendor_iri,
                    "tool": tool_iri,
                    "external_metric": metric_iri,
                    "actions": action_iris,
                    "durations": duration_iris,
                },
            }
        finally:
            if previous_data_dir is None:
                os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
            else:
                os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir


def run_reasoner_gate(
    *,
    tbox_paths: list[Path],
    abox_path: Path,
) -> dict[str, Any]:
    """Run deterministic OWL-RL/domain/range checks over the complete bundle."""
    reasoner = _load_reasoner_module()
    return reasoner.validate(tbox_paths, [abox_path], run_hermit=False)


def run_harness(
    *,
    output_root: Path,
    fixture_path: Path = FIXTURE,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    context, scripts_dir = build_generated_package(output_root)
    abox_path = output_root / "abox.ttl"
    abox = materialize_fixture(
        scripts_dir=scripts_dir,
        fixture=fixture,
        abox_path=abox_path,
    )
    reasoner = run_reasoner_gate(tbox_paths=TBOX_PATHS, abox_path=abox_path)
    summary = {
        "ok": bool(abox.get("ok")) and bool(reasoner.get("ok")),
        "abox_mode": "harness",
        "fixture": str(fixture_path),
        "tbox_paths": [str(path) for path in TBOX_PATHS],
        "tbox_bundle": context.contract.get("tbox_bundle"),
        "abox": abox,
        "reasoner": reasoner,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=str(LOOP_CONFIG.output_root))
    parser.add_argument("--fixture", default=str(FIXTURE))
    parser.add_argument(
        "--abox-mode",
        choices=["harness", "react"],
        default="harness",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Accepted for parity; the OntoMock harness never invokes an LLM.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.abox_mode == "react":
        print(
            "error: OntoMock react mode is reserved for the generic runtime adapter; "
            "use --abox-mode harness for the offline oracle path",
            file=sys.stderr,
        )
        return 2
    summary = run_harness(
        output_root=Path(args.output_root),
        fixture_path=Path(args.fixture),
    )
    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
