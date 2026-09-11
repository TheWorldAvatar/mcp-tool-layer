"""Compile occurrence-surface MCP packages from T-Box + domain config.

Typical invocation from any working directory:

    python -m src.kg_building_mcp_generation
    python -m src.kg_building_mcp_generation ontosynthesis

Omitting the ontology name compiles the four standard domains in order.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from models.MCPConfig import resolve_mcp_set_path
from models.locations import resolve_under_repository
from src.extraction_prompt_generation.config.domain_config import (
    load_domain_generation_config,
)
from src.extraction_prompt_generation.paths import (
    available_ontologies,
    default_output_root,
    read_domain_ontology_name,
    repository_root,
    resolve_domain_config_path,
)
from src.kg_building_mcp_generation.compile_hook import (
    build_occurrence_generation_context,
)
from src.kg_building_mcp_generation.emit.launch import write_launch
from src.kg_building_mcp_generation.emit.prompts import (
    generate_deterministic_prompt_slice,
)
from src.kg_building_mcp_generation.emit.runtime import write_runtime_support
from src.kg_building_mcp_generation.emit.scripts import (
    generate_deterministic_script_slice,
)

FOUR_DOMAINS = ("ontosynthesis", "ontomops", "ontospecies", "medical")


def _print_utf8(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        stream = getattr(sys.stdout, "buffer", None)
        if stream is None:
            print(text.encode("ascii", errors="backslashreplace").decode("ascii"))
            return
        stream.write(text.encode("utf-8", errors="replace") + b"\n")
        stream.flush()


def _known_ontologies_help() -> str:
    names = available_ontologies()
    return ", ".join(names) if names else "(no configs/domains/*.json found)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile occurrence-surface MCP packages from T-Box + domain config. "
            "Omit the ontology name to generate the four standard domains."
        )
    )
    parser.add_argument(
        "ontology",
        nargs="?",
        help=(
            "Ontology name. Looks up configs/domains/<name>.json. "
            f"Known: {_known_ontologies_help()}. "
            "Omit to run ontosynthesis, ontomops, ontospecies, medical."
        ),
    )
    parser.add_argument(
        "--domain-config",
        help="Override domain config path. Ontology is read from the file.",
    )
    parser.add_argument(
        "--output-root",
        help="Generated package root (default: <repository>/generated).",
    )
    parser.add_argument(
        "--stage",
        choices=["context", "scripts", "prompts", "all"],
        default="all",
        help=(
            "context compiles planning / occurrence units. "
            "scripts rewrites MCP tools. "
            "prompts rewrites deterministic prompts and SPARQL. "
            "Default: all."
        ),
    )
    parser.add_argument(
        "--selected-top-entity",
        help=(
            "Optional JSON file with a top_entity object, or a decisions file "
            "containing top_entity. When set, GPT-5 root planning is skipped."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON.",
    )
    return parser


def _load_selected_top_entity(raw_path: str | None) -> dict[str, Any] | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_file():
        raise SystemExit(f"selected top-entity file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("selected top-entity file must be a JSON object")
    if str(payload.get("class_local") or "").strip():
        return payload
    nested = payload.get("top_entity")
    if isinstance(nested, dict) and str(nested.get("class_local") or "").strip():
        return nested
    raise SystemExit(
        "selected top-entity file must contain class_local, or a top_entity object"
    )


def _resolve_output_root(raw: str | None) -> Path:
    if str(raw or "").strip():
        return resolve_under_repository(raw)
    return default_output_root()


def precheck_domain(
    *,
    ontology: str,
    domain_config: Path,
    output_root: Path,
    repository_root: Path,
    require_upstream: bool,
) -> None:
    """Fail closed when domain config, T-Box, MCP sets, or parent context are missing."""
    if not domain_config.is_file():
        raise FileNotFoundError(f"domain config not found: {domain_config}")
    domain = load_domain_generation_config(
        domain_config, repository_root=repository_root
    )
    if domain.ontology_name != ontology:
        raise ValueError(
            "domain config ontology does not match the given name: "
            f"{domain.ontology_name!r} != {ontology!r}"
        )
    missing_sets: list[str] = []
    for group in domain.mcp_capabilities.values():
        if not isinstance(group, dict):
            continue
        set_name = str(group.get("set_name") or "").strip()
        if not set_name:
            continue
        try:
            resolve_mcp_set_path(set_name)
        except FileNotFoundError:
            missing_sets.append(set_name)
    if missing_sets:
        raise FileNotFoundError(
            "MCP set files not found: " + ", ".join(sorted(set(missing_sets)))
        )
    if domain.role == "extension" and require_upstream:
        binding = domain.runtime.get("binding") or {}
        upstream = str(binding.get("upstream_ontology") or "").strip()
        parent_contract = (
            output_root / "ontology_structures" / upstream / "generation_contract.json"
        )
        parent_plan = (
            output_root / "semantic_planning" / upstream / "accepted_semantic_plan.json"
        )
        if not parent_contract.is_file() and not parent_plan.is_file():
            raise FileNotFoundError(
                f"{ontology} is an extension and needs {upstream} compiled first "
                f"in the same --output-root ({output_root})"
            )


def _summarize(context: Any) -> dict[str, Any]:
    units = context.contract.get("occurrence_surface_units") or {}
    decisions = context.contract.get("occurrence_surface_decisions") or {}
    top = context.contract.get("top_entity") or {}
    focus = context.contract.get("extension_focus") or {}
    return {
        "ok": not bool(units.get("errors")),
        "ontology": context.ontology.name,
        "top_entity": top.get("class_local"),
        "top_entity_source": top.get("source"),
        "extension_focus": focus.get("class_local"),
        "tools": len(units.get("public_tools") or []),
        "linkers": len(units.get("public_linkers") or []),
        "llm_judged": decisions.get("llm_judged_count"),
        "errors": list(units.get("errors") or []),
    }


def generate_one(
    *,
    ontology: str,
    domain_config: Path,
    output_root: Path,
    repo: Path,
    stage: str,
    selected_top_entity: dict[str, Any] | None,
    require_upstream: bool,
) -> dict[str, Any]:
    precheck_domain(
        ontology=ontology,
        domain_config=domain_config,
        output_root=output_root,
        repository_root=repo,
        require_upstream=require_upstream,
    )
    print(f"GENERATE_START ontology={ontology}", flush=True)
    context = build_occurrence_generation_context(
        domain_config_path=domain_config,
        output_root=output_root,
        repository_root=repo,
        write_files=True,
        selected_top_entity=selected_top_entity,
    )
    if context.ontology.name != ontology:
        raise ValueError(
            "domain config ontology does not match: "
            f"{context.ontology.name!r} != {ontology!r}"
        )
    written: list[str] = []
    if stage in {"scripts", "all"}:
        written.extend(generate_deterministic_script_slice(context))
        written.extend(write_runtime_support(context))
        written.append(write_launch(output_root, context.ontology.name))
    if stage in {"prompts", "all"}:
        written.extend(generate_deterministic_prompt_slice(context))
        if stage == "prompts":
            written.extend(write_runtime_support(context))
    summary = _summarize(context)
    summary["stage"] = stage
    summary["output_root"] = str(output_root)
    summary["files"] = len(written)
    summary["written"] = written
    print(
        "GENERATE_DONE "
        f"ontology={summary['ontology']} "
        f"top={summary['top_entity']} "
        f"focus={summary['extension_focus']} "
        f"tools={summary['tools']} "
        f"linkers={summary['linkers']} "
        f"llm_judged={summary['llm_judged']} "
        f"errors={len(summary['errors'])} "
        f"files={summary['files']}",
        flush=True,
    )
    return summary


def _requested_ontologies(args: argparse.Namespace) -> list[tuple[str, Path]]:
    ontology = str(args.ontology or "").strip()
    domain_override = str(args.domain_config or "").strip()
    if ontology or domain_override:
        domain_config = resolve_domain_config_path(
            domain_override or None,
            ontology_name=ontology,
        )
        name = read_domain_ontology_name(domain_config)
        if ontology and name != ontology:
            raise SystemExit(
                "domain config ontology does not match the given name: "
                f"{name!r} != {ontology!r}"
            )
        return [(name, domain_config)]
    specs: list[tuple[str, Path]] = []
    for name in FOUR_DOMAINS:
        specs.append((name, resolve_domain_config_path(ontology_name=name)))
    return specs


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = repository_root()
    output_root = _resolve_output_root(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    selected_top_entity = _load_selected_top_entity(args.selected_top_entity)
    specs = _requested_ontologies(args)
    if selected_top_entity is not None and len(specs) > 1:
        raise SystemExit("--selected-top-entity can only be used with one ontology")
    reports: list[dict[str, Any]] = []
    failed = False
    for name, domain_config in specs:
        try:
            report = generate_one(
                ontology=name,
                domain_config=domain_config,
                output_root=output_root,
                repo=repo,
                stage=args.stage,
                selected_top_entity=selected_top_entity,
                require_upstream=True,
            )
            reports.append(report)
            if report.get("ok") is not True:
                failed = True
        except Exception as exc:
            failed = True
            reports.append(
                {
                    "ok": False,
                    "ontology": name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(
                f"GENERATE_FAIL ontology={name} error={type(exc).__name__}: {exc}",
                flush=True,
            )
    summary = {
        "ok": not failed and all(item.get("ok") is True for item in reports),
        "output_root": str(output_root),
        "repository_root": str(repo),
        "stage": args.stage,
        "domains": reports,
    }
    if len(specs) > 1:
        report_path = output_root / "generation_from_human_domain.json"
        report_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        summary["report_path"] = str(report_path)
    if args.json:
        _print_utf8(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Generation finished. Repository: {repo}")
        print(f"Output root: {output_root}")
        for item in reports:
            status = "ok" if item.get("ok") else "failed"
            extra = item.get("error") or (
                f"tools={item.get('tools')} linkers={item.get('linkers')}"
            )
            print(f"- {item.get('ontology')}: {status} ({extra})")
    return 0 if summary.get("ok") is True else 2


if __name__ == "__main__":
    sys.exit(main())
