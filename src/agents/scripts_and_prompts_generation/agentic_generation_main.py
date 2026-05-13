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
        choices=["medical", "ontosynthesis", "ontomops", "ontospecies"],
        help="Ontology to process",
    )
    parser.add_argument("--both", action="store_true", help="Process medical and ontosynthesis")
    parser.add_argument("--extensions", action="store_true", help="Process extension ontologies")
    parser.add_argument(
        "--meta-task-config",
        help="Override meta-task config path. Usually omit when using --both.",
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
        "--llm-agent-generation",
        action="store_true",
        help="Use LLM-driven Coding, Prompt, and Validation agents over deterministic scaffolds.",
    )
    parser.add_argument(
        "--generation-model",
        default="gpt-5.2",
        help="Model for LLM-driven script/prompt/validation generation agents.",
    )
    parser.add_argument(
        "--max-agent-rounds",
        type=int,
        default=2,
        help="Maximum LLM agent validation/repair rounds.",
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

    if args.main_only:
        if len(ontology_names) != 1:
            raise SystemExit("--main-only requires exactly one ontology (use --ontology …)")
        name = ontology_names[0]
        mt_path = args.meta_task_config or str(resolve_default_config_for_ontology(name))
        ctx = build_agentic_generation_context(
            ontology_name=name,
            meta_task_config_path=mt_path,
            output_root=args.output_root,
            write_files=True,
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
        contexts = [
            build_agentic_generation_context(
                ontology_name=name,
                meta_task_config_path=args.meta_task_config,
                output_root=args.output_root,
                write_files=True,
            )
            for name in ontology_names
        ]
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
            output_root=args.output_root,
            generate_scripts=generate_scripts,
            generate_prompts=generate_prompts,
            repair_loop=args.repair_loop,
            exercise_repair=args.exercise_repair,
            max_repair_iterations=args.max_repair_iterations,
            llm_agent_generation=args.llm_agent_generation,
            generation_model=args.generation_model,
            max_agent_rounds=args.max_agent_rounds,
        )

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
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
