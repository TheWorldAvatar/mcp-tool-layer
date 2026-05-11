"""
Render a compact Markdown scorecard (tables like `medical_scores_report.md`) from
`medical_case_scoring_report.json`.

  python scripts/render_medical_scores_tables_md.py \\
    --json data_medical_e2e_json_full/evaluation_results/medical_case_scoring_report.json \\
    --output data_medical_e2e_json_full/evaluation_results/medical_scores_report.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


# Display order and labels (matches historic report layout; Fall-Nr from test fixtures).
_CASE_ORDER: list[tuple[str, str, str]] = [
    ("4dd7b3a0", "Peter_Lustig", "345678901"),
    ("ce49a454", "Claudia_Meyer", "234567890"),
    ("d2b47254", "Dog_Snoopy", "567890123"),
    ("eb7ead0d", "Gustav_Gans", "456789012"),
    ("ec5d5219", "Hans_Mueller", "123456789"),
]


def _pct(x: float) -> str:
    if math.isclose(x, 1.0) or math.isclose(x, 0.0):
        return f"{x * 100:.0f}%"
    if abs(x * 100 - round(x * 100, 1)) < 0.001:
        return f"{x * 100:.1f}%"
    return f"{x * 100:.2f}%"


def render(report: dict) -> str:
    by_hash = {row["hash"]: row for row in report["per_case_summary"]}
    pred_path = report.get("pred_path") or "data_medical_e2e_json_full/predicted_all.csv"
    lines: list[str] = [
        "# Medical structured extraction — accuracy report",
        "",
        "**Ground truth:** `evaluation/medical/medical_cases_latest.csv`  ",
        f"**Predicted:** `{pred_path}`  ",
        "**Scoring:** `scripts/medical_score_predicted_vs_gold.py` (row match on `_doi_hash`, 79 clinical columns per case)",
        "",
        "---",
        "",
        "## Accuracy per case",
        "",
        "| Case | Correct | Total | Accuracy |",
        "|:----|--------:|------:|-----------:|",
    ]

    for h, label, fall in _CASE_ORDER:
        row = by_hash.get(h, {})
        c = row.get("correct", 0)
        t = row.get("total", 79)
        acc = row.get("accuracy", 0.0)
        case_cell = f"{label} (Fall …{fall})"
        bold = c < t or acc < 1.0
        acc_s = _pct(acc)
        if bold:
            lines.append(f"| {case_cell} | **{c}** | **{t}** | **{acc_s}** |")
        else:
            lines.append(f"| {case_cell} | {c} | {t} | {acc_s} |")

    tot_c = report.get("total_correct", 0)
    tot_t = report.get("total_cells", 0)
    oa = report.get("overall_accuracy", 0.0)
    oa_s = _pct(oa)
    lines.append(f"| **All** | **{tot_c}** | **{tot_t}** | **{oa_s}** |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Columns below 100% (only)",
            "",
            "| Column | Correct / Total | Column accuracy |",
            "|:-------|:----------------|:----------------|",
        ]
    )

    per_col = report.get("per_column") or {}
    low: list[tuple[str, dict]] = [(k, v) for k, v in per_col.items() if (v.get("accuracy") or 1.0) < 1.0]
    low.sort(key=lambda kv: (kv[1].get("accuracy") or 0, kv[0]))

    if not low:
        lines.append("| *(none)* | — | — |")
    else:
        for name, st in low:
            c, t = st.get("correct", 0), st.get("total", 0)
            acc = st.get("accuracy", 0.0)
            lines.append(f"| {name} | {c} / {t} | **{_pct(acc)}** |")

    n_col = report.get("n_columns", 79)
    lines.append("")
    lines.append("---")
    lines.append("")
    if low:
        rest = n_col - len(low)
        lines.append(f"*(All **other {rest} columns** at **100%** across five cases.)*")
    else:
        lines.append(f"*(All **{n_col} columns** at **100%** across five cases.)*")

    lines.extend(
        [
            "",
            "### Reference",
            "",
            "| Detail | Location |",
            "|--------|----------|",
            "| Full JSON metrics | `medical_case_scoring_report.json` |",
            "| Long-form report | `medical_case_scoring_report.md` |",
            "",
            "Regenerate: run `medical_ttl_to_csv_sparql.py`, then `medical_score_predicted_vs_gold.py`, then this script.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render medical_scores_report.md from scoring JSON.")
    ap.add_argument("--json", type=Path, required=True, help="Path to medical_case_scoring_report.json")
    ap.add_argument("--output", type=Path, required=True, help="Markdown output path")
    args = ap.parse_args(list(argv) if argv is not None else None)

    data = json.loads(args.json.read_text(encoding="utf-8"))
    md = render(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(f"[OK] Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
