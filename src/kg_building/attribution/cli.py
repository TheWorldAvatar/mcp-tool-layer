"""CLI: python -m src.kg_building.attribution"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.kg_building.attribution.atoms import MODULES
from src.kg_building.attribution.judge import ATTRIBUTION_MODEL
from src.kg_building.attribution.run import OFFICIAL_MODULES, attribute_run
from src.kg_building.scorer_repo import find_scorer_repo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Attribute official KG scorer mistakes to extraction vs KG building "
            "by asking gpt-5.6-sol whether each missed fact is already in the ledger."
        )
    )
    parser.add_argument("--pred-root", type=Path, required=True, help="merged/ folder")
    parser.add_argument(
        "--hint-runs",
        action="append",
        dest="hint_runs",
        required=True,
        help="Pipeline/OX run that holds mcp_run ledgers. Repeatable.",
    )
    parser.add_argument("--hash", action="append", dest="hashes", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--gt-root", type=Path, help="full_ground_truth folder")
    parser.add_argument("--scorer-repo", type=Path)
    parser.add_argument(
        "--module",
        action="append",
        dest="modules",
        choices=OFFICIAL_MODULES,
        help="Repeat to restrict modules. Default: four modules, plus CBU formula-only when --scores-root is set.",
    )
    parser.add_argument("--model", default=ATTRIBUTION_MODEL)
    parser.add_argument(
        "--scores-root",
        type=Path,
        help="Official scores/ folder. Uses scorer TP/FP/FN instead of the homemade collector.",
    )
    parser.add_argument(
        "--heuristic-only",
        action="store_true",
        help="Skip the LLM and use fingerprint presence only.",
    )
    parser.add_argument(
        "--cbu-derivation",
        action="store_true",
        help="Tag CBU FNs absent from the ledger as derivation, not extraction.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Reuse per-hash JSON already in --out-dir.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    modules = tuple(args.modules) if args.modules else (
        OFFICIAL_MODULES if args.scores_root else MODULES
    )
    scorer = args.scorer_repo or find_scorer_repo()
    overall = attribute_run(
        list(args.hashes),
        pred_root=args.pred_root,
        hint_runs=list(args.hint_runs),
        out_dir=args.out_dir,
        gt_root=args.gt_root,
        scorer_repo=scorer,
        modules=modules,
        model=args.model,
        heuristic_only=args.heuristic_only,
        cbu_derivation=args.cbu_derivation,
        skip_existing=args.skip_existing,
        scores_root=args.scores_root,
    )
    print(f"[OK] attribution -> {args.out_dir / '_overall.md'}", flush=True)
    print(
        f"monotonicity_ok={overall['monotonicity_ok']} papers={len(overall['papers'])}",
        flush=True,
    )
    return 0 if overall["monotonicity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
