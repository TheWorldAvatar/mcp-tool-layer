"""Non-vision page conversion (PyMuPDF text blocks)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

try:
    import fitz
except Exception as exc:  # pragma: no cover
    fitz = None  # type: ignore
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _norm_whitespace(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("\u200b", "")
    out: list[str] = []
    prev_space = False
    for char in text:
        if char.isspace():
            if not prev_space:
                out.append(" ")
                prev_space = True
        else:
            out.append(char)
            prev_space = False
    return "".join(out).strip()


def _merge_hyphenated(lines: List[str]) -> List[str]:
    if not lines:
        return lines
    merged: list[str] = []
    for line in lines:
        if merged and merged[-1].endswith("-"):
            prev = merged.pop()
            merged.append(prev[:-1] + line.lstrip())
        else:
            merged.append(line)
    return merged


def _text_from_spans(spans: Iterable[Dict[str, Any]]) -> str:
    parts: list[str] = []
    for span in spans or []:
        if isinstance(span, dict) and isinstance(span.get("text"), str):
            parts.append(span["text"])
    return "".join(parts)


def _collect_lines_from_block(block: Dict[str, Any]) -> List[str]:
    lines_out: list[str] = []
    for line in block.get("lines", []) or []:
        text = _norm_whitespace(_text_from_spans(line.get("spans") or []))
        if text:
            lines_out.append(text)
    return lines_out


def page_to_markdown(page: Any) -> str:
    data = page.get_text("dict")
    md_lines: list[str] = []
    for block in data.get("blocks", []) or []:
        if block.get("type") not in (None, 0) or not block.get("lines"):
            continue
        lines = _merge_hyphenated(_collect_lines_from_block(block))
        if lines:
            md_lines.append("\n".join(lines))
    return "\n\n".join(md_lines).strip()


def fallback_page_text(page: Any) -> str:
    return _norm_whitespace(page.get_text()) or ""
