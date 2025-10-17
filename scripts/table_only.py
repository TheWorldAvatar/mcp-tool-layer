#!/usr/bin/env python3
from pathlib import Path
import sys

import pandas as pd  # pip install pandas tabulate
from docling.document_converter import DocumentConverter  # pip install docling

# --------- hardcoded paths ---------
PDF_PATH   = Path("scripts/data/d5ff239e.pdf")
OUTPUT_MD  = Path("scripts/data/d5ff239e_tables.md")

def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDF not found: {PDF_PATH}", file=sys.stderr)
        return 2

    conv = DocumentConverter()
    res = conv.convert(PDF_PATH)

    lines = []
    for i, table in enumerate(res.document.tables, start=1):
        df = table.export_to_dataframe()
        lines.append(f"## Table {i}\n")
        lines.append(df.to_markdown(index=False))
        lines.append("")  # blank line

    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT_MD}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
