#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Layout-aware PDF extraction with OCR fallback for OP Bericht documents.

Primary: pdfplumber (best quality for text-layer PDFs)
Fallback: OCR via pytesseract + PyMuPDF rendering (for scanned/poor quality PDFs)

Install:
  pip install pdfplumber PyMuPDF pytesseract pillow

System requirement:
  - Tesseract OCR binary: https://github.com/tesseract-ocr/tesseract
    Windows: Download installer from https://github.com/UB-Mannheim/tesseract/wiki
    Linux: apt-get install tesseract-ocr tesseract-ocr-deu
    macOS: brew install tesseract tesseract-lang

Usage:
  python ocr_fallback_segmentation.py <pdf_path> -o <output.md>
  python ocr_fallback_segmentation.py --doi-hash <hash> --pipeline-override
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

try:
    import pdfplumber
except ImportError as e:
    raise RuntimeError("pdfplumber is required. Install with: pip install pdfplumber") from e

try:
    import fitz  # PyMuPDF
except ImportError as e:
    raise RuntimeError("PyMuPDF is required. Install with: pip install PyMuPDF") from e

try:
    from PIL import Image
    import pytesseract
except ImportError as e:
    pytesseract = None
    PIL_IMPORT_ERROR = e


# -----------------------------
# Data structures
# -----------------------------

@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int


# -----------------------------
# Utilities: text normalization
# -----------------------------

def _norm(s: str) -> str:
    """Normalize whitespace."""
    return re.sub(r"\s+", " ", s).strip()


def _norm_key(s: str) -> str:
    """Normalize punctuation variants for key matching."""
    s = _norm(s)
    # Handle colon variants
    for ch in ["\uf03a", ":", "：", "﹕", "∶", "꞉", "︰"]:
        s = s.replace(ch, ":")
    return s


# -----------------------------
# Primary: pdfplumber extraction
# -----------------------------

def extract_words_pdfplumber(pdf_path: str) -> Tuple[List[Word], List[Tuple[float, float]]]:
    """
    Extract words with bounding boxes using pdfplumber.
    Returns (words, page_sizes) where page_sizes = [(width, height), ...]
    """
    words: List[Word] = []
    page_sizes: List[Tuple[float, float]] = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            page_sizes.append((page.width, page.height))
            # extract_words parameters (pdfplumber 0.11.9)
            w = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=["x0", "x1", "top", "bottom"],
            )
            for ww in w:
                t = _norm(ww.get("text", ""))
                if not t:
                    continue
                words.append(
                    Word(
                        text=t,
                        x0=float(ww["x0"]),
                        x1=float(ww["x1"]),
                        top=float(ww["top"]),
                        bottom=float(ww["bottom"]),
                        page=pi,
                    )
                )
    
    return words, page_sizes


# -----------------------------
# Fallback: OCR extraction
# -----------------------------

def extract_words_ocr(pdf_path: str, dpi: int = 300) -> Tuple[List[Word], List[Tuple[float, float]]]:
    """
    Extract words using OCR (tesseract) on rendered PDF pages.
    Returns same format as pdfplumber extraction.
    """
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract and Pillow are required for OCR. "
            "Install with: pip install pytesseract pillow\n"
            "Also install Tesseract binary: https://github.com/tesseract-ocr/tesseract"
        ) from PIL_IMPORT_ERROR
    
    words: List[Word] = []
    page_sizes: List[Tuple[float, float]] = []
    
    with fitz.open(pdf_path) as doc:
        for pi in range(len(doc)):
            page = doc.load_page(pi)
            
            # Render page to image
            mat = fitz.Matrix(dpi / 72, dpi / 72)  # scale for DPI
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Page size in PDF units
            page_width = page.rect.width
            page_height = page.rect.height
            page_sizes.append((page_width, page_height))
            
            # OCR with word-level bounding boxes
            # Use German language model
            ocr_data = pytesseract.image_to_data(
                img,
                lang="deu",
                output_type=pytesseract.Output.DICT
            )
            
            # Scale factor from image pixels back to PDF units
            scale_x = page_width / pix.width
            scale_y = page_height / pix.height
            
            # Extract words with confidence filtering
            n_boxes = len(ocr_data["text"])
            for i in range(n_boxes):
                conf = int(ocr_data["conf"][i])
                text = ocr_data["text"][i]
                
                # Filter low confidence and empty
                if conf < 30 or not text.strip():
                    continue
                
                # Bounding box in image pixels
                x = ocr_data["left"][i]
                y = ocr_data["top"][i]
                w = ocr_data["width"][i]
                h = ocr_data["height"][i]
                
                # Convert to PDF coordinates
                x0 = x * scale_x
                x1 = (x + w) * scale_x
                top = y * scale_y
                bottom = (y + h) * scale_y
                
                words.append(
                    Word(
                        text=_norm(text),
                        x0=x0,
                        x1=x1,
                        top=top,
                        bottom=bottom,
                        page=pi,
                    )
                )
    
    return words, page_sizes


