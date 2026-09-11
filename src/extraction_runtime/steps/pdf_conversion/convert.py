"""Convert copied PDFs to markdown. simple_main domains use vision by default."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.extraction_runtime.domain_binding import RuntimeDomain
from src.utils.source_text_sanitize import sanitize_source_markdown


def _write_source_markdown(path: str, content: str) -> None:
    Path(path).write_text(sanitize_source_markdown(content), encoding="utf-8")


def _vision_enabled(config: dict) -> bool:
    domain = config.get("domain")
    if isinstance(domain, RuntimeDomain) and domain.vision_required:
        return True
    if config.get("vision_pdf_conversion") is not None:
        return bool(config.get("vision_pdf_conversion"))
    return str(config.get("execution_profile") or "") == "simple_main"


def _extract_text_md(pdf_path: str, output_folder: str, config: dict) -> str:
    try:
        import fitz  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF (fitz) is required for text extraction. Install with: pip install PyMuPDF"
        ) from exc

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    text_md = os.path.join(output_folder, f"{base_name}_text.md")
    combined_md = os.path.join(output_folder, f"{base_name}.md")
    vision_md = os.path.join(output_folder, f"{base_name}_vision.md")
    if _vision_enabled(config):
        print("    [VISION] Using vision LLM transcription")
        from src.extraction_runtime.steps.pdf_conversion.vision_llm import (
            convert_pdf_to_markdown,
        )

        try:
            md_content = convert_pdf_to_markdown(
                pdf_path,
                model=config.get("vision_model", "gpt-4o"),
                dpi=int(config.get("vision_dpi", 150)),
            )
        except Exception as exc:
            raise RuntimeError(
                "Vision PDF conversion requires LLM transcription, "
                "but vision extraction failed"
            ) from exc
        for dest in (vision_md, text_md, combined_md):
            _write_source_markdown(dest, md_content)
        print(f"    [OK] Vision transcription written -> {os.path.basename(vision_md)}")
        return text_md

    import fitz

    from src.extraction_runtime.steps.pdf_conversion.simple_conversion import (
        fallback_page_text,
        page_to_markdown,
    )

    parts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for index in range(len(doc)):
            page = doc.load_page(index)
            markdown = page_to_markdown(page) or fallback_page_text(page)
            parts.append(markdown)
    _write_source_markdown(text_md, "\n\n".join(part for part in parts if part))
    return text_md


def _extract_tables_md(pdf_path: str, output_folder: str) -> Optional[str]:
    try:
        from docling.document_converter import DocumentConverter
    except Exception:
        return None
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    tables_md = os.path.join(output_folder, f"{base_name}_tables.md")
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    lines: list[str] = []
    for index, table in enumerate(result.document.tables or [], start=1):
        frame = table.export_to_dataframe()
        lines.append(f"## Table {index}\n")
        lines.append(frame.to_markdown(index=False))
        lines.append("")
    _write_source_markdown(tables_md, "\n".join(lines))
    return tables_md


def _combine_text_and_tables(text_md: str, tables_md: Optional[str], combined_md: str) -> str:
    text_content = Path(text_md).read_text(encoding="utf-8") if os.path.exists(text_md) else ""
    tables_content = (
        Path(tables_md).read_text(encoding="utf-8")
        if tables_md and os.path.exists(tables_md)
        else ""
    )
    combined = (
        f"{text_content}\n\n{tables_content}"
        if text_content and tables_content.strip()
        else (tables_content or text_content)
    )
    _write_source_markdown(combined_md, combined)
    return combined_md


def convert_pdf_to_markdown(pdf_path: str, output_folder: str, config: dict) -> Optional[str]:
    try:
        print(f"  Converting {os.path.basename(pdf_path)}...")
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        combined_md = os.path.join(output_folder, f"{base_name}.md")
        text_md = _extract_text_md(pdf_path, output_folder, config)
        print("    [OK] Text extracted")
        if _vision_enabled(config):
            return combined_md if os.path.exists(combined_md) else text_md
        tables_md = None
        try:
            tables_md = _extract_tables_md(pdf_path, output_folder)
        except Exception as exc:
            print(f"    [WARN] Tables extraction skipped: {exc}")
        else:
            if tables_md:
                print("    [OK] Tables extracted")
            else:
                print("    [WARN] No tables extracted (docling unavailable)")
        final_md = _combine_text_and_tables(text_md, tables_md, combined_md)
        print(f"    [OK] Combined markdown created: {os.path.basename(final_md)}")
        return final_md
    except Exception as exc:
        print(f"    [ERROR] Error converting {pdf_path}: {exc}")
        return None


def convert_doi_pdfs(doi_hash: str, data_dir: str, config: dict) -> bool:
    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        print(f"  [ERROR] DOI folder not found: {doi_folder}")
        return False
    force_reconvert = bool(config.get("force_reconvert", False))
    vision_mode = _vision_enabled(config)
    success_count = 0
    for pdf_file in (f"{doi_hash}.pdf", f"{doi_hash}_si.pdf"):
        pdf_path = os.path.join(doi_folder, pdf_file)
        base_stem = os.path.splitext(pdf_file)[0]
        markdown_file = os.path.join(doi_folder, f"{base_stem}.md")
        vision_file = os.path.join(doi_folder, f"{base_stem}_vision.md")
        if not os.path.exists(pdf_path):
            if pdf_file.endswith("_si.pdf"):
                print(f"  [SKIP] SI PDF not found (optional): {pdf_file}")
                continue
            print(f"  [ERROR] PDF not found: {pdf_file}")
            continue
        if vision_mode and not force_reconvert and os.path.exists(vision_file):
            if Path(vision_file).stat().st_size > 0:
                print(f"  [SKIP] Vision markdown already exists: {os.path.basename(vision_file)}")
                success_count += 1
                continue
        if (
            not vision_mode
            and os.path.exists(markdown_file)
            and not force_reconvert
            and Path(markdown_file).stat().st_size > 0
        ):
            print(f"  [SKIP] Markdown already exists: {os.path.basename(markdown_file)}")
            success_count += 1
            continue
        if convert_pdf_to_markdown(pdf_path, doi_folder, config):
            success_count += 1
    return success_count > 0


def run_step(doi_hash: str, config: dict) -> bool:
    data_dir = config.get("data_dir", "data")
    print(f">> PDF Conversion: {doi_hash}")
    success = convert_doi_pdfs(doi_hash, data_dir, config)
    print(
        f"[OK] PDF Conversion completed: {doi_hash}"
        if success
        else f"[FAIL] PDF Conversion failed: {doi_hash}"
    )
    return success
