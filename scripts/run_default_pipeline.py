"""Shipped 5-step default pipeline: generate → MCP → extract → KG → score."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from ship_lib import (
    DEFAULT_WORKERS,
    GENERATION_TAG,
    MAIN_PDF_DIR,
    MAIN_RUN_TAG,
    MEDICAL_GOLD,
    MEDICAL_SCHEMA,
    ONTOMED_PDF_DIR,
    ONTOMED_RUN_TAG,
    PROTOCOL,
    active_generation_root,
    bounded_workers,
    campaign_path,
    chemistry_score_table,
    child_env,
    command_succeeded,
    configure_stdio,
    ensure_scorer_repo,
    format_score_report,
    hash_cli,
    latest_scenario_run,
    load_campaign,
    marker_complete,
    missing_pdfs,
    parse_step,
    python_executable,
    repo_root,
    required_env_keys,
    run_logged,
    save_campaign,
    select_eval_cases,
)

EXTRACT_UNTIL = "main_ontology_extractions"
EXTRACT_MARKER = ".main_ontology_extractions_done"
KG_MARKER = ".main_kg_building_done"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the shipped default pipeline: GPT-5 extraction-prompt generation, "
            "MCP generation for OntoSynthesis and OntoMed, extraction, Pipeline KG "
            f"under {PROTOCOL}, then score and print the report."
        )
    )
    parser.add_argument(
        "cases_positional",
        nargs="?",
        type=int,
        default=None,
        help="How many eval cases to run (1-30). Same as --cases. Default: 10.",
    )
    parser.add_argument("--cases", type=int, default=10, help="How many eval cases (1-30). Default: 10.")
    parser.add_argument(
        "--from-step",
        default="1",
        help="Start at step 1-5 or generate/mcp/extract/kg/score. Default: 1.",
    )
    parser.add_argument(
        "--domain",
        choices=("both", "main", "ontomed"),
        default="both",
        help="Which domains to generate, extract, build, and score. Default: both (main + OntoMed).",
    )
    parser.add_argument("--generation-tag", default=GENERATION_TAG)
    parser.add_argument("--scorer-repo", default=None)
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"Parallelism for every step (default: {DEFAULT_WORKERS}): "
            "prompt authoring, MCP compile waves, paper extract/KG, and scoring."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _want(domain: str, name: str) -> bool:
    return domain == "both" or domain == name


def _hashes(cases: list[dict[str, str]]) -> list[str]:
    return [item["hash"] for item in cases]


def _check_pdfs(root: Path, *, domain: str, main_cases: list[dict[str, str]], medical_cases: list[dict[str, str]]) -> None:
    problems: list[str] = []
    if _want(domain, "main"):
        missing = missing_pdfs(main_cases, root / MAIN_PDF_DIR)
        if missing:
            problems.append(
                f"Main PDFs missing under {MAIN_PDF_DIR}: " + ", ".join(missing)
            )
    if _want(domain, "ontomed"):
        missing = missing_pdfs(medical_cases, root / ONTOMED_PDF_DIR)
        if missing:
            problems.append(
                f"OntoMed PDFs missing under {ONTOMED_PDF_DIR}: " + ", ".join(missing)
            )
    if problems:
        raise SystemExit("\n".join(problems))


def _python() -> str:
    return python_executable()


def _module_cmd(module: str, *args: str) -> list[str]:
    return [_python(), "-m", module, *args]


def _worker_flags(args: argparse.Namespace) -> list[str]:
    return ["--workers", str(args.workers)]


def _run_jobs_parallel(
    jobs: list[tuple[str, list[str]]],
    *,
    env: dict[str, str],
    dry_run: bool,
    workers: int,
) -> None:
    if not jobs:
        return

    def _one(label: str, argv: list[str]) -> tuple[str, int]:
        return label, run_logged(argv, env=env, dry_run=dry_run)

    pool_n = bounded_workers(workers, len(jobs))
    if pool_n == 1 or len(jobs) == 1:
        for label, argv in jobs:
            rc = run_logged(argv, env=env, dry_run=dry_run)
            if not command_succeeded(rc):
                raise SystemExit(f"{label} failed (exit {rc})")
        return
    with ThreadPoolExecutor(max_workers=pool_n) as pool:
        futures = [pool.submit(_one, label, argv) for label, argv in jobs]
        failures: list[str] = []
        for future in futures:
            label, rc = future.result()
            if not command_succeeded(rc):
                failures.append(f"{label} (exit {rc})")
        if failures:
            raise SystemExit("failed: " + "; ".join(failures))


def _prompt_generation_jobs(args: argparse.Namespace) -> list[list[str]]:
    """Official s1–s4 authoring: one batch, `--stage all`, workers=5, all domains.

    Chemistry packs wrote ontosynthesis then ontomops/ontospecies (and medical
    when `--all-domains`). Do not skip extensions: `--test` extract still
    launches those MCP servers.
    """
    common = ["--tag", args.generation_tag, "--stage", "all", *_worker_flags(args)]
    if args.domain == "both":
        return [_module_cmd("src.extraction_prompt_generation", "--all-domains", *common)]
    names: list[str] = []
    if _want(args.domain, "main"):
        names.extend(["ontosynthesis", "ontomops", "ontospecies"])
    if _want(args.domain, "ontomed"):
        names.append("medical")
    return [_module_cmd("src.extraction_prompt_generation", name, *common) for name in names]


def step_generate(args: argparse.Namespace, env: dict[str, str]) -> Path:
    root = repo_root()
    print("\n=== STEP 1 / 5  GPT-5 extraction prompt generation ===", flush=True)
    jobs = _prompt_generation_jobs(args)
    if not jobs:
        raise SystemExit("prompt generation has no ontologies for this --domain")
    for argv in jobs:
        label = " ".join(argv[3:5])
        rc = run_logged(argv, env=env, dry_run=args.dry_run)
        if not command_succeeded(rc):
            raise SystemExit(f"prompt generation failed ({label}, exit {rc})")
    if args.dry_run:
        return root / "generated" / "runs" / f"<stamp>_{args.generation_tag}"
    return active_generation_root(root)


def step_mcp(args: argparse.Namespace, env: dict[str, str], generated_root: Path) -> None:
    print("\n=== STEP 2 / 5  MCP generation (main + OntoMed) ===", flush=True)
    first: list[str] = []
    second: list[str] = []
    if _want(args.domain, "main"):
        # OntoSyn --test still launches extension MCP servers from domain
        # mcp_capabilities, even when the step list stops at main extraction.
        # Extensions need OntoSynthesis compiled first in the same package.
        first.append("ontosynthesis")
        second.extend(["ontomops", "ontospecies"])
    if _want(args.domain, "ontomed"):
        first.append("medical")
    for wave in (first, second):
        jobs = [
            (
                f"MCP {name}",
                _module_cmd(
                    "src.kg_building_mcp_generation",
                    name,
                    "--output-root",
                    str(generated_root),
                ),
            )
            for name in wave
        ]
        _run_jobs_parallel(
            jobs,
            env=env,
            dry_run=args.dry_run,
            workers=args.workers,
        )


def _mint_extract(
    *,
    ontology: str,
    tag: str,
    hashes: list[str],
    generated_root: Path,
    env: dict[str, str],
    dry_run: bool,
    workers: int,
) -> int:
    return run_logged(
        _module_cmd(
            "src.extraction_runtime",
            ontology,
            "--generation-run",
            str(generated_root),
            "--until",
            EXTRACT_UNTIL,
            "--test",
            "--tag",
            tag,
            "--workers",
            str(workers),
            *hash_cli(hashes),
        ),
        env=env,
        dry_run=dry_run,
    )


def step_extract(
    args: argparse.Namespace,
    env: dict[str, str],
    generated_root: Path,
    main_hashes: list[str],
    medical_hashes: list[str],
    campaign: dict[str, Any],
) -> dict[str, Any]:
    print("\n=== STEP 3 / 5  Extraction (main + OntoMed) ===", flush=True)
    root = repo_root()
    failed = False
    if _want(args.domain, "main"):
        rc = _mint_extract(
            ontology="ontosynthesis",
            tag=MAIN_RUN_TAG,
            hashes=main_hashes,
            generated_root=generated_root,
            env=env,
            dry_run=args.dry_run,
            workers=args.workers,
        )
        run_dir = latest_scenario_run("mops", MAIN_RUN_TAG, root=root)
        if args.dry_run:
            campaign["main"] = {
                "ontology": "ontosynthesis",
                "run_dir": f"scenarios/mops/runs/<stamp>_{MAIN_RUN_TAG}",
                "config": f"scenarios/mops/runs/<stamp>_{MAIN_RUN_TAG}/pipeline.resolved.json",
                "hashes": main_hashes,
            }
        elif run_dir is None:
            failed = True
            print("[FAIL] Could not find the OntoSynthesis extract run directory", flush=True)
        else:
            runtime = run_dir / "runtime"
            ok, missing = marker_complete(runtime, main_hashes, EXTRACT_MARKER)
            campaign["main"] = {
                "ontology": "ontosynthesis",
                "run_dir": str(run_dir.relative_to(root).as_posix()),
                "config": str((run_dir / "pipeline.resolved.json").relative_to(root).as_posix()),
                "hashes": main_hashes,
            }
            if not command_succeeded(rc) and not ok:
                failed = True
                print(f"[FAIL] OntoSynthesis extraction incomplete: {missing}", flush=True)
            elif not ok:
                failed = True
                print(f"[FAIL] OntoSynthesis extraction markers missing: {missing}", flush=True)
            elif rc in {3221225477, -1073741819}:
                print("[WARN] Windows process fault after extract; markers are present, continuing", flush=True)
            else:
                print(f"[OK] OntoSynthesis extraction: {run_dir}", flush=True)
    if _want(args.domain, "ontomed"):
        rc = _mint_extract(
            ontology="medical",
            tag=ONTOMED_RUN_TAG,
            hashes=medical_hashes,
            generated_root=generated_root,
            env=env,
            dry_run=args.dry_run,
            workers=args.workers,
        )
        run_dir = latest_scenario_run("medical", ONTOMED_RUN_TAG, root=root)
        if args.dry_run:
            campaign["ontomed"] = {
                "ontology": "medical",
                "run_dir": f"scenarios/medical/runs/<stamp>_{ONTOMED_RUN_TAG}",
                "config": f"scenarios/medical/runs/<stamp>_{ONTOMED_RUN_TAG}/pipeline.resolved.json",
                "hashes": medical_hashes,
            }
        elif run_dir is None:
            failed = True
            print("[FAIL] Could not find the OntoMed extract run directory", flush=True)
        else:
            runtime = run_dir / "runtime"
            ok, missing = marker_complete(runtime, medical_hashes, EXTRACT_MARKER)
            campaign["ontomed"] = {
                "ontology": "medical",
                "run_dir": str(run_dir.relative_to(root).as_posix()),
                "config": str((run_dir / "pipeline.resolved.json").relative_to(root).as_posix()),
                "hashes": medical_hashes,
            }
            if not command_succeeded(rc) and not ok:
                failed = True
                print(f"[FAIL] OntoMed extraction incomplete: {missing}", flush=True)
            elif not ok:
                failed = True
                print(f"[FAIL] OntoMed extraction markers missing: {missing}", flush=True)
            elif rc in {3221225477, -1073741819}:
                print("[WARN] Windows process fault after extract; markers are present, continuing", flush=True)
    if not args.dry_run:
        save_campaign(campaign, root=root)
    if failed:
        raise SystemExit("extraction step failed")
    return campaign


def _kg_one(
    *,
    ontology: str,
    config_path: Path,
    hashes: list[str],
    env: dict[str, str],
    dry_run: bool,
    workers: int,
) -> int:
    return run_logged(
        _module_cmd(
            "src.extraction_runtime",
            ontology,
            "--config",
            str(config_path),
            "--resume",
            "--protocol",
            PROTOCOL,
            "--test",
            "--workers",
            str(workers),
            *hash_cli(hashes),
        ),
        env=env,
        dry_run=dry_run,
    )


def step_kg(
    args: argparse.Namespace,
    env: dict[str, str],
    campaign: dict[str, Any],
) -> None:
    print(f"\n=== STEP 4 / 5  Pipeline KG ({PROTOCOL}, main + OntoMed) ===", flush=True)
    root = repo_root()
    failed = False
    if _want(args.domain, "main"):
        block = campaign.get("main") or {}
        config = root / str(block.get("config") or "")
        hashes = list(block.get("hashes") or [])
        if not args.dry_run and not config.is_file():
            raise SystemExit("OntoSynthesis extract run is missing; re-run from step 3")
        rc = _kg_one(
            ontology="ontosynthesis",
            config_path=config,
            hashes=hashes,
            env=env,
            dry_run=args.dry_run,
            workers=args.workers,
        )
        if not args.dry_run:
            runtime = (root / str(block["run_dir"])) / "runtime"
            ok, missing = marker_complete(runtime, hashes, KG_MARKER)
            if not command_succeeded(rc) and not ok:
                failed = True
                print(f"[FAIL] OntoSynthesis KG incomplete: {missing}", flush=True)
            elif not ok:
                print(f"[WARN] OntoSynthesis KG markers missing: {missing}", flush=True)
                failed = True
    if _want(args.domain, "ontomed"):
        block = campaign.get("ontomed") or {}
        config = root / str(block.get("config") or "")
        hashes = list(block.get("hashes") or [])
        if not args.dry_run and not config.is_file():
            raise SystemExit("OntoMed extract run is missing; re-run from step 3")
        rc = _kg_one(
            ontology="medical",
            config_path=config,
            hashes=hashes,
            env=env,
            dry_run=args.dry_run,
            workers=args.workers,
        )
        if not args.dry_run:
            runtime = (root / str(block["run_dir"])) / "runtime"
            ok, missing = marker_complete(runtime, hashes, KG_MARKER)
            if not command_succeeded(rc) and not ok:
                failed = True
                print(f"[FAIL] OntoMed KG incomplete: {missing}", flush=True)
            elif not ok:
                print(f"[WARN] OntoMed KG markers missing: {missing}", flush=True)
                failed = True
    if failed:
        raise SystemExit("KG step failed")


def _score_chemistry(
    *,
    run_dir: Path,
    hashes: list[str],
    scorer: Path,
    dry_run: bool,
    workers: int,
) -> dict[str, float | None]:
    ox = ROOT / "src" / "kg_building" / "ontologx"
    if str(ox) not in sys.path:
        sys.path.insert(0, str(ox))
    from score_four import convert_runtime_many, score_four

    runtime = run_dir / "runtime"
    merged = run_dir / "merged"
    scores = run_dir / "scores"
    if dry_run:
        print(f"[dry-run] convert+score_four workers={workers} → {scores}", flush=True)
        return {name: None for name in ("scoring_chemicals", "scoring_steps", "scoring_characterisation", "scoring_cbu")}
    convert_runtime_many(
        data_dir=runtime,
        output_dir=merged,
        paper_hashes=hashes,
        converter_repo=scorer,
        max_workers=workers,
    )
    score_four(
        pred_root=merged,
        out_root=scores,
        paper_hashes=hashes,
        scorer_repo=scorer,
        max_workers=workers,
    )
    return chemistry_score_table(scores)


def _score_medical(
    *,
    run_dir: Path,
    scorer: Path,
    env: dict[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    eval_dir = run_dir / "evaluation"
    pred = eval_dir / "predicted.csv"
    report_json = eval_dir / "scoring_report.json"
    report_md = eval_dir / "scoring_report.md"
    runtime = run_dir / "runtime"
    convert = [
        _python(),
        str(scorer / "scripts" / "medical_ttl_to_csv_sparql.py"),
        "--data-dir",
        str(runtime.resolve()),
        "--output",
        str(pred.resolve()),
        "--reference-csv",
        str((repo_root() / MEDICAL_GOLD).resolve()),
        "--reference-csv-header-row",
        "0",
        "--schema-ttl",
        str((repo_root() / MEDICAL_SCHEMA).resolve()),
    ]
    score = [
        _python(),
        str(scorer / "scripts" / "medical_score_predicted_vs_gold.py"),
        "--gold",
        str((repo_root() / MEDICAL_GOLD).resolve()),
        "--pred",
        str(pred.resolve()),
        "--out-json",
        str(report_json.resolve()),
        "--out-md",
        str(report_md.resolve()),
    ]
    rc = run_logged(convert, cwd=scorer, env=env, dry_run=dry_run)
    if not command_succeeded(rc):
        raise SystemExit(f"medical TTL→CSV failed (exit {rc})")
    rc = run_logged(score, cwd=scorer, env=env, dry_run=dry_run)
    if not command_succeeded(rc):
        raise SystemExit(f"medical scoring failed (exit {rc})")
    if dry_run or not report_json.is_file():
        return {}
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def step_score(
    args: argparse.Namespace,
    env: dict[str, str],
    campaign: dict[str, Any],
    cases: int,
) -> Path:
    print("\n=== STEP 5 / 5  Score and report ===", flush=True)
    root = repo_root()
    scorer = ensure_scorer_repo(args.scorer_repo, root=root, clone=not args.dry_run)
    if scorer is None:
        if args.dry_run:
            print("[dry-run] scorer checkout not found; score commands skipped", flush=True)
            print(format_score_report(cases=cases, main_scores=None, medical=None), flush=True)
            return campaign_path(root)
        raise SystemExit(
            "Could not find or clone scoring engines. "
            "setup.cmd / run.cmd clone them into data/third_party_repos/ automatically."
        )
    print(f"[OK] Scorer: {scorer}", flush=True)
    main_scores = None
    medical = None
    main_run = ""
    medical_run = ""
    if _want(args.domain, "main"):
        block = campaign.get("main") or {}
        run_dir = root / str(block.get("run_dir") or "")
        hashes = list(block.get("hashes") or [])
        if not args.dry_run and not run_dir.is_dir():
            raise SystemExit("OntoSynthesis run directory is missing; re-run from step 3")
        main_run = str(block.get("run_dir") or "")
        main_scores = _score_chemistry(
            run_dir=run_dir,
            hashes=hashes,
            scorer=scorer,
            dry_run=args.dry_run,
            workers=args.workers,
        )
    if _want(args.domain, "ontomed"):
        block = campaign.get("ontomed") or {}
        run_dir = root / str(block.get("run_dir") or "")
        if not args.dry_run and not run_dir.is_dir():
            raise SystemExit("OntoMed run directory is missing; re-run from step 3")
        medical_run = str(block.get("run_dir") or "")
        medical = _score_medical(
            run_dir=run_dir,
            scorer=scorer,
            env=env,
            dry_run=args.dry_run,
        )
    report = format_score_report(
        cases=cases,
        main_scores=main_scores,
        medical=medical,
        main_run=main_run,
        medical_run=medical_run,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = root / "generated" / "ship_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{stamp}_scores.txt"
    payload_path = report_dir / f"{stamp}_scores.json"
    if not args.dry_run:
        report_path.write_text(report, encoding="utf-8")
        payload_path.write_text(
            json.dumps(
                {
                    "cases": cases,
                    "protocol": PROTOCOL,
                    "main": main_scores,
                    "ontomed": {
                        "overall_accuracy": None if medical is None else medical.get("overall_accuracy"),
                        "mean_per_case_accuracy": None
                        if medical is None
                        else medical.get("mean_per_case_accuracy"),
                        "n_cases": None if medical is None else medical.get("n_cases"),
                    },
                    "main_run": main_run,
                    "ontomed_run": medical_run,
                    "generation_run": campaign.get("generation_run"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print("\n" + report, flush=True)
    if not args.dry_run:
        print(f"[OK] Wrote {report_path}", flush=True)
        print(f"[OK] Wrote {payload_path}", flush=True)
    return report_path


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    cases_count = args.cases_positional if args.cases_positional is not None else args.cases
    try:
        from_step = parse_step(args.from_step)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2
    if args.workers < 1:
        print("[FAIL] --workers must be at least 1")
        return 2
    try:
        main_cases = select_eval_cases(cases_count, kind="main")
        medical_cases = select_eval_cases(cases_count, kind="ontomed")
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2

    root = repo_root()
    os_missing = required_env_keys(root)
    if os_missing and not args.dry_run:
        print("[FAIL] Copy .env.example to .env and set: " + ", ".join(os_missing))
        return 2

    if from_step <= 3 and not args.dry_run:
        try:
            _check_pdfs(
                root,
                domain=args.domain,
                main_cases=main_cases,
                medical_cases=medical_cases,
            )
        except SystemExit as exc:
            print(f"[FAIL] {exc}")
            return 2

    scorer = None
    if from_step <= 5 and not args.dry_run:
        scorer = ensure_scorer_repo(args.scorer_repo, root=root)
        if scorer is None:
            print(
                "[FAIL] Could not find or clone scoring engines. "
                "Need git access to origin (archive branch). "
                "setup.cmd does this automatically."
            )
            return 2
        print(f"[OK] Scorer: {scorer}")

    env = child_env(root)
    if scorer is not None:
        env["SCORER_REPO"] = str(scorer)
    generated_root = None
    campaign = load_campaign(root)
    campaign.update(
        {
            "schema_version": "ship-campaign.v1",
            "cases": cases_count,
            "protocol": PROTOCOL,
            "domain": args.domain,
            "workers": args.workers,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    if from_step > 1:
        try:
            generated_root = active_generation_root(root)
            campaign["generation_run"] = str(generated_root.relative_to(root).as_posix())
        except Exception:
            generated_root = None
        if generated_root is None or (not args.dry_run and not generated_root.is_dir()):
            print("[FAIL] No generation package. Re-run from step 1.")
            return 2

    print("=" * 60)
    print("Shipped default pipeline")
    print("=" * 60)
    print(f"Cases: {cases_count}")
    print(f"From step: {from_step}")
    print(f"Domain: {args.domain}")
    print(f"Protocol: {PROTOCOL}")
    print(f"Workers: {args.workers}")
    if _want(args.domain, "main"):
        print("Main hashes: " + ", ".join(_hashes(main_cases)))
    if _want(args.domain, "ontomed"):
        print("OntoMed hashes: " + ", ".join(_hashes(medical_cases)))
    print(f"Campaign: {campaign_path(root)}")
    print("=" * 60, flush=True)

    if from_step <= 1:
        generated_root = step_generate(args, env)
        campaign["generation_run"] = (
            str(generated_root.relative_to(root).as_posix())
            if generated_root.is_relative_to(root)
            else str(generated_root)
        )
        if not args.dry_run:
            save_campaign(campaign, root=root)
    assert generated_root is not None
    env["TWA_GENERATED_ARTIFACT_ROOT"] = str(generated_root)

    if from_step <= 2:
        step_mcp(args, env, generated_root)

    if from_step <= 3:
        campaign = step_extract(
            args,
            env,
            generated_root,
            _hashes(main_cases),
            _hashes(medical_cases),
            campaign,
        )
        campaign["generation_run"] = (
            str(generated_root.relative_to(root).as_posix())
            if generated_root.is_relative_to(root)
            else str(generated_root)
        )
        if not args.dry_run:
            save_campaign(campaign, root=root)
    else:
        campaign.setdefault("main", {"hashes": _hashes(main_cases)})
        campaign.setdefault("ontomed", {"hashes": _hashes(medical_cases)})

    if from_step <= 4:
        step_kg(args, env, campaign)

    if from_step <= 5:
        step_score(args, env, campaign, cases_count)

    print("\n[OK] Default pipeline finished", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