def extract_words_with_fallback(pdf_path: str, min_words_threshold: int = 50) -> Tuple[List[Word], List[Tuple[float, float]], str]:
    """
    Try pdfplumber first, fall back to OCR if extraction is poor.
    Returns (words, page_sizes, method) where method is "pdfplumber" or "ocr"
    """
    print(f"  [1/3] Attempting pdfplumber extraction...")
    try:
        words, page_sizes = extract_words_pdfplumber(pdf_path)
        
        # Quality check: if very few words extracted, likely scanned/poor text layer
        if len(words) < min_words_threshold:
            print(f"  [WARN] Only {len(words)} words extracted, below threshold {min_words_threshold}")
            print(f"  [2/3] Falling back to OCR...")
            try:
                words, page_sizes = extract_words_ocr(pdf_path)
                return words, page_sizes, "ocr"
            except Exception as ocr_error:
                print(f"  [ERROR] OCR fallback failed: {ocr_error}")
                print(f"  [INFO] To enable OCR, install Tesseract:")
                print(f"          Windows: https://github.com/UB-Mannheim/tesseract/wiki")
                print(f"          Linux: apt-get install tesseract-ocr tesseract-ocr-deu")
                print(f"  [FALLBACK] Using pdfplumber results despite low word count")
                return words, page_sizes, "pdfplumber"
        
        print(f"  [OK] Extracted {len(words)} words via pdfplumber")
        return words, page_sizes, "pdfplumber"
    
    except Exception as e:
        print(f"  [WARN] pdfplumber extraction failed: {e}")
        print(f"  [2/3] Falling back to OCR...")
        try:
            words, page_sizes = extract_words_ocr(pdf_path)
            print(f"  [OK] Extracted {len(words)} words via OCR")
            return words, page_sizes, "ocr"
        except Exception as ocr_error:
            print(f"  [ERROR] OCR fallback failed: {ocr_error}")
            print(f"  [INFO] To enable OCR, install Tesseract:")
            print(f"          Windows: https://github.com/UB-Mannheim/tesseract/wiki")
            print(f"          Linux: apt-get install tesseract-ocr tesseract-ocr-deu")
            raise RuntimeError(f"Both pdfplumber and OCR extraction failed") from e


# -----------------------------
# Line grouping
# -----------------------------

def group_words_into_lines(words: List[Word], y_tol: float = 3.0) -> Dict[int, List[List[Word]]]:
    """
    Group words by page into lines using y coordinate clustering.
    Returns dict: page_index -> list of lines (each line is list of Word sorted by x0)
    """
    by_page: Dict[int, List[Word]] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)
    
    page_lines: Dict[int, List[List[Word]]] = {}
    for p, ws in by_page.items():
        ws = sorted(ws, key=lambda z: (z.top, z.x0))
        lines: List[List[Word]] = []
        for w in ws:
            placed = False
            for line in lines:
                # Compare to first word's top
                if abs(w.top - line[0].top) <= y_tol:
                    line.append(w)
                    placed = True
                    break
            if not placed:
                lines.append([w])
        
        # Sort words within each line by x position
        for line in lines:
            line.sort(key=lambda w: w.x0)
        
        page_lines[p] = lines
    
    return page_lines


def render_lines_to_text(page_lines: Dict[int, List[List[Word]]]) -> List[str]:
    """Convert grouped lines to text strings, removing empty lines."""
    text_lines: List[str] = []
    
    for page_idx in sorted(page_lines.keys()):
        for line_words in page_lines[page_idx]:
            # Join words with space
            line_text = " ".join(w.text for w in line_words)
            line_text = _norm(line_text)
            if line_text:
                text_lines.append(line_text)
    
    return text_lines


# -----------------------------
# Header/Footer noise removal
# -----------------------------

FOOTER_NOISE_PATTERNS = [
    re.compile(r"^Druckdatum\s*:", re.IGNORECASE),
    re.compile(r"^Seite\s+\d+\s*/\s*\d+", re.IGNORECASE),
    re.compile(r"^Patient\s*:\s*\*?\s*Fall\s*:", re.IGNORECASE),
    re.compile(r"^Hauptstra(ß|ss)e\s+\d+", re.IGNORECASE),
    re.compile(r"^\d{5,}\s+(Berlin|Stadt)", re.IGNORECASE),  # Address line with city
]


def remove_footer_noise(lines: List[str]) -> List[str]:
    """Remove common header/footer noise patterns."""
    cleaned: List[str] = []
    for ln in lines:
        if any(pat.search(ln) for pat in FOOTER_NOISE_PATTERNS):
            continue
        cleaned.append(ln)
    return cleaned


