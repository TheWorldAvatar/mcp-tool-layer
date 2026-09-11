"""Generate extraction prompts from T-Box + domain config.

The configured model writes full (thick) extraction prompts with the
exact-edits backend. Each campaign writes a separate package under
`generated/runs/<YYYYMMDD_HHMMSS>_<tag>/`.

Typical invocation from any working directory:

    python -m src.extraction_prompt_generation ontosynthesis --tag gpt5_r1

Package map: README.md in this folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from models.generated_layout import update_batch_manifest
from src.extraction_prompt_generation.compile.artifact_compiler import (
    build_domain_generation_context,
)
from src.extraction_prompt_generation.paths import (
    available_ontologies,
    read_domain_ontology_name,
    repository_root,
    resolve_domain_config_path,
    resolve_generation_output_root,
)
from src.extraction_prompt_generation.pipeline import (
    run_agentic_generation_experiment,
)
from models.locked_llm import LOCKED_GENERATION_MODEL, LOCKED_LLM_SEED

GENERATION_MODEL = LOCKED_GENERATION_MODEL
CAMPAIGN_ONTOLOGY_ORDER = (
    "ontosynthesis",
    "ontomops",
    "ontospecies",
    "medical",
)


def _print_utf8(text: str) -> None:
    """Print Unicode reports safely on legacy Windows console encodings."""
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


def campaign_ontology_order() -> list[str]:
    known = set(available_ontologies())
    ordered = [name for name in CAMPAIGN_ONTOLOGY_ORDER if name in known]
    extras = [name for name in available_ontologies() if name not in set(ordered)]
    return ordered + extras


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate extraction prompts from one domain config and its T-Box "
            "bundle using thick-prompt generation. Writes one batch under "
            "generated/runs/<timestamp>_<tag>/."
        )
    )
    parser.add_argument(
        "ontology",
        nargs="?",
        help=(
            "Ontology name. Looks up configs/domains/<name>.json under the "
            f"repository root. Known: {_known_ontologies_help()}."
        ),
    )
    parser.add_argument(
        "--domain-config",
        help=(
            "Override domain config path. Optional when the ontology name is "
            "given. Ontology is read from the file."
        ),
    )
    parser.add_argument(
        "--output-root",
        help=(
            "Exact package root to write. Skips minting. Use this to continue "
            "a known batch. Default is generated/runs/<timestamp>_<tag>/."
        ),
    )
    parser.add_argument(
        "--tag",
        help=(
            "Batch suffix. Reuses the unique existing run with this tag, or "
            "mints generated/runs/<timestamp>_<tag>/."
        ),
    )
    parser.add_argument(
        "--generation-run",
        help="Existing run id, tag, path, or 'current' / 'latest'.",
    )
    parser.add_argument(
        "--use-current",
        action="store_true",
        help="Write into the package pointed at by generated/current.json.",
    )
    parser.add_argument(
        "--no-current",
        action="store_true",
        help="Do not update generated/current.json (use when running batches in parallel).",
    )
    parser.add_argument(
        "--model",
        help=(
            "Ignored. Planning, reuse, authoring, and repair are welded to "
            f"{LOCKED_GENERATION_MODEL}."
        ),
    )
    parser.add_argument(
        "--all-domains",
        action="store_true",
        help=(
            "Generate every domain in this batch: ontosynthesis first, then "
            "extensions, then medical."
        ),
    )
    parser.add_argument(
        "--stage",
        choices=["reuse", "context", "prompts", "all"],
        default="all",
        help=(
            "reuse writes only the class-reuse judgment after planning. "
            "context writes planning / contracts / reuse. "
            "all and prompts also run extraction prompt generation. Default: all."
        ),
    )
    parser.add_argument(
        "--selected-top-entity",
        help=(
            "Optional JSON file with a top_entity object, or a decisions file "
            "containing top_entity. When set, root planning is skipped."
        ),
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_artifacts",
        metavar="NAME",
        help=(
            "Author only this prompt file (repeatable), e.g. EXTRACTION_ITER_1.md. "
            "Reuses the compiled plan in --output-root when present."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        dest="max_generation_workers",
        help="Maximum concurrent model artifact generation calls (default: 5).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON.",
    )
    return parser


def _resolve_ontology_and_config(
    ontology: str,
    domain_override: str,
) -> tuple[str, Path]:
    name = str(ontology or "").strip()
    override = str(domain_override or "").strip()
    if not name and not override:
        known = _known_ontologies_help()
        raise SystemExit(
            "pass an ontology name, e.g. "
            "python -m src.extraction_prompt_generation ontosynthesis. "
            f"Known: {known}"
        )
    domain_config = resolve_domain_config_path(
        override or None,
        ontology_name=name,
    )
    config_ontology = read_domain_ontology_name(domain_config)
    if name and config_ontology != name:
        raise SystemExit(
            "domain config ontology does not match the given name: "
            f"{config_ontology!r} != {name!r}"
        )
    return config_ontology, domain_config


def _resolve_output_root(args: argparse.Namespace, *, model: str) -> Path:
    try:
        return resolve_generation_output_root(
            output_root=str(args.output_root or "").strip() or None,
            generation_run=str(args.generation_run or "").strip() or None,
            use_current=bool(args.use_current),
            tag=str(args.tag or "").strip(),
            model=model,
            settings={
                "stage": args.stage,
                "workers": args.max_generation_workers,
                "from_scratch": True,
            },
            set_current=not bool(args.no_current),
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def _load_selected_top_entity(raw_path: str | None) -> dict[str, Any] | None:
    """Load an optional planned root so reuse-only runs skip GPT-5 planning."""
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


def _reuse_summary(ctx: object, output_root: Path) -> dict[str, Any]:
    policy = dict((getattr(ctx, "contract") or {}).get("reuse_policy") or {})
    classes = [item for item in (policy.get("classes") or []) if isinstance(item, dict)]
    ontology = str(getattr(getattr(ctx, "ontology"), "name") or "")
    return {
        "ok": True,
        "stage": "reuse",
        "output_root": str(output_root),
        "reuse_policy_path": str(
            output_root / "derived_inputs" / ontology / "reuse_policy.json"
        ),
        "reuse_judgment_path": str(
            output_root / "derived_inputs" / ontology / "reuse_judgment.json"
        ),
        "inventory_count": len(classes),
        "reusable_count": sum(1 for item in classes if item.get("reusable") is True),
        "non_reusable_count": sum(
            1 for item in classes if item.get("reusable") is False
        ),
        "cached": bool(policy.get("_cached")),
    }


def _print_human_summary(repo: Path, output_root: Path, summary: dict[str, Any]) -> None:
    print(f"Generation finished. Repository: {repo}")
    print(f"Output root: {output_root}")
    if "reports" in summary:
        for report in summary["reports"]:
            status = "ok" if report.get("ok") else "needs_revision"
            print(
                f"- {report.get('ontology')}: {status} "
                f"({len(report.get('failures') or [])} failures)"
            )
    elif summary.get("stage") == "reuse":
        print(
            f"- reuse inventory={summary.get('inventory_count')} "
            f"reusable={summary.get('reusable_count')} "
            f"non_reusable={summary.get('non_reusable_count')}"
        )
        print(f"- policy: {summary.get('reuse_policy_path')}")
    else:
        for item in summary.get("contexts", []):
            print(
                f"- {item['ontology']}: {item['class_count']} classes, "
                f"{item['property_count']} properties"
            )


def _run_one_ontology(
    *,
    ontology: str,
    domain_config: Path,
    output_root: Path,
    args: argparse.Namespace,
    selected_top_entity: dict[str, Any] | None,
) -> dict[str, Any]:
    repo = repository_root()
    model_name = GENERATION_MODEL

    if args.stage in {"context", "reuse"}:
        ctx = build_domain_generation_context(
            domain_config_path=domain_config,
            output_root=output_root,
            repository_root=repo,
            write_files=True,
            selected_top_entity=selected_top_entity,
        )
        if ctx.ontology.name != ontology:
            raise SystemExit(
                "domain config ontology does not match: "
                f"{ctx.ontology.name!r} != {ontology!r}"
            )
        if args.stage == "reuse":
            summary = _reuse_summary(ctx, output_root)
            summary["repository_root"] = str(repo)
        else:
            summary = {
                "ok": True,
                "output_root": str(output_root),
                "repository_root": str(repo),
                "contexts": [
                    {
                        "ontology": ctx.ontology.name,
                        "ttl_file": ctx.ontology.ttl_file,
                        "scripts_dir": ctx.scripts_dir,
                        "prompts_dir": ctx.prompts_dir,
                        "contract_path": ctx.contract_path,
                        "class_count": len(ctx.parsed.get("classes") or {}),
                        "property_count": len(ctx.parsed.get("properties") or {}),
                    }
                ],
            }
    else:
        generate_prompts = args.stage in {"prompts", "all"}
        summary = run_agentic_generation_experiment(
            [ontology],
            domain_config_path=domain_config,
            output_root=output_root,
            generate_prompts=generate_prompts,
            llm_agent_generation=True,
            generation_model=model_name,
            edit_backend="exact_edits",
            parallel_generation=True,
            max_generation_workers=args.max_generation_workers,
            target_artifacts=list(args.target_artifacts or []) or None,
            write_context_files=True,
            operation_mode="legacy",
            selected_top_entity=selected_top_entity,
        )
        summary.setdefault("repository_root", str(repo))
    update_batch_manifest(
        output_root,
        tag=str(args.tag or ""),
        model=model_name,
        ontologies=[ontology],
        settings={"stage": args.stage, "workers": args.max_generation_workers},
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_generation_workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.all_domains and (args.ontology or args.domain_config):
        raise SystemExit("do not pass an ontology name together with --all-domains")

    os.environ["TWA_LLM_SEED"] = str(LOCKED_LLM_SEED)
    os.environ.pop("TWA_GENERATION_MODEL", None)
    generation_model = GENERATION_MODEL

    repo = repository_root()
    output_root = _resolve_output_root(args, model=generation_model)
    selected_top_entity = _load_selected_top_entity(args.selected_top_entity)

    print(f"Generation batch: {output_root.name}")
    print(f"Output root: {output_root}")
    print(f"Model: {generation_model}")

    if args.all_domains:
        names = campaign_ontology_order()
        if not names:
            raise SystemExit("no domain configs found under configs/domains/")
        summaries: list[dict[str, Any]] = []
        ok = True
        for name in names:
            print(f"\n=== {name} ===")
            ontology, domain_config = _resolve_ontology_and_config(name, "")
            summary = _run_one_ontology(
                ontology=ontology,
                domain_config=domain_config,
                output_root=output_root,
                args=args,
                selected_top_entity=None,
            )
            summaries.append(summary)
            if not args.json:
                _print_human_summary(repo, output_root, summary)
            if summary.get("ok") is not True:
                ok = False
                break
        combined = {
            "ok": ok,
            "output_root": str(output_root),
            "repository_root": str(repo),
            "model": generation_model,
            "ontologies": [item.get("ontology") or names[index] for index, item in enumerate(summaries)],
            "summaries": summaries,
        }
        if args.json:
            _print_utf8(json.dumps(combined, indent=2, ensure_ascii=False))
        return 0 if ok else 2

    ontology, domain_config = _resolve_ontology_and_config(
        str(args.ontology or ""),
        str(args.domain_config or ""),
    )
    summary = _run_one_ontology(
        ontology=ontology,
        domain_config=domain_config,
        output_root=output_root,
        args=args,
        selected_top_entity=selected_top_entity,
    )
    if args.json:
        _print_utf8(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_human_summary(repo, output_root, summary)
    return 0 if summary.get("ok") is True else 2


if __name__ == "__main__":
    sys.exit(main())
