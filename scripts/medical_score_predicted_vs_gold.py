"""
Compare a predicted medical pipeline CSV (from `medical_ttl_to_csv_sparql.py`) to a gold CSV.

Rows are aligned on `_doi_hash` (required). Column sets are intersected (excluding `_ttl_file`).

Example:

  python scripts/medical_ttl_to_csv_sparql.py --data-dir data_medical_e2e_json_full \\
    --output data_medical_e2e_json_full/predicted_all.csv

  python scripts/medical_score_predicted_vs_gold.py \\
    --gold medical_cases_agentic_valid.csv \\
    --pred data_medical_e2e_json_full/predicted_all.csv \\
    --out-json data_medical_e2e_json_full/evaluation_results/medical_case_scoring_report.json \\
    --out-md data_medical_e2e_json_full/evaluation_results/medical_case_scoring_report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path


META_FIELDS = {"_ttl_file", "_doi_hash"}


def _norm(s: str | None) -> str:
    t = (s or "").strip()
    if t == "-":
        return ""
    return t


def _norm_relaxed_free_text(s: str | None) -> str:
    t = _norm(s).casefold()
    for token in (" rechts", " links", " beidseits", " beidseitig"):
        t = t.replace(token, "")
    return " ".join(t.replace(",", " ").split())


def _is_relaxed_free_text_col(col: str) -> bool:
    return col.casefold().startswith("sonst.")


def _same_cell(gold: str, pred: str, col: str, *, relaxed_free_text: bool) -> bool:
    if _norm(gold) == _norm(pred):
        return True
    if not relaxed_free_text or not _is_relaxed_free_text_col(col):
        return False

    g = _norm_relaxed_free_text(gold)
    p = _norm_relaxed_free_text(pred)
    if not g or not p:
        return g == p
    if g == p or g in p or p in g:
        return True
    return SequenceMatcher(None, g, p).ratio() >= 0.82


def _load_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        return [], []
    reader = csv.DictReader(lines)
    fieldnames = list(reader.fieldnames or [])
    rows = []
    for row in reader:
        if not any((v or "").strip() for k, v in row.items() if k not in META_FIELDS):
            continue
        rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return fieldnames, rows


def _by_hash(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        h = (row.get("_doi_hash") or "").strip()
        if h:
            out[h] = row
    return out


def run_score(gold_path: Path, pred_path: Path, *, relaxed_free_text: bool = False) -> dict:
    _, gold_rows = _load_csv_rows(gold_path)
    _, pred_rows = _load_csv_rows(pred_path)
    gold_by_h = _by_hash(gold_rows)
    pred_by_h = _by_hash(pred_rows)

    common_hashes = sorted(set(gold_by_h) & set(pred_by_h))
    if not common_hashes:
        raise SystemExit(
            f"No overlapping _doi_hash between {gold_path} and {pred_path}. "
            f"Gold hashes: {sorted(gold_by_h)}, pred hashes: {sorted(pred_by_h)}"
        )

    sample = gold_by_h[common_hashes[0]]
    field_cols = [k for k in sample if k not in META_FIELDS]
    # Restrict to columns that exist in prediction header too
    pred0 = pred_by_h[common_hashes[0]]
    field_cols = [c for c in field_cols if c in pred0]

    per_case: list[dict] = []
    per_column: dict[str, dict[str, int | float]] = {}
    total_correct = 0
    total_cells = 0

    for col in field_cols:
        per_column[col] = {"correct": 0, "total": 0, "accuracy": 0.0}

    for h in common_hashes:
        g, pr = gold_by_h[h], pred_by_h[h]
        case_correct = 0
        case_total = 0
        for col in field_cols:
            same = _same_cell(
                g.get(col, ""),
                pr.get(col, ""),
                col,
                relaxed_free_text=relaxed_free_text,
            )
            if same:
                case_correct += 1
                per_column[col]["correct"] += 1
            case_total += 1
            per_column[col]["total"] += 1
        total_correct += case_correct
        total_cells += case_total
        acc = (case_correct / case_total) if case_total else 0.0
        ttl_name = (pr.get("_ttl_file") or g.get("_ttl_file") or "").strip() or h
        per_case.append(
            {
                "case": ttl_name,
                "hash": h,
                "correct": case_correct,
                "total": case_total,
                "accuracy": acc,
            }
        )

    n_cases = len(common_hashes)
    n_columns = len(field_cols)
    overall = (total_correct / total_cells) if total_cells else 0.0
    mean_case = sum(c["accuracy"] for c in per_case) / n_cases if n_cases else 0.0

    for col, stats in per_column.items():
        t = stats["total"]
        c = stats["correct"]
        stats["accuracy"] = (c / t) if t else 0.0

    return {
        "overall_accuracy": overall,
        "mean_per_case_accuracy": mean_case,
        "total_correct": total_correct,
        "total_cells": total_cells,
        "n_cases": n_cases,
        "n_columns": n_columns,
        "per_case_summary": [{"case": x["case"], "hash": x["hash"], "accuracy": x["accuracy"], "correct": x["correct"], "total": x["total"]} for x in per_case],
        "per_column": {k: {"correct": v["correct"], "total": v["total"], "accuracy": v["accuracy"]} for k, v in per_column.items()},
        "gold_path": str(gold_path),
        "pred_path": str(pred_path),
        "hashes_scored": common_hashes,
        "relaxed_free_text": relaxed_free_text,
    }


def _write_md(report: dict, path: Path) -> None:
    lines = [
        "# Medical Case CSV Scoring",
        "",
        f"- **Ground Truth**: `{report['gold_path']}`",
        f"- **Predicted**: `{report['pred_path']}`",
        "",
        "## Overall",
        "",
        f"- **Overall Accuracy**: {report['overall_accuracy']:.2%} ({report['total_correct']} / {report['total_cells']} cells)",
        f"- **Mean Per-Case Accuracy**: {report['mean_per_case_accuracy']:.2%}",
        f"- **Cases (matched on _doi_hash)**: {report['n_cases']} | **Columns**: {report['n_columns']}",
        "",
        "## Per-Case",
        "",
        "| Case | Hash | Correct | Total | Accuracy |",
        "|------|------|---------|-------|----------|",
    ]
    for row in report["per_case_summary"]:
        lines.append(
            f"| {row['case']} | `{row['hash']}` | {row['correct']} | {row['total']} | {row['accuracy']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Per-Column (lowest accuracy first)",
            "",
            "| Column | Correct | Total | Accuracy |",
            "|--------|---------|-------|----------|",
        ]
    )
    cols_sorted = sorted(
        report["per_column"].items(),
        key=lambda kv: (kv[1]["accuracy"], kv[0]),
    )
    for name, st in cols_sorted:
        lines.append(f"| {name} | {st['correct']} | {st['total']} | {st['accuracy']:.1%} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score predicted medical CSV vs gold CSV (keyed by _doi_hash).")
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--out-md", type=Path, default=None)
    ap.add_argument(
        "--relaxed-free-text",
        action="store_true",
        help="Relax comparison for free-text sonst.* columns: ignore side words and near-identical wording.",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    report = run_score(args.gold, args.pred, relaxed_free_text=args.relaxed_free_text)
    print(json.dumps({k: report[k] for k in report if k != "per_column"}, indent=2))
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] Wrote {args.out_json}", file=sys.stderr)
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        _write_md(report, args.out_md)
        print(f"[OK] Wrote {args.out_md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
