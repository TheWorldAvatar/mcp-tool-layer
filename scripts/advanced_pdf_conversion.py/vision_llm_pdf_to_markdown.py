#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convert PDF pages to images and transcribe them to markdown with a vision LLM.

Examples:
  python scripts/advanced_pdf_conversion.py/vision_llm_pdf_to_markdown.py "raw_data/OP Bericht 1.pdf"
  python scripts/advanced_pdf_conversion.py/vision_llm_pdf_to_markdown.py --all-md
  python scripts/advanced_pdf_conversion.py/vision_llm_pdf_to_markdown.py --all-md --raw-dir medical_case
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
from typing import List, Optional

try:
    import fitz  # PyMuPDF
except ImportError as e:
    raise RuntimeError("PyMuPDF (fitz) is required. Install with: pip install PyMuPDF") from e

try:
    from openai import OpenAI
except ImportError as e:
    raise RuntimeError("openai is required. Install with: pip install openai") from e

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


SYSTEM_PROMPT = """You are transcribing a PDF page from an image into faithful markdown.

Rules:
- Output only markdown for the visible page contents.
- Preserve reading order as accurately as possible.
- Preserve headings, lists, short key/value structures, and tables when possible.
- For tables, use markdown tables if the structure is clear; otherwise use readable bullet points.
- Do not invent missing text.
- Do not add commentary, explanations, or code fences.
- Ignore page furniture only if it is clearly irrelevant noise; otherwise keep visible text.
"""


def _get_client(timeout: float = 120.0) -> OpenAI:
    if load_dotenv is not None:
        load_dotenv(override=True)

    api_key = (
        os.getenv("REMOTE_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = os.getenv("REMOTE_BASE_URL") or os.getenv("BASE_URL")

    if not api_key:
        raise ValueError(
            "No API key found. Set one of: REMOTE_API_KEY, API_KEY, OPENAI_API_KEY."
        )

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    return OpenAI(api_key=api_key, timeout=timeout)


def _render_page_to_data_url(page: "fitz.Page", dpi: int = 180) -> str:
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    png_bytes = pix.tobytes("png")
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _strip_code_fence(text: str) -> str:
    """Remove spurious ```markdown ... ``` wrapping the model sometimes adds."""
    import re
    stripped = re.sub(r"^```(?:markdown)?\s*\n", "", text.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\n```\s*$", "", stripped.strip())
    return stripped.strip()


def _extract_text_from_response(response) -> str:
    try:
        content = response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"Unexpected LLM response shape: {e}") from e

    if isinstance(content, str):
        return _strip_code_fence(content)

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(p.strip() for p in parts if p and p.strip()).strip()

    return str(content).strip()


def transcribe_page_to_markdown(
    client: OpenAI,
    page: "fitz.Page",
    *,
    model: str,
    dpi: int,
    page_number: int,
    total_pages: int,
) -> str:
    image_url = _render_page_to_data_url(page, dpi=dpi)
    user_text = (
        f"Transcribe page {page_number} of {total_pages} into clean markdown. "
        "Keep the content faithful to the page and do not add explanations."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        temperature=0.0,
    )
    return _extract_text_from_response(response)


def convert_pdf_to_markdown(
    pdf_path: str,
    *,
    model: str = "gpt-4o",
    dpi: int = 180,
    max_pages: Optional[int] = None,
) -> str:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    client = _get_client()
    out_lines: List[str] = []

    with fitz.open(str(path)) as doc:
        total_pages = doc.page_count
        page_limit = min(total_pages, max_pages) if max_pages else total_pages

        for page_index in range(page_limit):
            page = doc.load_page(page_index)
            print(f"[INFO] Vision transcribing page {page_index + 1}/{page_limit}: {path.name}")
            page_md = transcribe_page_to_markdown(
                client,
                page,
                model=model,
                dpi=dpi,
                page_number=page_index + 1,
                total_pages=page_limit,
            )
            out_lines.append(f"# Page {page_index + 1} / {page_limit}")
            out_lines.append("")
            out_lines.append(page_md or "_No text returned by model for this page._")
            out_lines.append("")

    return "\n".join(out_lines).strip() + "\n"


def _default_output_path(pdf_path: Path, suffix: str) -> Path:
    return pdf_path.with_name(f"{pdf_path.stem}{suffix}")


def convert_all_pdfs_in_dir(
    raw_dir: Path,
    *,
    model: str,
    dpi: int,
    suffix: str,
    max_pages: Optional[int],
) -> None:
    if not raw_dir.exists():
        raise FileNotFoundError(f"Directory not found: {raw_dir}")

    pdf_files = sorted(raw_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"[WARN] No PDF files found in {raw_dir}")
        return

    print(f"[INFO] Found {len(pdf_files)} PDF(s) in {raw_dir}")
    for pdf_path in pdf_files:
        output_path = _default_output_path(pdf_path, suffix)
        markdown = convert_pdf_to_markdown(
            str(pdf_path),
            model=model,
            dpi=dpi,
            max_pages=max_pages,
        )
        output_path.write_text(markdown, encoding="utf-8")
        print(f"[OK] Wrote markdown: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vision-LLM PDF to markdown conversion."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=None,
        help="Path to a single PDF to convert.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output markdown path for single-PDF mode.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="Vision-capable model name (default: gpt-4o).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=180,
        help="Render DPI for page images (default: 180).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional page limit for debugging.",
    )
    parser.add_argument(
        "--all-md",
        action="store_true",
        help="Convert all PDFs in --raw-dir to sidecar markdown files.",
    )
    parser.add_argument(
        "--raw-dir",
        type=str,
        default="raw_data",
        help="Directory used by --all-md (default: raw_data).",
    )
    parser.add_argument(
        "--suffix",
        type=str,
        default="_vision.md",
        help="Output suffix for generated markdown files (default: _vision.md).",
    )
    args = parser.parse_args()

    if args.all_md:
        convert_all_pdfs_in_dir(
            Path(args.raw_dir),
            model=args.model,
            dpi=args.dpi,
            suffix=args.suffix,
            max_pages=args.max_pages,
        )
        return

    if not args.pdf:
        parser.error("Provide a PDF path or use --all-md.")

    pdf_path = Path(args.pdf)
    output_path = Path(args.output) if args.output else _default_output_path(pdf_path, args.suffix)

    markdown = convert_pdf_to_markdown(
        str(pdf_path),
        model=args.model,
        dpi=args.dpi,
        max_pages=args.max_pages,
    )
    output_path.write_text(markdown, encoding="utf-8")
    print(f"[OK] Wrote markdown: {output_path}")


if __name__ == "__main__":
    main()
