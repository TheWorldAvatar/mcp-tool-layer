#!/usr/bin/env python3
"""Convert raw_data_new_medical/Ground truth.xlsx to evaluation CSV for the new PDFs."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipelines.utils import generate_hash  # noqa: E402


def _discover_pdf_stems(pdf_dir: Path) -> list[str]:
    """Return OPR stems sorted by numeric suffix (OPR1a..OPR30a)."""

    def _key(stem: str) -> tuple[int, str]:
        m = re.search(r"OPR(\d+)", stem, re.I)
        return (int(m.group(1)) if m else 10**9, stem)

    return sorted((p.stem for p in pdf_dir.glob("*.pdf")), key=_key)


def _load_ground_truth_rows(xlsx_path: Path, n_cases: int) -> tuple[list[str], list[dict]]:
    raw = pd.read_excel(xlsx_path, sheet_name=0, header=None)
    headers = _header_aliases([str(x).strip() if pd.notna(x) else f"col_{i}" for i, x in enumerate(raw.iloc[1].tolist())])
    data = raw.iloc[2 : 2 + n_cases].copy()
    data.columns = headers
    data = data.reset_index(drop=True)
    rows = []
    for _, row in data.iterrows():
        rows.append({str(k): ("" if pd.isna(v) else str(v).strip()) for k, v in row.items()})
    return headers, rows


def _format_cell(col: str, value: str) -> str:
    if value in ("", "nan", "None"):
        return "-"
    if col in {"Geburtsdatum", "OP-Datum", "OP-Datum ", "Entlassdatum", "präop TuKo", "präop TuKo "}:
        date_part = value.split(" ")[0]
        parts = date_part.split("-")
        if len(parts) == 3 and len(parts[0]) == 4:
            y, m, d = parts
            return f"{int(d):02d}.{int(m):02d}.{y}"
        parts = date_part.split(".")
        if len(parts) == 3:
            return date_part
    if value in ("1.0", "1"):
        return "1"
    if value in ("0.0", "0"):
        return "-"
    return value


def _header_aliases(headers: list[str]) -> list[str]:
    """Disambiguate duplicate xlsx headers (e.g. two 'sonst.' columns)."""
    counts: dict[str, int] = {}
    out: list[str] = []
    for h in headers:
        key = str(h).strip() if pd.notna(h) else ""
        if key == "sonst." or key.startswith("sonst."):
            counts["sonst."] = counts.get("sonst.", 0) + 1
            out.append("sonst. (Eingriff)" if counts["sonst."] == 1 else "sonst. (Diagnose)")
        else:
            out.append(key)
    return out


def _parse_de_date(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text or text == "-":
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.split(" ")[0], fmt)
        except ValueError:
            continue
    return None


def _completed_years(birth: datetime, on_date: datetime) -> int:
    years = on_date.year - birth.year
    if (on_date.month, on_date.day) < (birth.month, birth.day):
        years -= 1
    return years


def _surname_only(value: str) -> str:
    text = (value or "").strip()
    if not text or text == "-":
        return "-"
    # Keep last whitespace-separated token (xlsx often stores full names).
    return text.split()[-1]


def _apply_schema_corrections(row: dict[str, str]) -> dict[str, str]:
    """Deterministic schema-level fixes (not clinical judgment)."""
    out = dict(row)
    fall = (out.get("Fall-Nr") or "").strip()
    if fall.isdigit() and len(fall) < 9:
        out["Fall-Nr"] = fall.zfill(9)
    for col in ("Operateur/in", "Assistent/in"):
        out[col] = _surname_only(out.get(col, "-"))
    birth = _parse_de_date(out.get("Geburtsdatum", ""))
    op = _parse_de_date(out.get("OP-Datum", "") or out.get("OP-Datum ", ""))
    if birth and op:
        out["Alter"] = str(_completed_years(birth, op))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xlsx",
        type=Path,
        default=ROOT / "raw_data_new_medical" / "Ground truth.xlsx",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=ROOT / "raw_data_new_medical",
        help="Directory of OPR PDFs used for hash mapping",
    )
    parser.add_argument(
        "--n-cases",
        type=int,
        default=0,
        help="Number of xlsx data rows / PDFs to convert (0 = all discovered PDFs)",
    )
    parser.add_argument(
        "--reference-csv",
        type=Path,
        default=ROOT / "evaluation" / "medical" / "medical_cases_latest.csv",
        help="Use column order from existing gold CSV",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation" / "medical" / "medical_cases_new_20260710_all30.csv",
    )
    parser.add_argument(
        "--corrected-output",
        type=Path,
        default=ROOT / "evaluation" / "medical" / "medical_cases_new_20260710_all30_corrected.csv",
        help="Also write schema-corrected gold (surname-only team, derived age, Fall-Nr pad)",
    )
    args = parser.parse_args()

    stems = _discover_pdf_stems(args.pdf_dir)
    if args.n_cases and args.n_cases > 0:
        stems = stems[: args.n_cases]
    if not stems:
        raise SystemExit(f"No PDFs found under {args.pdf_dir}")

    ref_text = args.reference_csv.read_text(encoding="utf-8-sig")
    ref_headers = next(csv.reader(ref_text.splitlines()))
    clinical_headers = [h for h in ref_headers if h not in {"_ttl_file", "_doi_hash"}]

    _, gt_rows = _load_ground_truth_rows(args.xlsx, len(stems))
    if len(gt_rows) != len(stems):
        raise SystemExit(f"xlsx rows ({len(gt_rows)}) != PDF stems ({len(stems)})")

    def _write(path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ref_headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    raw_out_rows: list[dict[str, str]] = []
    corr_out_rows: list[dict[str, str]] = []
    for stem, gt in zip(stems, gt_rows):
        doi_hash = generate_hash(stem)
        out = {"_ttl_file": f"{stem}.ttl", "_doi_hash": doi_hash}
        for col in clinical_headers:
            raw_val = gt.get(col, gt.get(col.rstrip(), ""))
            out[col] = _format_cell(col, raw_val)
        raw_out_rows.append(out)
        corr_out_rows.append(_apply_schema_corrections(out))

    _write(args.output, raw_out_rows)
    _write(args.corrected_output, corr_out_rows)

    print(f"Wrote {len(stems)} rows to {args.output}")
    print(f"Wrote {len(stems)} corrected rows to {args.corrected_output}")
    for stem in stems:
        print(f"  {stem}.pdf -> {generate_hash(stem)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
