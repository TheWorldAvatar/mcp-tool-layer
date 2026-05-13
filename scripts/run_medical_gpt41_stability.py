"""Run repeated downstream medical extraction trials and compare stability."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data_medical_nonflat_one_iter_vision5_20260510"
INPUT_DIR = ROOT / "raw_data_medical_nonflat_one_iter_vision5_20260510"
PIPELINE_CONFIG = ROOT / "configs" / "pipeline_medical_nonflat_one_iter_vision5_downstream_20260510.json"
ARTIFACT_ROOT = "ai_generated_contents_agent_candidate_json_medical_nonflat_one_iter_20260510"
GOLD_CSV = ROOT / "evaluation" / "medical" / "medical_cases_latest.csv"
SCHEMA_TTL = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v3.ttl"


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print("[run]", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def _reset_downstream_artifacts(hashes: list[str] | None) -> None:
    candidates = [DATA_DIR / h for h in hashes] if hashes else [p for p in DATA_DIR.iterdir() if p.is_dir()]
    for doi_dir in candidates:
        mcp_run = doi_dir / "mcp_run"
        if mcp_run.exists():
            for hint in mcp_run.glob("iter2_hints_*.txt"):
                hint.unlink(missing_ok=True)
        for marker in (".main_ontology_extractions_done", ".main_kg_building_done"):
            (doi_dir / marker).unlink(missing_ok=True)


def _load_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["_doi_hash"]: row for row in csv.DictReader(f)}


def _mismatches(pred_csv: Path) -> list[dict[str, str]]:
    gold = _load_rows(GOLD_CSV)
    pred = _load_rows(pred_csv)
    first = next(iter(gold.values()))
    cols = [c for c in first if c not in {"_doi_hash", "_ttl_file"}]
    rows: list[dict[str, str]] = []
    for h in sorted(set(gold) & set(pred)):
        for col in cols:
            gold_value = (gold[h].get(col) or "").strip()
            pred_value = (pred[h].get(col) or "").strip()
            if gold_value != pred_value:
                rows.append(
                    {
                        "hash": h,
                        "column": col,
                        "gold": gold_value,
                        "pred": pred_value,
                    }
                )
    return rows


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_and_archive(out_root: Path, trial: int) -> dict:
    predicted = DATA_DIR / "predicted_all.csv"
    eval_dir = DATA_DIR / "evaluation_results"
    strict_json = eval_dir / "medical_case_scoring_report.json"
    strict_md = eval_dir / "medical_case_scoring_report.md"
    relaxed_json = eval_dir / "medical_case_scoring_report_relaxed.json"
    relaxed_md = eval_dir / "medical_case_scoring_report_relaxed.md"

    _run(
        [
            sys.executable,
            "scripts/medical_ttl_to_csv_sparql.py",
            "--data-dir",
            str(DATA_DIR.relative_to(ROOT)),
            "--output",
            str(predicted.relative_to(ROOT)),
            "--reference-csv",
            str(GOLD_CSV.relative_to(ROOT)),
            "--reference-csv-header-row",
            "0",
            "--schema-ttl",
            str(SCHEMA_TTL.relative_to(ROOT)),
        ]
    )
    _run(
        [
            sys.executable,
            "scripts/medical_score_predicted_vs_gold.py",
            "--gold",
            str(GOLD_CSV.relative_to(ROOT)),
            "--pred",
            str(predicted.relative_to(ROOT)),
            "--out-json",
            str(strict_json.relative_to(ROOT)),
            "--out-md",
            str(strict_md.relative_to(ROOT)),
        ]
    )
    _run(
        [
            sys.executable,
            "scripts/medical_score_predicted_vs_gold.py",
            "--gold",
            str(GOLD_CSV.relative_to(ROOT)),
            "--pred",
            str(predicted.relative_to(ROOT)),
            "--relaxed-free-text",
            "--out-json",
            str(relaxed_json.relative_to(ROOT)),
            "--out-md",
            str(relaxed_md.relative_to(ROOT)),
        ]
    )

    trial_dir = out_root / f"run_{trial:02d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    for src in (predicted, strict_json, strict_md, relaxed_json, relaxed_md):
        shutil.copy2(src, trial_dir / src.name)

    mismatches = _mismatches(predicted)
    (trial_dir / "mismatches.json").write_text(
        json.dumps(mismatches, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    strict = _read_json(strict_json)
    relaxed = _read_json(relaxed_json)
    return {
        "trial": trial,
        "strict_accuracy": strict["overall_accuracy"],
        "strict_correct": strict["total_correct"],
        "relaxed_accuracy": relaxed["overall_accuracy"],
        "relaxed_correct": relaxed["total_correct"],
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--hash", action="append", dest="hashes")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = args.output_root or DATA_DIR / "evaluation_results" / f"gpt41_stability_{stamp}"
    out_root = (ROOT / out_root).resolve() if not out_root.is_absolute() else out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    summaries: list[dict] = []
    env = os.environ.copy()
    env["TWA_GENERATED_ARTIFACT_ROOT"] = ARTIFACT_ROOT
    for trial in range(1, args.trials + 1):
        print(f"\n=== GPT-4.1 stability trial {trial}/{args.trials} ===", flush=True)
        _reset_downstream_artifacts(args.hashes)
        cmd = [
            sys.executable,
            "generic_main.py",
            "--config",
            str(PIPELINE_CONFIG.relative_to(ROOT)),
            "--input-dir",
            str(INPUT_DIR.relative_to(ROOT)),
        ]
        for h in args.hashes or []:
            cmd.extend(["--hash", h])
        _run(cmd, env=env)
        summary = _score_and_archive(out_root, trial)
        summaries.append(summary)
        print(
            f"[trial {trial}] strict={summary['strict_correct']}/395 "
            f"relaxed={summary['relaxed_correct']}/395 mismatches={summary['mismatch_count']}",
            flush=True,
        )

    summary_path = out_root / "summary.json"
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        display_path = summary_path.relative_to(ROOT)
    except ValueError:
        display_path = summary_path
    print(f"\n[OK] Stability summary written to {display_path}")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
