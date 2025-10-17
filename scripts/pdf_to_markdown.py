from docling.document_converter import DocumentConverter
import os
import sys
import importlib.util
from typing import Optional

import pandas as pd  # used for table markdown export
from models.locations import DATA_DIR 

def _load_simple_conversion_module():
    """Load scripts/simple_conversion.py as a module regardless of package layout."""
    here = os.path.dirname(__file__)
    simple_path = os.path.join(here, "simple_conversion.py")
    spec = importlib.util.spec_from_file_location("simple_conversion", simple_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load simple_conversion.py from {simple_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_text_md(pdf_path: str, output_folder: str) -> str:
    """Use simple_conversion to extract text to <pdf>_text.md and return its path."""
    # Defer heavy imports to runtime to keep CLI snappy
    sc = _load_simple_conversion_module()
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise RuntimeError(
            "PyMuPDF (fitz) is required by simple_conversion for text extraction"
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
    """Use docling (table_only logic) to extract tables to <pdf>_tables.md and return its path."""
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
    """Combine text and table markdown files into the final combined markdown."""
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


def convert_pdf_to_markdown(pdf_path, output_folder):
    """Convert a PDF by extracting text and tables, then combine into a single markdown.

    Creates three files in output_folder:
      - <pdf>_text.md
      - <pdf>_tables.md
      - <pdf>.md (combined)
    """
    try:
        print(f"Converting {pdf_path}...")

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        combined_md = os.path.join(output_folder, f"{base_name}.md")

        # 1) Text extraction via simple_conversion
        text_md = _extract_text_md(pdf_path, output_folder)
        print(f"OK Text extracted to {text_md}")

        # 2) Table extraction via docling (table_only logic)
        tables_md = _extract_tables_md(pdf_path, output_folder)
        print(f"OK Tables extracted to {tables_md}")

        # 3) Combine
        final_md = _combine_text_and_tables(text_md, tables_md, combined_md)
        print(f"Successfully combined markdown at {final_md}")
        return final_md

    except Exception as e:
        print(f"Error converting {pdf_path}: {str(e)}")
        return None

def convert_doi_pdfs(doi: str):
    """Convert PDFs for a specific DOI using the new file path convention."""
    # Define the data folder path
    data_folder = os.path.join(DATA_DIR, doi)
    
    # Check if data folder exists
    if not os.path.exists(data_folder):
        print(f"X Data folder not found: {data_folder}")
        return False
    
    # List of PDF files to convert for this DOI
    pdf_files = [f"{doi}.pdf", f"{doi}_si.pdf"]
    
    print(f"Starting PDF to Markdown conversion for DOI: {doi}")
    print(f"Data folder: {data_folder}")
    print("-" * 50)
    
    success_count = 0
    skipped_count = 0
    
    # Convert each PDF
    for pdf_file in pdf_files:
        pdf_path = os.path.join(data_folder, pdf_file)
        markdown_file = os.path.join(data_folder, f"{os.path.splitext(pdf_file)[0]}.md")
        
        if os.path.exists(pdf_path):
            # Check if markdown file already exists
            if os.path.exists(markdown_file):
                print(f"SKIP Markdown file already exists: {markdown_file}")
                skipped_count += 1
                success_count += 1  # Count as success since file exists
            else:
                output_file = convert_pdf_to_markdown(pdf_path, data_folder)
                if output_file:
                    print(f"OK Conversion completed: {output_file}")
                    success_count += 1
                else:
                    print(f"X Conversion failed: {pdf_file}")
        else:
            print(f"X PDF file not found: {pdf_path}")
    
    print("-" * 50)
    if skipped_count > 0:
        print(f"Conversion process completed! {success_count}/{len(pdf_files)} files ready ({skipped_count} skipped, {success_count - skipped_count} converted).")
    else:
        print(f"Conversion process completed! {success_count}/{len(pdf_files)} files converted successfully.")
    return success_count > 0

def convert_all_dois():
    """Convert PDFs for all DOIs in the data directory."""
    print("Starting PDF to Markdown conversion for all DOIs...")
    print(f"Data directory: {DATA_DIR}")
    print("-" * 50)
    
    if not os.path.exists(DATA_DIR):
        print(f"X Data directory not found: {DATA_DIR}")
        return False
    
    # Get all DOI folders in the data directory
    # Skip log directory and other non-DOI directories
    excluded_dirs = {'log', '__pycache__', '.git', '.vscode', 'node_modules'}
    
    doi_folders = sorted(
        [d for d in os.listdir(DATA_DIR)
         if os.path.isdir(os.path.join(DATA_DIR, d))
         and not d.startswith('.')
         and d not in excluded_dirs],
        key=lambda s: s.lower()
    )
    
    if not doi_folders:
        print("No DOI folders found in data directory")
        return False
    
    print(f"Found {len(doi_folders)} DOI folders")
    
    success_count = 0
    total_skipped = 0
    total_converted = 0
    
    for doi in doi_folders:
        print(f"\nProcessing DOI: {doi}")
        if convert_doi_pdfs(doi):
            success_count += 1
    
    print("-" * 50)
    print(f"Overall conversion completed! {success_count}/{len(doi_folders)} DOIs processed successfully.")
    return success_count > 0

def check_markdown_files_exist(doi: str):
    """
    Check if all required markdown files exist for a DOI.
    
    Args:
        doi (str): The DOI identifier
        
    Returns:
        bool: True if all required markdown files exist, False otherwise
    """
    data_folder = os.path.join(DATA_DIR, doi)
    
    if not os.path.exists(data_folder):
        return False
    
    # Check for main markdown file
    main_md = os.path.join(data_folder, f"{doi}.md")
    if not os.path.exists(main_md):
        return False
    
    # Check for SI markdown file (optional)
    si_md = os.path.join(data_folder, f"{doi}_si.md")
    if not os.path.exists(si_md):
        print(f"Warning: SI markdown file not found: {si_md}")
        # Don't fail if SI doesn't exist, as it's optional
    
    return True

def main(specific_doi: str = None):
    """Main function for command line usage."""
    if specific_doi:
        convert_doi_pdfs(specific_doi)
    else:
        convert_all_dois()

if __name__ == "__main__":
    main()
