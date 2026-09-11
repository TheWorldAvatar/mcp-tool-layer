"""Discover PDF stems, mint 8-char hashes, and copy PDFs into the runtime."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.extraction_runtime.names import generate_hash


def write_paper_doi_files(doi_folder: str | Path, doi: str) -> None:
    slash = (doi or "").strip().replace("_", "/")
    if not slash:
        return
    folder = Path(doi_folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "paper_doi.txt").write_text(slash + "\n", encoding="utf-8")
    (folder / "paper_doi_key.txt").write_text(
        slash.replace("/", "_") + "\n", encoding="utf-8"
    )


def read_paper_doi(doi_folder: str | Path) -> str | None:
    path = Path(doi_folder) / "paper_doi.txt"
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip().replace("_", "/")
    return value or None


def discover_dois(input_dir: str | Path, data_dir: str | Path) -> dict[str, str]:
    source = Path(input_dir)
    runtime = Path(data_dir)
    if not source.is_dir():
        print(f"[FAIL] Input directory not found: {source}")
        return {}

    pdf_files = sorted(
        (
            item.name
            for item in source.iterdir()
            if item.is_file()
            and item.suffix.lower() == ".pdf"
            and not item.name.lower().endswith("_si.pdf")
        ),
        key=str.lower,
    )
    if not pdf_files:
        print(f"[FAIL] No PDF files found in {source}")
        return {}

    mapping_path = runtime / "doi_to_hash.json"
    existing: dict[str, str] = {}
    if mapping_path.is_file():
        loaded = json.loads(mapping_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = {str(key): str(value) for key, value in loaded.items()}

    mapping = dict(existing)
    current: dict[str, str] = {}
    for pdf_file in pdf_files:
        doi = pdf_file[:-4]
        doi_hash = generate_hash(doi)
        mapping[doi] = doi_hash
        current[doi] = doi_hash
        print(f"  {doi} -> {doi_hash}")
        write_paper_doi_files(runtime / doi_hash, doi)

    runtime.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] Saved DOI mapping ({len(current)} current) to {mapping_path}")
    return current


def copy_pdfs_to_data_dir(
    doi: str,
    doi_hash: str,
    input_dir: str | Path,
    data_dir: str | Path,
) -> bool:
    source = Path(input_dir)
    folder = Path(data_dir) / doi_hash
    folder.mkdir(parents=True, exist_ok=True)
    write_paper_doi_files(folder, doi)

    src_pdf = source / f"{doi}.pdf"
    dst_pdf = folder / f"{doi_hash}.pdf"
    if not src_pdf.is_file():
        print(f"  [FAIL] Main PDF not found: {src_pdf}")
        return False
    if not dst_pdf.exists():
        shutil.copy2(src_pdf, dst_pdf)
        print(f"  [OK] Copied {src_pdf.name} -> {dst_pdf.name}")

    src_si = source / f"{doi}_si.pdf"
    dst_si = folder / f"{doi_hash}_si.pdf"
    if src_si.is_file() and not dst_si.exists():
        shutil.copy2(src_si, dst_si)
        print(f"  [OK] Copied {src_si.name} -> {dst_si.name}")
    return True
