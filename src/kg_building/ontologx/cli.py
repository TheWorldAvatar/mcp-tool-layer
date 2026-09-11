"""OntoLogX KG-building CLI (locked protocol profiles)."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from src.kg_building.experiment_protocol import (
    OX_PROTOCOL_NAMES,
    apply_to_ox_args,
    resolve_scorer_repo,
)
from src.kg_building.ontologx.paths import HERE, REPO_ROOT
from src.kg_building.ontologx.run_loop import run_papers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize KG with OntoLogX. Pass --protocol for the locked 1:1 setup."
    )
    parser.add_argument("--domain", choices=("ontosynthesis", "medical"), default="ontosynthesis")
    parser.add_argument("--papers", type=Path, default=HERE / "papers_eval30.json")
    parser.add_argument("--hash", action="append", dest="hashes")
    parser.add_argument("--model", default="openai/gpt-4o-2024-11-20")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hint-runs", action="append", dest="hint_runs")
    parser.add_argument("--from-main-run", type=Path)
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        choices=("ontospecies", "ontomops"),
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--protocol",
        choices=OX_PROTOCOL_NAMES,
        help=(
            "Locked 1:1 OX experiment. Pins seed 42, gpt-4o-2024-11-20, no paper "
            "body, no --from-main-run. generic-strict / generic-noprompt / "
            "with-prompt exclude official ONEPASS; full-prompt is the historical "
            "official system (from_extraction + full_hints + ONEPASS + T-Box). "
            "generic-strict / generic-noprompt / with-prompt share a hardcoded "
            "hops occurrence (MCP expander dual-encoding). with-prompt-qty / "
            "with-prompt-hops only add dual-coding appendices on with-prompt."
        ),
    )
    parser.add_argument(
        "--prompt-profile",
        choices=OX_PROTOCOL_NAMES,
        help="Deprecated alias of --protocol.",
    )
    parser.add_argument("--mop-derivation", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument(
        "--scorer-repo",
        type=Path,
        help="Repo that still hosts evaluation.scoring_* (read-only).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(REPO_ROOT / ".env", override=True)
    args = build_parser().parse_args(argv)
    if args.domain == "medical":
        args.extensions = []
        if args.papers == HERE / "papers_eval30.json":
            args.papers = HERE / "papers_medical.json"
    protocol = args.protocol or args.prompt_profile
    if not protocol:
        print(
            "[FAIL] --protocol {generic-noprompt, generic-strict, with-prompt, "
            "with-prompt-qty, with-prompt-hops, full-prompt} is required"
        )
        return 1
    if args.protocol and args.prompt_profile and args.protocol != args.prompt_profile:
        print("[FAIL] --protocol and --prompt-profile disagree")
        return 1
    requested_model = args.model
    try:
        apply_to_ox_args(args, protocol)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1
    if requested_model and requested_model != args.model:
        args.model = requested_model
        print(f"[OK] Locked 1:1 protocol {protocol}; model override {args.model}")
    else:
        print(f"[OK] Locked 1:1 protocol {protocol}")
    if "kimi-k3" in str(args.model).lower():
        effort = (os.environ.get("TWA_REASONING_EFFORT") or "low").strip().lower() or "low"
        print(f"[OK] kimi-k3 reasoning effort={effort}")
    if args.from_main_run is not None and not args.from_main_run.is_absolute():
        args.from_main_run = REPO_ROOT / args.from_main_run
    if not args.out_dir.is_absolute():
        args.out_dir = (REPO_ROOT / args.out_dir).resolve()
    if args.scorer_repo is not None and not args.scorer_repo.is_absolute():
        args.scorer_repo = (REPO_ROOT / args.scorer_repo).resolve()
    elif args.score and args.scorer_repo is None:
        args.scorer_repo = resolve_scorer_repo()
    summary = run_papers(args)
    runtime = args.out_dir / "score_runtime"
    if args.mop_derivation:
        from mop_hook import run_mop_derivation

        for paper in summary.get("papers") or []:
            run_mop_derivation(data_dir=runtime, paper_hash=paper["hash"])
    if args.score:
        from score_four import convert_runtime, score_four

        scorer = args.scorer_repo
        merged = args.out_dir / "merged"
        scores = args.out_dir / "scores"
        hashes = [paper["hash"] for paper in summary.get("papers") or []]
        for paper_hash in hashes:
            convert_runtime(
                data_dir=runtime,
                output_dir=merged,
                paper_hash=paper_hash,
                converter_repo=scorer,
            )
        if scorer is not None and hashes:
            score_four(
                pred_root=merged,
                out_root=scores,
                paper_hashes=hashes,
                scorer_repo=scorer,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
