from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    DEFAULT_AGENTIC_OUTPUT_ROOT,
    build_agentic_generation_context,
    resolve_default_config_for_ontology,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    run_agentic_generation_experiment,
    write_agentic_mcp_main_py,
)
from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.generation_checkpoint import (
    copy_generation_checkpoint,
    replay_generation_checkpoint,
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


def _selected_ontologies(args: argparse.Namespace) -> list[str]:
    if args.extensions:
        return ["ontomops", "ontospecies"]
    if args.both:
        return ["medical", "ontosynthesis"]
    if args.ontology:
        return [args.ontology]
    return ["medical", "ontosynthesis"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run isolated agentic prompt/script generation experiments."
    )
    parser.add_argument(
        "--ontology",
        help=(
            "Ontology name to process. With --meta-task-config, the name is "
            "resolved from that config instead of a hard-coded domain list."
        ),
    )
    parser.add_argument("--both", action="store_true", help="Process medical and ontosynthesis")
    parser.add_argument("--extensions", action="store_true", help="Process extension ontologies")
    parser.add_argument(
        "--meta-task-config",
        help="Override meta-task config path. Usually omit when using --both.",
    )
    parser.add_argument(
        "--domain-config",
        help=(
            "Use the two-input generation architecture: active T-Box bundle plus "
            "one domain runtime config. Semantic planning is performed with gpt-5."
        ),
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_AGENTIC_OUTPUT_ROOT),
        help="Isolated output root for agentic generation artifacts.",
    )
    parser.add_argument(
        "--stage",
        choices=["context", "scripts", "prompts", "validate", "all"],
        default="context",
        help="Experiment stage to run. The first MVP implements context, scripts, and validate.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build context and validation reports without generating scripts.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON.",
    )
    parser.add_argument(
        "--repair-loop",
        action="store_true",
        help="Run validation feedback and targeted repair iterations after generation.",
    )
    parser.add_argument(
        "--exercise-repair",
        action="store_true",
        help="Seed isolated draft defects before validation so the repair loop can be observed.",
    )
    parser.add_argument(
        "--max-repair-iterations",
        type=int,
        default=3,
        help="Maximum validation/repair iterations.",
    )
    parser.add_argument(
        "--llm-generation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable plain LLM unified-diff generation (CLI default: enabled).",
    )
    parser.add_argument(
        "--generation-model",
        default="gpt-5",
        help="Model for LLM-driven script/prompt/validation generation agents.",
    )
    parser.add_argument(
        "--edit-backend",
        choices=["exact_edits", "unified_diff"],
        default="exact_edits",
        help="LLM artifact editing protocol (default: exact_edits).",
    )
    parser.add_argument(
        "--max-agent-rounds",
        type=int,
        default=2,
        help="Maximum LLM agent validation/repair rounds.",
    )
    parser.add_argument(
        "--focused-repair",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep each repair round bounded to one LLM-selected observation focus.",
    )
    parser.add_argument(
        "--incremental-generation-repair",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate artifacts in LLM-planned dependency order and validate each stage.",
    )
    parser.add_argument(
        "--max-focus-targets",
        type=int,
        default=3,
        help="Maximum editable files in one focused repair dependency step.",
    )
    parser.add_argument(
        "--repair-only",
        action="store_true",
        help="Use existing non-empty artifacts as an immutable generation checkpoint and run only LLM repair.",
    )
    parser.add_argument(
        "--generation-only",
        action="store_true",
        help="Generate and validate an immutable checkpoint without starting repair.",
    )
    parser.add_argument(
        "--package-synthesis",
        action="store_true",
        help="Deprecated alias for --focused-package-integration.",
    )
    parser.add_argument(
        "--focused-package-integration",
        action="store_true",
        help="Run bounded multi-round package integration with full validation.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        help="Copy a completed generation checkpoint into a fresh output root.",
    )
    parser.add_argument(
        "--max-integration-rounds",
        type=int,
        default=24,
        help="Maximum focused package integration rounds.",
    )
    parser.add_argument(
        "--max-integration-targets",
        type=int,
        default=3,
        help="Maximum editable files in one package integration round (hard maximum 3).",
    )
    parser.add_argument(
        "--runtime-adapter-synthesis",
        action="store_true",
        help="Repair only main.py as the package runtime/materialization adapter.",
    )
    parser.add_argument(
        "--creation-foundation-synthesis",
        action="store_true",
        help="Repair RDF creation modules before synthesizing the main runtime adapter.",
    )
    parser.add_argument(
        "--creation-foundation-module",
        help="Limit creation-foundation synthesis to one exact generated module filename.",
    )
    parser.add_argument(
        "--checkpoint-summary",
        help="Replay audited initial-generation diffs from this summary before repair-only.",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="Regenerate only MCP main.py (requires existing deterministic *_creation_*.py peers).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ontology_names = _selected_ontologies(args)
    if args.domain_config and args.meta_task_config:
        raise SystemExit("--domain-config and --meta-task-config are mutually exclusive")
    if args.domain_config and len(ontology_names) != 1:
        raise SystemExit("--domain-config requires exactly one --ontology")
    if args.domain_config and args.generation_model != "gpt-5":
        raise SystemExit("--domain-config requires --generation-model gpt-5")
    focused_integration = bool(
        args.focused_package_integration or args.package_synthesis
    )
    if focused_integration:
        if len(ontology_names) != 1:
            raise SystemExit(
                "--focused-package-integration requires exactly one ontology"
            )
        if not 1 <= args.max_integration_targets <= 3:
            raise SystemExit("--max-integration-targets must be between 1 and 3")
        conflicts = [
            name
            for name, enabled in {
                "--generation-only": args.generation_only,
                "--runtime-adapter-synthesis": args.runtime_adapter_synthesis,
                "--creation-foundation-synthesis": args.creation_foundation_synthesis,
                "--main-only": args.main_only,
            }.items()
            if enabled
        ]
        if conflicts:
            raise SystemExit(
                "--focused-package-integration conflicts with " + ", ".join(conflicts)
            )
        args.repair_only = True
        args.incremental_generation_repair = False
        args.focused_repair = True
        args.max_agent_rounds = args.max_integration_rounds
        args.max_focus_targets = args.max_integration_targets
    if args.checkpoint_dir:
        if not focused_integration:
            raise SystemExit(
                "--checkpoint-dir requires --focused-package-integration"
            )
        checkpoint = copy_generation_checkpoint(
            checkpoint_root=Path(args.checkpoint_dir),
            output_root=Path(args.output_root),
            ontology_name=ontology_names[0],
        )
        _print_utf8(json.dumps(checkpoint, ensure_ascii=False))
    if args.repair_only and args.generation_only:
        raise SystemExit("--repair-only and --generation-only are mutually exclusive")
    if args.checkpoint_summary:
        if not args.repair_only:
            raise SystemExit("--checkpoint-summary requires --repair-only")
        checkpoint = replay_generation_checkpoint(
            summary_path=Path(args.checkpoint_summary),
            output_root=Path(args.output_root),
            include_package_synthesis=not focused_integration,
        )
        _print_utf8(json.dumps(checkpoint, ensure_ascii=False))

    if args.main_only:
        if len(ontology_names) != 1:
            raise SystemExit("--main-only requires exactly one ontology (use --ontology …)")
        name = ontology_names[0]
        mt_path = args.meta_task_config or str(resolve_default_config_for_ontology(name))
        ctx = (
            build_domain_generation_context(
                domain_config_path=args.domain_config,
                output_root=args.output_root,
                repository_root=Path.cwd(),
                write_files=True,
            )
            if args.domain_config
            else build_agentic_generation_context(
                ontology_name=name,
                meta_task_config_path=mt_path,
                output_root=args.output_root,
                write_files=True,
            )
        )
        written_main = write_agentic_mcp_main_py(ctx)
        summary = {
            "ok": True,
            "output_root": args.output_root,
            "main_only": True,
            "ontology": name,
            "written": [written_main],
        }
        if args.json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(f"[main-only] Wrote {written_main}")
        return 0

    if args.meta_task_config and len(ontology_names) > 1 and not args.extensions:
        raise SystemExit("--meta-task-config can only be used with one --ontology unless --extensions is used")

    generate_scripts = args.stage in {"scripts", "all"} and not args.dry_run
    generate_prompts = args.stage in {"prompts", "all"} and not args.dry_run
    if args.stage == "context" or args.dry_run:
        contexts = (
            [
                build_domain_generation_context(
                    domain_config_path=args.domain_config,
                    output_root=args.output_root,
                    repository_root=Path.cwd(),
                    write_files=True,
                )
            ]
            if args.domain_config
            else [
                build_agentic_generation_context(
                    ontology_name=name,
                    meta_task_config_path=args.meta_task_config,
                    output_root=args.output_root,
                    write_files=True,
                )
                for name in ontology_names
            ]
        )
        summary = {
            "ok": True,
            "output_root": args.output_root,
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
                for ctx in contexts
            ],
        }
    else:
        summary = run_agentic_generation_experiment(
            ontology_names,
            meta_task_config_path=args.meta_task_config,
            domain_config_path=args.domain_config,
            output_root=args.output_root,
            generate_scripts=generate_scripts,
            generate_prompts=generate_prompts,
            repair_loop=args.repair_loop,
            exercise_repair=args.exercise_repair,
            max_repair_iterations=args.max_repair_iterations,
            llm_agent_generation=args.llm_generation,
            generation_model=args.generation_model,
            edit_backend=args.edit_backend,
            max_agent_rounds=args.max_agent_rounds,
            repair_only=args.repair_only,
            generation_only=args.generation_only,
            package_synthesis=args.package_synthesis,
            runtime_adapter_synthesis=args.runtime_adapter_synthesis,
            creation_foundation_synthesis=args.creation_foundation_synthesis,
            creation_foundation_module=args.creation_foundation_module,
            focused_repair=args.focused_repair,
            incremental_generation_repair=args.incremental_generation_repair,
            max_focus_targets=args.max_focus_targets,
            focused_package_integration=focused_integration,
            write_context_files=args.stage != "validate",
        )

    if args.json:
        _print_utf8(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Agentic generation stage completed. Output root: {Path(args.output_root)}")
        if "reports" in summary:
            for report in summary["reports"]:
                status = "ok" if report.get("ok") else "needs_revision"
                repairs = len(report.get("repair_history") or [])
                print(f"- {report.get('ontology')}: {status} ({len(report.get('failures') or [])} failures, {repairs} validation pass(es))")
        else:
            for item in summary.get("contexts", []):
                print(f"- {item['ontology']}: {item['class_count']} classes, {item['property_count']} properties")
    return 0


if __name__ == "__main__":
    sys.exit(main())
