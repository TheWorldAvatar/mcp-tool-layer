"""File operation utilities"""

import os
import shutil


def write_paper_doi_files(doi_folder: str, doi: str) -> None:
    """Write per-case bibliographic DOI files for CCDC / prompt injection.

    ``paper_doi.txt`` holds slash form (e.g. ``10.1039/C6DT02764D``).
    ``paper_doi_key.txt`` holds pipeline underscore form.
    Document hash remains the folder name; these files are for CCDC only.
    """
    slash = (doi or "").strip().replace("_", "/")
    if not slash:
        return
    underscore = slash.replace("/", "_")
    os.makedirs(doi_folder, exist_ok=True)
    paper_doi_path = os.path.join(doi_folder, "paper_doi.txt")
    paper_key_path = os.path.join(doi_folder, "paper_doi_key.txt")
    with open(paper_doi_path, "w", encoding="utf-8") as f:
        f.write(slash + "\n")
    with open(paper_key_path, "w", encoding="utf-8") as f:
        f.write(underscore + "\n")


def read_paper_doi(doi_folder: str) -> str | None:
    """Return slash-form paper DOI from ``paper_doi.txt`` if present."""
    path = os.path.join(doi_folder, "paper_doi.txt")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip().replace("_", "/")
        return value or None
    except OSError:
        return None


def copy_pdfs_to_data_dir(doi: str, doi_hash: str, input_dir: str, data_dir: str = "data") -> bool:
    """
    Copy PDF files from input directory to DOI-specific data directory.
    
    Args:
        doi: Original DOI string
        doi_hash: Hash of the DOI
        input_dir: Source directory containing PDFs
        data_dir: Base data directory
        
    Returns:
        True if main PDF was copied successfully
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    os.makedirs(doi_folder, exist_ok=True)
    write_paper_doi_files(doi_folder, doi)
    
    # Copy main PDF
    src_pdf = os.path.join(input_dir, f"{doi}.pdf")
    dst_pdf = os.path.join(doi_folder, f"{doi_hash}.pdf")
    
    if not os.path.exists(src_pdf):
        print(f"  ✗ Main PDF not found: {src_pdf}")
        return False
    
    if not os.path.exists(dst_pdf):
        shutil.copy2(src_pdf, dst_pdf)
        print(f"  ✓ Copied: {doi}.pdf -> {doi_hash}.pdf")
    else:
        print(f"  ⏭️  PDF already exists: {doi_hash}.pdf")
    
    # Copy SI PDF (optional)
    src_si_pdf = os.path.join(input_dir, f"{doi}_si.pdf")
    dst_si_pdf = os.path.join(doi_folder, f"{doi_hash}_si.pdf")
    
    if os.path.exists(src_si_pdf):
        if not os.path.exists(dst_si_pdf):
            shutil.copy2(src_si_pdf, dst_si_pdf)
            print(f"  ✓ Copied: {doi}_si.pdf -> {doi_hash}_si.pdf")
        else:
            print(f"  ⏭️  SI PDF already exists: {doi_hash}_si.pdf")
    
    return True

