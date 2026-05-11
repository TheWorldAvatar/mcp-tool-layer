#!/usr/bin/env python3
"""
Verify local PDF inputs for chemistry (OntoSynthesis / MOPs) and medical E2E runs.

Uses the repo’s existing gitignored folders:

- ``raw_data_mop/`` — chemistry evaluation PDFs (e.g. Angew. Chem. + ACS AMI pairs).
- ``raw_data/`` — shared bucket that typically includes the same ACS AMI pair plus
  medical operative reports ``OP Bericht 1.pdf`` … ``OP Bericht 5.pdf``.

No PDFs are copied or overwritten unless you pass ``--write-placeholders`` (only
fills **missing** paths for empty-machine smoke tests; requires PyMuPDF).

Suggested runs (outputs remain gitignored: ``data/``, ``data_medical_e2e/``, …):

  python generic_main.py --config configs/pipeline.json \\
      --input-dir raw_data_mop --hash 0c57bac8

  python generic_main.py --config configs/pipeline_medical_e2e_local_inputs.json \\
      --input-dir raw_data --hash ec5d5219

Run:

  python scripts/prepare_e2e_pdf_inputs.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CHEM_DOI_STEM = "10.1021_acsami.7b18836"
CHEM_HASH = "0c57bac8"
MEDICAL_TITLE = "OP Bericht 1"


def _write_placeholder_pdf(path: Path, lines: list[str]) -> None:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "PyMuPDF (import fitz) is required for --write-placeholders. "
            "Install with: pip install pymupdf"
        ) from e

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=10)
        y += 14
    doc.save(path)
    doc.close()


def _maybe_copy_si_from_data_tree(raw_dir: Path) -> bool:
    si_src = ROOT / "data" / CHEM_HASH / f"{CHEM_HASH}_si.pdf"
    si_dst = raw_dir / f"{CHEM_DOI_STEM}_si.pdf"
    if not si_src.is_file():
        return False
    if si_dst.is_file():
        return True
    shutil.copy2(si_src, si_dst)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-placeholders",
        action="store_true",
        help="Only if missing: write tiny placeholder PDFs (requires PyMuPDF). Never overwrites existing files.",
    )
    args = parser.parse_args()

    raw_data = ROOT / "raw_data"
    raw_mop = ROOT / "raw_data_mop"

    chem_primary = raw_mop / f"{CHEM_DOI_STEM}.pdf"
    chem_si_mop = raw_mop / f"{CHEM_DOI_STEM}_si.pdf"
    med_main = raw_data / f"{MEDICAL_TITLE}.pdf"

    print(f"[check] Chemistry PDF dir: {raw_mop}")
    if chem_primary.is_file():
        print(f"  [ok] {chem_primary.name}")
    else:
        print(f"  [missing] {chem_primary.name} (needed for hash {CHEM_HASH} via {raw_mop})")

    if chem_si_mop.is_file():
        print(f"  [ok] {chem_si_mop.name}")
    else:
        print(f"  [optional] {chem_si_mop.name} (SI — optional for pipeline)")

    # Same stem often duplicated under raw_data for mixed runs
    chem_rd = raw_data / f"{CHEM_DOI_STEM}.pdf"
    if chem_rd.is_file():
        print(f"[check] Also present under raw_data: {chem_rd.name}")

    print(f"[check] Medical PDF dir: {raw_data}")
    if med_main.is_file():
        print(f"  [ok] {med_main.name} (hash ec5d5219)")
    else:
        print(f"  [missing] {med_main.name}")

    if args.write_placeholders:
        raw_data.mkdir(parents=True, exist_ok=True)
        raw_mop.mkdir(parents=True, exist_ok=True)
        chem_lines = [
            "Placeholder PDF (empty clone / CI only).",
            f"Replace with real PDF for {CHEM_DOI_STEM}.",
        ]
        med_lines = [
            "Placeholder PDF (empty clone / CI only).",
            f"Replace with real «{MEDICAL_TITLE}».",
        ]
        if not chem_primary.exists():
            _write_placeholder_pdf(chem_primary, chem_lines)
            print(f"[ok] Wrote placeholder: {chem_primary}")
        if not med_main.exists():
            _write_placeholder_pdf(med_main, med_lines)
            print(f"[ok] Wrote placeholder: {med_main}")

    if _maybe_copy_si_from_data_tree(raw_mop):
        print(f"[ok] Chemistry SI present at: {chem_si_mop}")
    elif _maybe_copy_si_from_data_tree(raw_data):
        print(f"[ok] Chemistry SI present under raw_data: {raw_data / (CHEM_DOI_STEM + '_si.pdf')}")

    print()
    print("Suggested commands (set TWA_GENERATED_ARTIFACT_ROOT so prompts/sparql load from that bundle):")
    print(
        '  PowerShell:  $env:TWA_GENERATED_ARTIFACT_ROOT="ai_generated_contents_pipeline_bundle"'
    )
    print(
        f"  python generic_main.py --config configs/pipeline.json "
        f"--input-dir raw_data_mop --hash {CHEM_HASH}"
    )
    print(
        "  python generic_main.py --config configs/pipeline_medical_e2e_local_inputs.json "
        "--input-dir raw_data --hash ec5d5219"
    )

    ok = chem_primary.is_file() and med_main.is_file()
    if not ok:
        print("\n[warn] Some expected PDFs are missing; fix paths above or use --write-placeholders.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