def remove_repeated_lines(lines: List[str], threshold: int = 2) -> List[str]:
    """Remove lines that appear more than threshold times (likely headers/footers)."""
    from collections import Counter
    
    counts = Counter(lines)
    filtered: List[str] = []
    
    for ln in lines:
        if counts[ln] <= threshold:
            filtered.append(ln)
    
    return filtered


# -----------------------------
# Metadata extraction
# -----------------------------

FIELD_PATTERNS = [
    ("Name", re.compile(r"^(?P<k>Name|Patient)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)),
    ("Fall-Nr", re.compile(r"^(?P<k>Fall[- ]?Nr\.?)\s*[:\-]?\s*(?P<v>\d{6,})", re.IGNORECASE)),
    ("OP-Datum", re.compile(r"^(?P<k>OP[- ]?Datum)\s*[:\-]?\s*(?P<v>\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)),
    ("OP-Nummer", re.compile(r"^(?P<k>OP[- ]?Nummer|OP[- ]?Nr\.?)\s*[:\-]?\s*(?P<v>\d{6,})", re.IGNORECASE)),
    ("OP-Saal", re.compile(r"^(?P<k>OP[- ]?Saal)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)),
    ("Operateur/in", re.compile(r"^(?P<k>Operateur[\/]?[Ii]n|Operateur)\s*[:\-]?\s*(?P<v>[^:]+?)(?:\s+Schnitt:.*)?$", re.IGNORECASE)),
    ("Assistenz", re.compile(r"^(?P<k>Assistenz)\s*[:\-]?\s*(?P<v>[^:]+?)(?:\s+Naht:.*)?$", re.IGNORECASE)),
    ("Schnitt", re.compile(r"^(?P<k>Schnitt)\s*[:\-]?\s*(?P<v>\d{2}:\d{2}\s*Uhr)", re.IGNORECASE)),
    ("Naht", re.compile(r"^(?P<k>Naht)\s*[:\-]?\s*(?P<v>\d{2}:\d{2}\s*Uhr)", re.IGNORECASE)),
]


def extract_metadata(lines: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """
    Extract key-value metadata from lines.
    Returns (metadata_dict, remaining_lines)
    """
    fields: Dict[str, str] = {}
    remaining: List[str] = []
    
    for ln in lines:
        matched = False
        
        for field_name, pattern in FIELD_PATTERNS:
            m = pattern.match(_norm_key(ln))
            if m:
                value = _norm(m.group("v"))
                if value and field_name not in fields:  # Don't overwrite
                    fields[field_name] = value
                matched = True
                break
        
        if not matched:
            remaining.append(ln)
    
    return fields, remaining


# -----------------------------
# Section splitting
# -----------------------------

SECTION_HEADINGS = {
    "Diagnose": re.compile(r"^Diagnose\s*:\s*$", re.IGNORECASE),
    "Operation": re.compile(r"^Operation\s*:\s*$", re.IGNORECASE),
    "Bericht": re.compile(r"^Bericht\s*:\s*$", re.IGNORECASE),
    "Procedere": re.compile(r"^Procedere\s*:\s*$", re.IGNORECASE),
}


def split_into_sections(lines: List[str]) -> Dict[str, List[str]]:
    """
    Split content into sections by heading lines.
    Unmatched lines go into 'Text'.
    """
    sections: Dict[str, List[str]] = {"Text": []}
    current = "Text"
    
    for ln in lines:
        found_heading = None
        for name, pat in SECTION_HEADINGS.items():
            if pat.match(ln.strip()):
                found_heading = name
                break
        
        if found_heading:
            current = found_heading
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(ln)
    
    return sections


# -----------------------------
# Markdown output
# -----------------------------

def metadata_to_markdown_table(metadata: Dict[str, str]) -> str:
    """Format metadata as markdown table."""
    if not metadata:
        return ""
    
    lines = ["| Feld | Wert |", "|---|---|"]
    for key in sorted(metadata.keys()):
        value = metadata[key].replace("|", r"\|")
        lines.append(f"| {key} | {value} |")
    
    return "\n".join(lines)


def section_to_markdown(name: str, lines: List[str]) -> str:
    """Format section as markdown."""
    if not lines:
        return ""
    
    body = "\n".join(lines).strip()
    if not body:
        return ""
    
    return f"## {name}\n\n{body}\n\n"


def convert_pdf_to_markdown(pdf_path: str, min_words_threshold: int = 50) -> Tuple[str, Dict]:
    """
    Convert PDF to markdown with metadata extraction.
    Returns (markdown_text, extraction_info)
    """
    # Extract words with OCR fallback
    words, page_sizes, method = extract_words_with_fallback(pdf_path, min_words_threshold)
    
    print(f"  [3/3] Processing {len(words)} words from {len(page_sizes)} pages...")
    
    # Group into lines
    page_lines = group_words_into_lines(words, y_tol=3.0)
    text_lines = render_lines_to_text(page_lines)
    
    # Clean noise
    text_lines = remove_footer_noise(text_lines)
    text_lines = remove_repeated_lines(text_lines, threshold=2)
    
    # Extract metadata
    metadata, remaining_lines = extract_metadata(text_lines)
    
    # Split into sections
    sections = split_into_sections(remaining_lines)
    
    # Build markdown
    md_parts: List[str] = []
    md_parts.append("# OP-Bericht (Layout-aware Extraktion)\n")
    
    if metadata:
        md_parts.append("## Metadaten\n")
        md_parts.append(metadata_to_markdown_table(metadata))
        md_parts.append("\n")
    
    # Add sections in preferred order
    for section_name in ["Diagnose", "Operation", "Bericht", "Procedere"]:
        if section_name in sections and sections[section_name]:
            md_parts.append(section_to_markdown(section_name, sections[section_name]))
    
    # Add any remaining text
    if sections.get("Text"):
        md_parts.append(section_to_markdown("Text", sections["Text"]))
    
    markdown = "".join(md_parts).strip() + "\n"
    
    # Extraction info
    info = {
        "method": method,
        "total_words": len(words),
        "pages": len(page_sizes),
        "metadata_fields": list(metadata.keys()),
        "sections": [k for k, v in sections.items() if v],
    }
    
    return markdown, info


# -----------------------------
# CLI and pipeline integration
# -----------------------------

def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _backup_if_exists(path: Path) -> Optional[Path]:
    try:
        if not path.exists():
            return None
        bak = path.with_suffix(path.suffix + f".bak.{_timestamp()}")
        bak.write_bytes(path.read_bytes())
        return bak
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Layout-aware PDF extraction with OCR fallback")
    ap.add_argument("pdf", nargs="?", help="Path to PDF file")
    ap.add_argument("-o", "--out", help="Output markdown path")
    ap.add_argument("--doi-hash", help="DOI hash; reads data/<hash>/<hash>.pdf")
    ap.add_argument("--data-dir", default="data", help="Data directory (default: data)")
    ap.add_argument(
        "--pipeline-override",
        action="store_true",
        help="Write into pipeline files: <hash>_text.md and <hash>.md (backs up existing). Requires --doi-hash.",
    )
    ap.add_argument(
        "--min-words",
        type=int,
        default=50,
        help="Minimum words threshold to trigger OCR fallback (default: 50)",
    )
    ap.add_argument("--out-json", help="Optional: write extraction info as JSON")
    args = ap.parse_args()
    
    if not args.pdf and not args.doi_hash:
        ap.error("Provide pdf positional arg or --doi-hash")
    
    if args.doi_hash:
        data_dir = Path(args.data_dir)
        pdf_path = data_dir / str(args.doi_hash) / f"{args.doi_hash}.pdf"
    else:
        pdf_path = Path(args.pdf)
    
    if not pdf_path.exists():
        ap.error(f"PDF not found: {pdf_path}")
    
    print(f"[START] Processing {pdf_path.name}")
    
    # Convert
    markdown, info = convert_pdf_to_markdown(str(pdf_path), args.min_words)
    
    print(f"[OK] Extraction complete via {info['method']}")
    print(f"     Words: {info['total_words']}, Pages: {info['pages']}")
    print(f"     Metadata: {', '.join(info['metadata_fields']) if info['metadata_fields'] else 'none'}")
    print(f"     Sections: {', '.join(info['sections']) if info['sections'] else 'none'}")
    
    # Output
    if args.pipeline_override:
        if not args.doi_hash:
            ap.error("--pipeline-override requires --doi-hash")
        
        doi_dir = Path(args.data_dir) / str(args.doi_hash)
        text_md = doi_dir / f"{args.doi_hash}_text.md"
        combined_md = doi_dir / f"{args.doi_hash}.md"
        
        bak1 = _backup_if_exists(text_md)
        bak2 = _backup_if_exists(combined_md)
        
        text_md.write_text(markdown, encoding="utf-8")
        combined_md.write_text(markdown, encoding="utf-8")
        
        if bak1:
            print(f"[OK] Backed up {text_md.name} -> {bak1.name}")
        if bak2:
            print(f"[OK] Backed up {combined_md.name} -> {bak2.name}")
        print(f"[OK] Pipeline override wrote: {text_md} and {combined_md}")
    
    elif args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"[OK] Wrote {out_path}")
    
    else:
        print(markdown)
    
    # Optional JSON info
    if args.out_json:
        json_path = Path(args.out_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Wrote extraction info: {json_path}")


if __name__ == "__main__":
    main()
