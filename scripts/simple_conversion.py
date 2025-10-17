#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Iterable

try:
    import fitz  # PyMuPDF
except Exception as e:
    print("PyMuPDF (fitz) is required: pip install pymupdf", file=sys.stderr)
    raise

# --------- hardcoded paths ---------
PDF_PATH = Path("scripts/data/d5ff239e.pdf")
OUT_DIR = Path("scripts/data")


def _norm_whitespace(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace("\u200b", "")
    out = []
    prev_space = False
    for ch in s:
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                prev_space = True
        else:
            out.append(ch)
            prev_space = False
    return "".join(out).strip()


def _merge_hyphenated(lines: List[str]) -> List[str]:
    if not lines:
        return lines
    merged: List[str] = []
    for line in lines:
        if merged and merged[-1].endswith("-"):
            prev = merged.pop()
            merged.append(prev[:-1] + line.lstrip())
        else:
            merged.append(line)
    return merged


def _text_from_spans(spans: Iterable[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for s in spans or []:
        if not isinstance(s, dict):
            continue
        t = s.get("text")
        if isinstance(t, str):
            parts.append(t)
    return "".join(parts)


def _collect_lines_from_block(block: Dict[str, Any]) -> List[str]:
    lines_out: List[str] = []
    for line in block.get("lines", []) or []:
        spans = line.get("spans") or []
        txt = _text_from_spans(spans)
        txt = _norm_whitespace(txt)
        if txt:
            lines_out.append(txt)
    return lines_out


def page_to_markdown(page: "fitz.Page") -> str:
    data = page.get_text("dict")
    md_lines: List[str] = []
    for b in data.get("blocks", []) or []:
        if b.get("type") not in (None, 0):  # only text blocks
            continue
        if not b.get("lines"):
            continue
        lines = _collect_lines_from_block(b)
        lines = _merge_hyphenated(lines)
        if lines:
            md_lines.append("\n".join(lines))
    return "\n\n".join(md_lines).strip()


def _json_safe(obj: Any) -> Any:
    """Recursively remove or summarize non-JSON-serializable values."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # Drop huge binary payloads commonly named like below
            if k in {"image", "stream", "data", "file"} and isinstance(v, (bytes, bytearray)):
                out[k] = f"<bytes:{len(v)}>"
            else:
                out[k] = _json_safe(v)
        return out
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<bytes:{len(obj)}>"
    # JSON can handle int/float/str/bool/None
    return obj


def extract_pages(pdf_path: Path, out_dir: Path, debug: bool = True) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    emitted: List[Path] = []
    with fitz.open(pdf_path) as doc:
        for i in range(len(doc)):
            page = doc.load_page(i)
            md = page_to_markdown(page)
            if not md:
                md = _norm_whitespace(page.get_text()) or ""
            out_file = out_dir / f"page_{i+1:04d}.md"
            out_file.write_text(md, encoding="utf-8")
            emitted.append(out_file)
            if debug:
                raw = page.get_text("dict")
                safe = _json_safe(raw)
                (out_dir / f"page_{i+1:04d}.debug.json").write_text(
                    json.dumps(safe, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    combined = out_dir / "all_pages.md"
    with combined.open("w", encoding="utf-8") as w:
        for p in emitted:
            w.write(f"\n\n<!-- {p.name} -->\n\n")
            w.write(p.read_text(encoding="utf-8"))
    return emitted


def main() -> None:
    if not PDF_PATH.exists():
        print(f"PDF not found: {PDF_PATH}", file=sys.stderr)
        sys.exit(2)
    try:
        files = extract_pages(PDF_PATH, OUT_DIR)
    except Exception as e:
        print(f"Extraction failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote {len(files)} page files to {OUT_DIR}")
    print(f"Combined file: {OUT_DIR / 'all_pages.md'}")


if __name__ == "__main__":
    main()
