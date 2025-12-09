"""File operation utilities"""

import os
import shutil


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

