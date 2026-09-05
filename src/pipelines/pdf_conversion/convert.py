"""
PDF to Markdown Conversion Step

Converts PDF files to markdown format using docling and simple_conversion.
Creates three files per PDF:
  - <name>_text.md (text extraction)
  - <name>_tables.md (table extraction)
  - <name>.md (combined)

For medical pipeline:
  - Uses a vision LLM to transcribe each page from its rendered image.
  - Produces <name>_vision.md (canonical source for all downstream steps).
  - Also copies result to <name>.md and <name>_text.md for backward compat.
"""

import os
import sys
import json
import importlib.util
from typing import Optional
from pathlib import Path

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    # Optional dependency: used only for table extraction.
    from docling.document_converter import DocumentConverter  # type: ignore
except Exception:  # pragma: no cover
    DocumentConverter = None  # type: ignore

try:
    import pandas as pd  # noqa: F401
except Exception:  # pragma: no cover
    pd = None  # type: ignore

from src.utils.source_text_sanitize import sanitize_source_markdown


def _write_source_markdown(path: str, content: str) -> None:
    """Write UTF-8 markdown after global illegal-character sanitization."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(sanitize_source_markdown(content))


def _is_medical_pipeline(config: dict) -> bool:
    """Check if this is the medical pipeline based on meta_task_config."""
    meta_task_config_path = config.get("meta_task_config")
    if not meta_task_config_path:
        return False
    
    try:
        abs_path = os.path.join(project_root, meta_task_config_path)
        if not os.path.exists(abs_path):
            return False
        
        with open(abs_path, "r", encoding="utf-8") as f:
            meta_cfg = json.load(f)
        
        main_ontology = meta_cfg.get("ontologies", {}).get("main", {})
        ontology_name = main_ontology.get("name", "")
        
        return ontology_name == "medical"
    except Exception:
        return False


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


def _is_vision_pdf_enabled(config: dict) -> bool:
    """Return True when vision LLM PDF conversion should be used."""
    if _is_medical_pipeline(config):
        return True
    return bool(config.get("vision_pdf_conversion", False))


def _load_vision_conversion_module():
    """Load scripts/advanced_pdf_conversion.py/vision_llm_pdf_to_markdown.py."""
    vision_path = os.path.join(
        project_root, "scripts", "advanced_pdf_conversion.py", "vision_llm_pdf_to_markdown.py"
    )
    if not os.path.exists(vision_path):
        raise ImportError(f"vision_llm_pdf_to_markdown.py not found at {vision_path}")

    spec = importlib.util.spec_from_file_location("vision_llm_pdf_to_markdown", vision_path)
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load vision_llm_pdf_to_markdown.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_basic_segmentation_module():
    """
    Load the medical PDF segmentation module for layout-aware extraction.

    Preference order (first existing wins):
      1. scripts/advanced_pdf_conversion.py/pymupdf_segmentation.py
      2. scripts/advanced_pdf_conversion.py/basic_segmentation.py
    """
    base_dir = os.path.join(project_root, "scripts", "advanced_pdf_conversion.py")
    candidates = [
        ("pymupdf_segmentation", os.path.join(base_dir, "pymupdf_segmentation.py")),
        ("basic_segmentation", os.path.join(base_dir, "basic_segmentation.py")),
    ]

    for mod_name, seg_path in candidates:
        if not os.path.exists(seg_path):
            continue

        spec = importlib.util.spec_from_file_location(mod_name, seg_path)
        if spec is None or spec.loader is None:
            continue

        module = importlib.util.module_from_spec(spec)
        # Register module in sys.modules before exec to fix dataclass decorator issue
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    raise ImportError(
        "No medical segmentation module found. "
        "Expected either pymupdf_segmentation.py or basic_segmentation.py "
        "under scripts/advanced_pdf_conversion.py/."
    )


def _extract_text_md(pdf_path: str, output_folder: str, config: dict) -> str:
    """
    Extract text from PDF to <pdf>_text.md.

    Medical pipeline:
        Calls the vision LLM script; writes <pdf>_vision.md, <pdf>_text.md, <pdf>.md.
        Vision conversion is mandatory for medical PDFs.
    Other pipelines:
        Uses simple_conversion.py.

    Returns the path to <pdf>_text.md in all cases.
    """
    try:
        import fitz  # noqa: F401  (required for non-vision path; vision module imports it itself)
    except ImportError as e:
        raise RuntimeError(
            "PyMuPDF (fitz) is required for text extraction. Install with: pip install PyMuPDF"
        ) from e

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    text_md = os.path.join(output_folder, f"{base_name}_text.md")
    combined_md = os.path.join(output_folder, f"{base_name}.md")
    vision_md = os.path.join(output_folder, f"{base_name}_vision.md")

    is_medical = _is_medical_pipeline(config)

    if is_medical and _is_vision_pdf_enabled(config):
        print(f"    [MEDICAL VISION] Using vision LLM transcription")
        try:
            vm = _load_vision_conversion_module()
            model = config.get("vision_model", "gpt-4o")
            dpi = int(config.get("vision_dpi", 150))
            md_content = vm.convert_pdf_to_markdown(pdf_path, model=model, dpi=dpi)

            for dest in (vision_md, text_md, combined_md):
                _write_source_markdown(dest, md_content)

            print(f"    [OK] Vision transcription written → {os.path.basename(vision_md)}")
            return text_md
        except Exception as e:
            raise RuntimeError(
                "Medical PDF conversion requires vision LLM transcription, but vision extraction failed"
            ) from e

    if is_medical:
        print(f"    [MEDICAL MODE] Using layout-aware extraction")
        try:
            bs = _load_basic_segmentation_module()
            try:
                seg_file = getattr(bs, "__file__", "") or ""
                seg_name = Path(seg_file).name if seg_file else getattr(bs, "__name__", "unknown")
                print(f"    [MEDICAL MODE] Segmentation module: {seg_name}")
            except Exception:
                pass
            md_content = bs.convert_pdf_to_markdown(pdf_path)

            for dest in (text_md, combined_md):
                _write_source_markdown(dest, md_content)

            return text_md
        except Exception as e:
            print(f"    [WARN] Layout-aware extraction failed ({e}), falling back to simple conversion")

    # Standard extraction for non-medical pipelines
    import fitz  # noqa: F811
    sc = _load_simple_conversion_module()

    parts = []
    with fitz.open(pdf_path) as doc:
        for i in range(len(doc)):
            page = doc.load_page(i)
            md = sc.page_to_markdown(page)
            if not md:
                md = sc._norm_whitespace(page.get_text()) or ""
            parts.append(md)

    text_content = "\n\n".join(p for p in parts if p is not None)
    _write_source_markdown(text_md, text_content)

    return text_md


def _extract_tables_md(pdf_path: str, output_folder: str) -> Optional[str]:
    """Extract tables from PDF to <pdf>_tables.md using docling."""
    if DocumentConverter is None:
        # Allow pipeline to run without docling; tables will simply be absent.
        return None

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

    _write_source_markdown(tables_md, "\n".join(lines))

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

    _write_source_markdown(combined_md, combined)

    return combined_md


def convert_pdf_to_markdown(pdf_path: str, output_folder: str, config: dict) -> Optional[str]:
    """
    Convert a single PDF to markdown format.
    
    Args:
        pdf_path: Path to the PDF file
        output_folder: Directory to save markdown files
        config: Pipeline configuration (used to detect medical mode)
        
    Returns:
        Path to combined markdown file, or None if conversion failed
    """
    try:
        print(f"  Converting {os.path.basename(pdf_path)}...")

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        combined_md = os.path.join(output_folder, f"{base_name}.md")

        # 1) Text extraction (layout-aware for medical, simple for others)
        text_md = _extract_text_md(pdf_path, output_folder, config)
        print(f"    [OK] Text extracted")

        # Skip table extraction for medical pipeline (already included in basic_segmentation output)
        if _is_medical_pipeline(config):
            # Medical extraction already wrote the combined .md file
            if os.path.exists(combined_md):
                return combined_md
            return text_md

        # 2) Table extraction (optional, for non-medical pipelines)
        tables_md = None
        try:
            tables_md = _extract_tables_md(pdf_path, output_folder)
        except Exception as e:
            print(f"    [WARN] Tables extraction skipped: {e}")
            tables_md = None
        else:
            if tables_md:
                print(f"    [OK] Tables extracted")
            else:
                print(f"    [WARN] No tables extracted (docling unavailable)")

        # 3) Combine
        final_md = _combine_text_and_tables(text_md, tables_md, combined_md)
        print(f"    [OK] Combined markdown created: {os.path.basename(final_md)}")
        
        return final_md

    except Exception as e:
        print(f"    [ERROR] Error converting {pdf_path}: {str(e)}")
        return None


def convert_doi_pdfs(doi_hash: str, data_dir: str, config: dict) -> bool:
    """
    Convert PDFs for a specific DOI hash.
    
    Args:
        doi_hash: The DOI hash identifier
        data_dir: Base data directory (e.g., 'data')
        config: Pipeline configuration
        
    Returns:
        True if at least one PDF was successfully converted or already exists
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    
    if not os.path.exists(doi_folder):
        print(f"  [ERROR] DOI folder not found: {doi_folder}")
        return False
    
    # Files to convert
    pdf_files = [f"{doi_hash}.pdf", f"{doi_hash}_si.pdf"]
    
    success_count = 0
    skipped_count = 0
    
    force_reconvert = bool(config.get("force_reconvert", False))
    medical_mode = _is_medical_pipeline(config)
    vision_mode = medical_mode and _is_vision_pdf_enabled(config)

    for pdf_file in pdf_files:
        pdf_path = os.path.join(doi_folder, pdf_file)
        base_stem = os.path.splitext(pdf_file)[0]
        markdown_file = os.path.join(doi_folder, f"{base_stem}.md")
        vision_file = os.path.join(doi_folder, f"{base_stem}_vision.md")

        if not os.path.exists(pdf_path):
            if "_si.pdf" in pdf_file:
                print(f"  [SKIP] SI PDF not found (optional): {pdf_file}")
                continue
            else:
                print(f"  [ERROR] PDF not found: {pdf_file}")
                continue

        # Vision mode: skip if _vision.md already exists and is non-empty.
        if vision_mode and not force_reconvert:
            if os.path.exists(vision_file):
                try:
                    if Path(vision_file).stat().st_size > 0:
                        print(f"  [SKIP] Vision markdown already exists: {os.path.basename(vision_file)}")
                        skipped_count += 1
                        success_count += 1
                        continue
                except Exception:
                    pass

        # Non-vision medical mode: regenerate if the existing .md lacks layout/KV metadata.
        needs_regen = False
        if not vision_mode and os.path.exists(markdown_file) and not force_reconvert:
            if medical_mode:
                try:
                    existing = Path(markdown_file).read_text(encoding="utf-8", errors="replace")
                    if ("bbox=(" not in existing) or ("Inferred key/value pairs" not in existing):
                        needs_regen = True
                        print(
                            f"  [REGEN] Medical markdown missing layout/KV metadata: "
                            f"{os.path.basename(markdown_file)}"
                        )
                except Exception:
                    needs_regen = True
                    print(f"  [REGEN] Unable to read existing markdown; regenerating: {os.path.basename(markdown_file)}")

            if not needs_regen:
                print(f"  [SKIP] Markdown already exists: {os.path.basename(markdown_file)}")
                skipped_count += 1
                success_count += 1
                continue

        missing = (vision_mode and not os.path.exists(vision_file)) or (
            not vision_mode and not os.path.exists(markdown_file)
        )
        if force_reconvert or needs_regen or missing:
            output_file = convert_pdf_to_markdown(pdf_path, doi_folder, config)
            if output_file:
                success_count += 1
            else:
                print(f"  [ERROR] Conversion failed: {pdf_file}")
    
    if success_count > 0:
        if skipped_count > 0:
            print(f"  [OK] PDF conversion: {success_count} files ready ({skipped_count} skipped, {success_count - skipped_count} converted)")
        else:
            print(f"  [OK] PDF conversion: {success_count} files converted")
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
    
    print(f">> PDF Conversion: {doi_hash}")
    success = convert_doi_pdfs(doi_hash, data_dir, config)
    
    if success:
        print(f"[OK] PDF Conversion completed: {doi_hash}")
    else:
        print(f"[FAIL] PDF Conversion failed: {doi_hash}")
    
    return success


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        run_step(test_hash, test_config)
    else:
        print("Usage: python convert.py <doi_hash>")

