"""One-click locked 1:1 Pipeline + OntoLogX runner.

Extraction runtimes are not in git. This script unpacks eval PDFs and frozen
MCP packs when the zip files are present, then extract → Pipeline KG → OX
for one protocol. Re-running the same pack/protocol/hashes reuses the last
campaign run when markers are complete.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from eval_inputs import (
    check_inputs,
    chemistry_pack_path,
    mcp_pack_ready,
    medical_pack_path,
    missing_required_pdfs,
    print_check,
    unpack_if_present,
    write_expected_manifest,
)
from ship_lib import (
    DEFAULT_WORKERS,
    MEDICAL_GOLD,
    MEDICAL_SCHEMA,
    chemistry_score_table,
    child_env,
    command_succeeded,
    configure_stdio,
    ensure_scorer_repo,
    hash_cli,
    latest_scenario_run,
    marker_complete,
    python_executable,
    repo_root,
    required_env_keys,
    run_logged,
    select_eval_cases,
)

# EXTRACT_MARKER / KG_MARKER live in run_default_pipeline; keep local copies
# so this script does not import that module's CLI main.
EXTRACT_DONE = ".main_ontology_extractions_done"
KG_DONE = ".main_kg_building_done"

LOCKED_PROTOCOLS = ("generic-strict", "generic-noprompt", "with-prompt")
LOCKED_CAMPAIGN = "locked_campaign.json"
DEFAULT_KG_MODEL = "openai/gpt-4o-2024-11-20"
KIMI_MODEL = "moonshotai/kimi-k3"

KG_MODEL_ALIASES = {
    "gpt-4o": DEFAULT_KG_MODEL,
    "gpt4o": DEFAULT_KG_MODEL,
    "kimi": KIMI_MODEL,
    "kimi-k3": KIMI_MODEL,
}
EXTRACT_MODEL_ALIASES = {
    "gpt-4.1": "gpt-4.1-2025-04-14",
    "gpt4.1": "gpt-4.1-2025-04-14",
    "gpt-5": "gpt-5-2025-08-07",
    "gpt5": "gpt-5-2025-08-07",
    "kimi": KIMI_MODEL,
    "kimi-k3": KIMI_MODEL,
}
PROTOCOL_TAG = {
    "generic-strict": "s",
    "generic-noprompt": "n",
    "with-prompt": "w",
}


def _alias(value: str | None, table: dict[str, str], default: str | None = None) -> str | None:
    if value is None or not str(value).strip():
        return default
    raw = str(value).strip()
    return table.get(raw.lower(), raw)


def locked_campaign_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "generated" / LOCKED_CAMPAIGN


def _module_cmd(module: str, *args: str) -> list[str]:
    return [python_executable(), "-m", module, *args]


def _hashes(cases: list[dict[str, str]]) -> list[str]:
    return [item["hash"] for item in cases]


def _want(domain: str, name: str) -> bool:
    return domain == "both" or domain == name


def _run_tag(pack: str, protocol: str) -> str:
    return f"lk{pack}{PROTOCOL_TAG[protocol]}"


def _ox_dirname(domain: str, pack: str, protocol: str) -> str:
    prefix = "ox_m" if domain == "ontomed" else "ox"
    return f"{prefix}_{_run_tag(pack, protocol)}"


def resolve_models(args: argparse.Namespace) -> tuple[str, str | None]:
    kg = _alias(args.kg_model, KG_MODEL_ALIASES, DEFAULT_KG_MODEL)
    extract = _alias(args.extract_model, EXTRACT_MODEL_ALIASES, None)
    assert kg is not None
    return kg, extract


def _ensure_inputs(args: argparse.Namespace) -> int:
    root = repo_root()
    write_expected_manifest(root=root)
    unpack_if_present(root=root)
    missing = missing_required_pdfs(cases=args.cases, domain=args.domain, root=root)
    if missing:
        print("[FAIL] Eval PDFs are not in git and are missing locally:")
        for item in missing[:15]:
            print(f"       {item}")
        print("       Put eval30_pdfs.zip in data/eval_bundles/ then re-run,")
        print("       or copy PDFs into scenarios/mops/datasets/eval30 and")
        print("       scenarios/medical/datasets/eval30. See docs/ONE_CLICK_RUN.md.")
        return 2
    if _want(args.domain, "main"):
        pack = chemistry_pack_path(args.pack, root=root)
        if not mcp_pack_ready(pack):
            print(f"[FAIL] Frozen chemistry MCP pack missing: {pack}")
            print("       Put locked_mcp_packs.zip in data/eval_bundles/ and unpack.")
            print("       Do not generate a new pack if you want the paper MCP surface.")
            return 2
    if _want(args.domain, "ontomed"):
        pack = medical_pack_path(root=root)
        if not mcp_pack_ready(pack):
            print(f"[FAIL] Frozen OntoMed MCP pack missing: {pack}")
            print("       Put locked_mcp_packs.zip in data/eval_bundles/ and unpack.")
            return 2
    return 0


def _score_medical(run_dir: Path, scorer: Path, env: dict[str, str], *, dry_run: bool) -> dict[str, Any]:
    eval_dir = run_dir / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    pred = eval_dir / "predicted.csv"
    report_json = eval_dir / "scoring_report.json"
    report_md = eval_dir / "scoring_report.md"
    runtime = run_dir / "score_runtime" if (run_dir / "score_runtime").is_dir() else run_dir / "runtime"
    convert = [
        python_executable(),
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
        python_executable(),
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
    if not command_succeeded(run_logged(convert, cwd=scorer, env=env, dry_run=dry_run)):
        raise SystemExit("medical TTL→CSV failed")
    if not command_succeeded(run_logged(score, cwd=scorer, env=env, dry_run=dry_run)):
        raise SystemExit("medical scoring failed")
    if dry_run or not report_json.is_file():
        return {}
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _pipeline_argv(
    *,
    ontology: str,
    pack_root: Path,
    protocol: str,
    tag: str,
    hashes: list[str],
    workers: int,
    kg_model: str,
    extract_model: str | None,
    config_path: Path | None,
    score: bool,
) -> list[str]:
    argv = _module_cmd("src.extraction_runtime", ontology)
    if config_path is not None:
        argv.extend(["--config", str(config_path), "--resume"])
    else:
        argv.extend(["--generation-run", str(pack_root), "--tag", tag])
    argv.extend(["--protocol", protocol, "--test", "--workers", str(workers), *hash_cli(hashes)])
    if kg_model:
        argv.extend(["--kg-model", kg_model])
    if extract_model:
        argv.extend(["--extraction-model", extract_model])
    if score:
        argv.append("--score")
    return argv


def _ox_argv(
    *,
    domain: str,
    protocol: str,
    hint_run: Path,
    out_dir: Path,
    hashes: list[str],
    kg_model: str,
    score: bool,
    scorer: Path | None,
) -> list[str]:
    argv = _module_cmd(
        "src.kg_building.ontologx",
        "--domain",
        "medical" if domain == "ontomed" else "ontosynthesis",
        "--protocol",
        protocol,
        "--hint-runs",
        str(hint_run),
        "--out-dir",
        str(out_dir),
        "--model",
        kg_model,
        *hash_cli(hashes),
    )
    if score and domain != "ontomed":
        argv.append("--score")
        if scorer is not None:
            argv.extend(["--scorer-repo", str(scorer)])
    return argv


def _block_complete(run_dir: Path, hashes: list[str], marker: str) -> bool:
    ok, _missing = marker_complete(run_dir / "runtime", hashes, marker)
    return ok


def _reuse_pipeline(
    *,
    scenario: str,
    tag: str,
    hashes: list[str],
    explicit: Path | None,
) -> Path | None:
    run_dir = explicit
    if run_dir is None:
        run_dir = latest_scenario_run(scenario, tag)
    if run_dir is None or not run_dir.is_dir():
        return None
    if not _block_complete(run_dir, hashes, EXTRACT_DONE):
        return None
    if not _block_complete(run_dir, hashes, KG_DONE):
        return None
    return run_dir


def run_domain(
    *,
    args: argparse.Namespace,
    ontology: str,
    scenario: str,
    pack_root: Path,
    hashes: list[str],
    env: dict[str, str],
    scorer: Path | None,
    kg_model: str,
    extract_model: str | None,
    from_extract: Path | None,
) -> dict[str, Any]:
    tag = _run_tag(args.pack if scenario == "mops" else "m", args.protocol)
    builder = args.builder
    score_pipeline = builder in {"pipeline", "both"}
    run_ox = builder in {"ox", "both"}
    # OX needs Pipeline KG traces for the token budget, even when scoring OX only.
    need_pipeline = builder in {"pipeline", "ox", "both"}

    block: dict[str, Any] = {
        "ontology": ontology,
        "hashes": hashes,
        "pack": str(pack_root),
        "protocol": args.protocol,
        "kg_model": kg_model,
    }
    reused = None if from_extract else _reuse_pipeline(
        scenario=scenario, tag=tag, hashes=hashes, explicit=None
    )
    if from_extract is not None:
        reused = from_extract if from_extract.is_dir() else None
        if reused is None:
            raise SystemExit(f"Missing --from-extract {from_extract}")

    run_dir = reused
    if need_pipeline and (run_dir is None or not _block_complete(run_dir, hashes, KG_DONE)):
        config = None if run_dir is None else run_dir / "pipeline.resolved.json"
        argv = _pipeline_argv(
            ontology=ontology,
            pack_root=pack_root,
            protocol=args.protocol,
            tag=tag,
            hashes=hashes,
            workers=args.workers,
            kg_model=kg_model,
            extract_model=extract_model,
            config_path=config if config is not None and config.is_file() else None,
            score=score_pipeline and ontology != "medical",
        )
        rc = run_logged(argv, env=env, dry_run=args.dry_run)
        if args.dry_run:
            run_dir = run_dir or Path(f"scenarios/{scenario}/runs/<stamp>_{tag}")
        else:
            run_dir = latest_scenario_run(scenario, tag) if run_dir is None else run_dir
            if run_dir is None:
                raise SystemExit(f"Could not find {ontology} run tagged {tag}")
            if not command_succeeded(rc) and not _block_complete(run_dir, hashes, KG_DONE):
                raise SystemExit(f"{ontology} Pipeline KG failed")
    if run_dir is None:
        if args.dry_run:
            run_dir = Path(f"scenarios/{scenario}/runs/<stamp>_{tag}")
        else:
            raise SystemExit(f"{ontology} run directory is missing")

    try:
        block["run_dir"] = str(run_dir.resolve().relative_to(repo_root()).as_posix())
        block["config"] = str((run_dir / "pipeline.resolved.json").resolve().relative_to(repo_root()).as_posix())
    except (OSError, ValueError):
        block["run_dir"] = str(run_dir).replace("\\", "/")
        block["config"] = str(Path(run_dir) / "pipeline.resolved.json").replace("\\", "/")

    if score_pipeline and ontology == "medical" and scorer is not None:
        block["medical_score"] = _score_medical(run_dir, scorer, env, dry_run=args.dry_run)
    elif score_pipeline and ontology != "medical":
        scores = run_dir / "scores"
        if scores.is_dir():
            block["scores"] = chemistry_score_table(scores)

    if run_ox:
        ox_dir = repo_root() / "scenarios" / scenario / "runs" / _ox_dirname(ontology, args.pack, args.protocol)
        argv = _ox_argv(
            domain="ontomed" if ontology == "medical" else "main",
            protocol=args.protocol,
            hint_run=run_dir,
            out_dir=ox_dir,
            hashes=hashes,
            kg_model=kg_model,
            score=ontology != "medical",
            scorer=scorer,
        )
        rc = run_logged(argv, env=env, dry_run=args.dry_run)
        if not args.dry_run and not command_succeeded(rc):
            raise SystemExit(f"{ontology} OntoLogX failed")
        block["ox_dir"] = str(ox_dir).replace("\\", "/")
        try:
            block["ox_dir"] = str(ox_dir.resolve().relative_to(repo_root()).as_posix())
        except (OSError, ValueError):
            pass
        if ontology == "medical" and scorer is not None:
            block["ox_medical_score"] = _score_medical(ox_dir, scorer, env, dry_run=args.dry_run)
        elif ontology != "medical":
            ox_scores = ox_dir / "scores"
            if ox_scores.is_dir():
                block["ox_scores"] = chemistry_score_table(ox_scores)
    return block


def print_summary(campaign: dict[str, Any]) -> None:
    print("")
    print("=" * 60)
    print("LOCKED 1:1 SUMMARY")
    print("=" * 60)
    print(f"Protocol: {campaign.get('protocol')}")
    print(f"Pack:     {campaign.get('pack')}")
    print(f"KG model: {campaign.get('kg_model')}")
    print(f"Cases:    {campaign.get('cases')}")
    print(f"Builder:  {campaign.get('builder')}")
    for key, title in (("main", "OntoSynthesis"), ("ontomed", "OntoMed")):
        block = campaign.get(key)
        if not isinstance(block, dict):
            continue
        print(f"\n{title}")
        print(f"  pipeline  {block.get('run_dir')}")
        if block.get("ox_dir"):
            print(f"  ontologx  {block.get('ox_dir')}")
        scores = block.get("scores") or {}
        if scores:
            steps = scores.get("scoring_steps")
            if isinstance(steps, float):
                print(f"  pipeline steps F1={steps:.3f}")
        ox_scores = block.get("ox_scores") or {}
        if ox_scores:
            steps = ox_scores.get("scoring_steps")
            if isinstance(steps, float):
                print(f"  ontologx steps F1={steps:.3f}")
        medical = block.get("medical_score") or {}
        if isinstance(medical.get("overall_accuracy"), float):
            print(f"  pipeline accuracy={medical['overall_accuracy']:.1%}")
        ox_med = block.get("ox_medical_score") or {}
        if isinstance(ox_med.get("overall_accuracy"), float):
            print(f"  ontologx accuracy={ox_med['overall_accuracy']:.1%}")
    print("=" * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one locked 1:1 condition: unpack eval zips if needed, extract "
            "from PDFs (extraction is not in git), Pipeline KG, then OntoLogX."
        )
    )
    parser.add_argument(
        "cases_positional",
        nargs="?",
        type=int,
        default=None,
        help="How many eval cases (1-30). Same as --cases. Default: 1.",
    )
    parser.add_argument("--cases", type=int, default=1, help="How many eval cases (1-30). Default: 1.")
    parser.add_argument(
        "--protocol",
        choices=LOCKED_PROTOCOLS,
        default="generic-strict",
        help="Guidance surface. Default: generic-strict (paper Minimal).",
    )
    parser.add_argument("--domain", choices=("both", "main", "ontomed"), default="both")
    parser.add_argument(
        "--builder",
        choices=("both", "pipeline", "ox"),
        default="both",
        help="Pipeline MCP, OntoLogX, or both. OX still runs Pipeline KG first (token budget).",
    )
    parser.add_argument(
        "--pack",
        choices=("s1", "s2", "s3", "s4"),
        default="s1",
        help="Frozen chemistry MCP pack. OntoMed always uses med-s1_newmcp.",
    )
    parser.add_argument("--kg-model", default="gpt-4o", help="gpt-4o or kimi (aliases allowed).")
    parser.add_argument(
        "--extract-model",
        default=None,
        help="Optional extract override (gpt-4.1, gpt-5, kimi). Default: domain config.",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--from-extract", type=Path, help="Reuse this chemistry Pipeline run.")
    parser.add_argument("--from-extract-medical", type=Path, help="Reuse this OntoMed Pipeline run.")
    parser.add_argument("--scorer-repo", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true", help="Print input status and exit.")
    parser.add_argument("--unpack", action="store_true", help="Unpack eval zips and exit.")
    parser.add_argument("--list", action="store_true", help="Print the locked conditions and exit.")
    return parser


def _print_list() -> None:
    print("Locked 1:1 conditions this script can run")
    print("  Protocols: generic-strict (Minimal), generic-noprompt (Graph rules), with-prompt (KG guidance)")
    print("  Chemistry packs: s1 s2 s3 (gpt-4.1 extract) and s4 (use --extract-model kimi --kg-model kimi)")
    print("  OntoMed: generic-strict only was locked; other protocols still run if you ask")
    print("  KG models: gpt-4o (default) or kimi")
    print("  Builders: pipeline, ox, both")
    print("")
    print("Examples")
    print("  run_locked.cmd")
    print("  run_locked.cmd 30")
    print("  run_locked.cmd --protocol generic-noprompt --domain main --cases 30")
    print("  run_locked.cmd --pack s4 --extract-model kimi --kg-model kimi --domain main")
    print("")
    print("Extraction ledgers are not in git. The script extracts from PDFs.")
    print("Inputs: docs/ONE_CLICK_RUN.md")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.list:
        _print_list()
        return 0
    if args.unpack:
        write_expected_manifest()
        unpack_if_present(force=False)
        return print_check(check_inputs(cases=args.cases, domain=args.domain, pack=args.pack))
    if args.check:
        write_expected_manifest()
        return print_check(check_inputs(cases=args.cases, domain=args.domain, pack=args.pack))

    cases_count = args.cases_positional if args.cases_positional is not None else args.cases
    args.cases = cases_count
    if args.workers < 1:
        print("[FAIL] --workers must be at least 1")
        return 2
    try:
        main_cases = select_eval_cases(cases_count, kind="main")
        medical_cases = select_eval_cases(cases_count, kind="ontomed")
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2

    kg_model, extract_model = resolve_models(args)
    if "kimi-k3" in kg_model.lower() or (extract_model and "kimi-k3" in extract_model.lower()):
        import os

        os.environ.setdefault("TWA_REASONING_EFFORT", "low")

    missing_env = required_env_keys()
    if missing_env and not args.dry_run:
        print("[FAIL] Copy .env.example to .env and set: " + ", ".join(missing_env))
        return 2
    if not args.dry_run:
        status = _ensure_inputs(args)
        if status:
            return status

    scorer = None
    if not args.dry_run:
        scorer = ensure_scorer_repo(args.scorer_repo)
        if scorer is None:
            print("[FAIL] Could not find or clone scoring engines.")
            return 2
        print(f"[OK] Scorer: {scorer}")

    env = child_env()
    if scorer is not None:
        env["SCORER_REPO"] = str(scorer)

    print("=" * 60)
    print("Locked 1:1 run")
    print("=" * 60)
    print(f"Cases:    {cases_count}")
    print(f"Protocol: {args.protocol}")
    print(f"Domain:   {args.domain}")
    print(f"Builder:  {args.builder}")
    print(f"Pack:     {args.pack}")
    print(f"KG model: {kg_model}")
    print(f"Extract:  {extract_model or 'domain default'}")
    print(f"Workers:  {args.workers}")
    print("=" * 60, flush=True)

    campaign = {
        "schema_version": "locked-campaign.v1",
        "cases": cases_count,
        "protocol": args.protocol,
        "domain": args.domain,
        "builder": args.builder,
        "pack": args.pack,
        "kg_model": kg_model,
        "extract_model": extract_model,
        "workers": args.workers,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        if _want(args.domain, "main"):
            campaign["main"] = run_domain(
                args=args,
                ontology="ontosynthesis",
                scenario="mops",
                pack_root=chemistry_pack_path(args.pack),
                hashes=_hashes(main_cases),
                env=env,
                scorer=scorer,
                kg_model=kg_model,
                extract_model=extract_model,
                from_extract=args.from_extract,
            )
        if _want(args.domain, "ontomed"):
            campaign["ontomed"] = run_domain(
                args=args,
                ontology="medical",
                scenario="medical",
                pack_root=medical_pack_path(),
                hashes=_hashes(medical_cases),
                env=env,
                scorer=scorer,
                kg_model=kg_model,
                extract_model=extract_model,
                from_extract=args.from_extract_medical,
            )
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(f"[FAIL] {message}")
        return 1

    if not args.dry_run:
        path = locked_campaign_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(campaign, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] Campaign {path}")
    print_summary(campaign)
    print("[OK] Locked run finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
