"""Vision PDF transcription used when the domain config enables vision."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import List, Optional

try:
    import fitz
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyMuPDF (fitz) is required. Install with: pip install PyMuPDF") from exc

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("openai is required. Install with: pip install openai") from exc

from models.llm_call_telemetry import instrument_openai_client

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
        return instrument_openai_client(
            OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        )
    return instrument_openai_client(OpenAI(api_key=api_key, timeout=timeout))


def _render_page_to_data_url(page: "fitz.Page", dpi: int = 150) -> str:
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _strip_code_fence(text: str) -> str:
    import re

    stripped = re.sub(r"^```(?:markdown)?\s*\n", "", text.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\n```\s*$", "", stripped.strip())
    return stripped.strip()


def _extract_text_from_response(response) -> str:
    content = response.choices[0].message.content
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
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
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
    dpi: int = 150,
    max_pages: Optional[int] = None,
) -> str:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    client = _get_client()
    out_lines: list[str] = []
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
