"""
PDF to Markdown Conversion Step

Converts PDF files to markdown format using docling and simple_conversion.
Creates three files per PDF:
  - <name>_text.md (text extraction)
  - <name>_tables.md (table extraction)
  - <name>.md (combined)
"""

import os
import sys
import importlib.util
from typing import Optional

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from docling.document_converter import DocumentConverter
import pandas as pd


def _load_simple_conversion_module():
    """Load scripts/simple_conversion.py as a module."""
    scripts_dir = os.path.join(project_root, "scripts")
    simple_path = os.path.join(scripts_dir, "simple_conversion.py")
    
    if not os.path.exists(simple_path):
        raise ImportError(f"simple_conversion.py not found at {simple_path}")
    
    spec = importlib.util.spec_from_file_location("simple_conversion", simple_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load simple_conversion.py from {simple_path}")
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_text_md(pdf_path: str, output_folder: str) -> str:
    """Extract text from PDF to <pdf>_text.md using simple_conversion."""
    sc = _load_simple_conversion_module()
    
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise RuntimeError(
            "PyMuPDF (fitz) is required for text extraction. Install with: pip install PyMuPDF"
        ) from e

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    text_md = os.path.join(output_folder, f"{base_name}_text.md")

    parts = []
    with fitz.open(pdf_path) as doc:
        for i in range(len(doc)):
            page = doc.load_page(i)
            md = sc.page_to_markdown(page)
            if not md:
                # Fallback to raw text normalized by simple_conversion
                md = sc._norm_whitespace(page.get_text()) or ""
            parts.append(md)

    text_content = "\n\n".join(p for p in parts if p is not None)
    with open(text_md, "w", encoding="utf-8") as f:
        f.write(text_content)
    
    return text_md


def _extract_tables_md(pdf_path: str, output_folder: str) -> str:
    """Extract tables from PDF to <pdf>_tables.md using docling."""
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    tables_md = os.path.join(output_folder, f"{base_name}_tables.md")

    converter = DocumentConverter()
    res = converter.convert(pdf_path)

    lines = []
    for i, table in enumerate(res.document.tables or [], start=1):
        df = table.export_to_dataframe()
        lines.append(f"## Table {i}\n")
        lines.append(df.to_markdown(index=False))
        lines.append("")

    with open(tables_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    return tables_md


def _combine_text_and_tables(text_md: str, tables_md: Optional[str], combined_md: str) -> str:
    """Combine text and table markdown files into final combined markdown."""
    text_content = ""
    tables_content = ""
    
    if os.path.exists(text_md):
        with open(text_md, "r", encoding="utf-8") as f:
            text_content = f.read()
    
    if tables_md and os.path.exists(tables_md):
        with open(tables_md, "r", encoding="utf-8") as f:
            tables_content = f.read()

    if tables_content.strip():
        combined = f"{text_content}\n\n{tables_content}" if text_content else tables_content
    else:
        combined = text_content

    with open(combined_md, "w", encoding="utf-8") as f:
        f.write(combined)
    
    return combined_md


def convert_pdf_to_markdown(pdf_path: str, output_folder: str) -> Optional[str]:
    """
    Convert a single PDF to markdown format.
    
    Args:
        pdf_path: Path to the PDF file
        output_folder: Directory to save markdown files
        
    Returns:
        Path to combined markdown file, or None if conversion failed
    """
    try:
        print(f"  Converting {os.path.basename(pdf_path)}...")

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        combined_md = os.path.join(output_folder, f"{base_name}.md")

        # 1) Text extraction
        text_md = _extract_text_md(pdf_path, output_folder)
        print(f"    ✓ Text extracted")

        # 2) Table extraction
        tables_md = _extract_tables_md(pdf_path, output_folder)
        print(f"    ✓ Tables extracted")

        # 3) Combine
        final_md = _combine_text_and_tables(text_md, tables_md, combined_md)
        print(f"    ✓ Combined markdown created: {os.path.basename(final_md)}")
        
        return final_md

    except Exception as e:
        print(f"    ✗ Error converting {pdf_path}: {str(e)}")
        return None


def convert_doi_pdfs(doi_hash: str, data_dir: str) -> bool:
    """
    Convert PDFs for a specific DOI hash.
    
    Args:
        doi_hash: The DOI hash identifier
        data_dir: Base data directory (e.g., 'data')
        
    Returns:
        True if at least one PDF was successfully converted or already exists
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    
    if not os.path.exists(doi_folder):
        print(f"  ✗ DOI folder not found: {doi_folder}")
        return False
    
    # Files to convert
    pdf_files = [f"{doi_hash}.pdf", f"{doi_hash}_si.pdf"]
    
    success_count = 0
    skipped_count = 0
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(doi_folder, pdf_file)
        markdown_file = os.path.join(doi_folder, f"{os.path.splitext(pdf_file)[0]}.md")
        
        if not os.path.exists(pdf_path):
            # SI PDF is optional
            if "_si.pdf" in pdf_file:
                print(f"  ⏭️  SI PDF not found (optional): {pdf_file}")
                continue
            else:
                print(f"  ✗ PDF not found: {pdf_file}")
                continue
        
        # Check if markdown already exists
        if os.path.exists(markdown_file):
            print(f"  ⏭️  Markdown already exists: {os.path.basename(markdown_file)}")
            skipped_count += 1
            success_count += 1
        else:
            output_file = convert_pdf_to_markdown(pdf_path, doi_folder)
            if output_file:
                success_count += 1
            else:
                print(f"  ✗ Conversion failed: {pdf_file}")
    
    if success_count > 0:
        if skipped_count > 0:
            print(f"  ✅ PDF conversion: {success_count} files ready ({skipped_count} skipped, {success_count - skipped_count} converted)")
        else:
            print(f"  ✅ PDF conversion: {success_count} files converted")
        return True
    
    return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for PDF conversion step.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if conversion succeeded
    """
    data_dir = config.get("data_dir", "data")
    
    print(f"▶️  PDF Conversion: {doi_hash}")
    success = convert_doi_pdfs(doi_hash, data_dir)
    
    if success:
        print(f"✅ PDF Conversion completed: {doi_hash}")
    else:
        print(f"❌ PDF Conversion failed: {doi_hash}")
    
    return success


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        run_step(test_hash, test_config)
    else:
        print("Usage: python convert.py <doi_hash>")

