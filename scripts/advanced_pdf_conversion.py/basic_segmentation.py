#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Layout-aware PDF -> Markdown for OP reports.
- Uses PyMuPDF (fitz) to read words with x/y coordinates
- Reconstructs lines by y clustering, then inserts spaces by x gaps
- Detects common header key-value fields (Fall-Nr, OP-Datum, OP-Nummer, etc.)
- Outputs Markdown with a metadata table + body sections

Install:
  pip install PyMuPDF

Run:
  python basic_segmentation.py "/path/to/OP Bericht 1.pdf" -o out.md
"""

from __future__ import annotations
import re
import argparse
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

try:
    import fitz  # PyMuPDF
except ImportError as e:
    raise RuntimeError("PyMuPDF (fitz) is required. Install with: pip install PyMuPDF") from e

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    pytesseract = None


# ---------------------------
# Layout reconstruction
# ---------------------------

@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


def extract_words(page) -> List[Word]:
    """Extract words using PyMuPDF's get_text('words')."""
    words: List[Word] = []
    for x0, y0, x1, y1, txt, block_num, line_num, word_num in page.get_text("words"):
        t = str(txt).strip()
        if not t:
            continue
        words.append(Word(text=t, x0=float(x0), x1=float(x1), top=float(y0), bottom=float(y1)))
    return words


def extract_words_ocr(page, dpi: int = 300) -> List[Word]:
    """
    Extract words using OCR (tesseract) on rendered page.
    Fallback for when text layer is poor or missing.
    """
    if not OCR_AVAILABLE or pytesseract is None:
        raise RuntimeError(
            "OCR requires pytesseract and Pillow. Install with: pip install pytesseract pillow\n"
            "Also install Tesseract binary: https://github.com/tesseract-ocr/tesseract"
        )
    
    words: List[Word] = []
    
    # Render page to image
    mat = fitz.Matrix(dpi / 72, dpi / 72)  # scale for DPI
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    
    # Page size in PDF units
    page_width = page.rect.width
    page_height = page.rect.height
    
    # OCR with word-level bounding boxes (German language)
    try:
        ocr_data = pytesseract.image_to_data(
            img,
            lang="deu",
            output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        # If German language model not found, try English
        print(f"    [WARN] OCR with German failed ({e}), trying English...")
        ocr_data = pytesseract.image_to_data(
            img,
            lang="eng",
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
        
        words.append(Word(text=text.strip(), x0=x0, x1=x1, top=top, bottom=bottom))
    
    return words


def cluster_lines(words: List[Word], y_tol: float = 2.5) -> List[List[Word]]:
    """
    Group words into lines by y coordinate (top), tolerant to small jitter.
    """
    if not words:
        return []

    # sort by y then x
    words = sorted(words, key=lambda t: (t.top, t.x0))

    lines: List[List[Word]] = []
    current: List[Word] = [words[0]]
    current_y = words[0].top

    for wd in words[1:]:
        if abs(wd.top - current_y) <= y_tol:
            current.append(wd)
        else:
            lines.append(sorted(current, key=lambda t: t.x0))
            current = [wd]
            current_y = wd.top

    lines.append(sorted(current, key=lambda t: t.x0))
    return lines


def rebuild_line(words_in_line: List[Word], gap_tol: float = 6.0) -> str:
    """
    Insert spaces based on x gaps between adjacent words.
    gap_tol controls how aggressive spacing is; increase if words get concatenated.
    """
    if not words_in_line:
        return ""

    parts = [words_in_line[0].text]
    prev = words_in_line[0]

    for wd in words_in_line[1:]:
        gap = wd.x0 - prev.x1
        # If far enough, insert a space; if extremely far, insert two spaces (helps table-ish blocks)
        if gap > gap_tol:
            parts.append(" ")
            if gap > gap_tol * 4:
                parts.append(" ")
        else:
            # hyphenation / punctuation glue rules
            if (parts[-1].endswith("-")):
                # join hyphenated wrap
                pass
            elif re.match(r"^[\.,;:\)\]]$", wd.text):
                # no space before punctuation
                pass
            elif re.match(r"^[\(\[]$", wd.text):
                # space before opening bracket is ok if there is already one
                parts.append(" ")
            else:
                parts.append(" ")

        parts.append(wd.text)
        prev = wd

    line = "".join(parts)

    # fix common artifacts
    line = re.sub(r"\s+", " ", line).strip()
    return line


def page_to_lines(page, y_tol: float = 2.5, gap_tol: float = 6.0) -> List[str]:
    words = extract_words(page)
    lines = cluster_lines(words, y_tol=y_tol)
    text_lines = [rebuild_line(ln, gap_tol=gap_tol) for ln in lines]
    # drop empty + obvious footer noise if needed later
    return [t for t in text_lines if t.strip()]


# ---------------------------
# Semantic normalization layer (key-value extraction)
# ---------------------------

FIELD_PATTERNS = [
    ("Name", re.compile(r"^(?P<k>Name)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)),
    # Fall-Nr can have trailing text (location, date, etc.), so extract just the number
    ("Fall-Nr", re.compile(r"^(?P<k>Fall[- ]?Nr\.?)\s*[:\-]?\s*(?P<v>\d{6,})", re.IGNORECASE)),
    ("OP-Datum", re.compile(r"^(?P<k>OP[- ]?Datum)\s*[:\-]?\s*(?P<v>\d{2}\.\d{2}\.\d{4})$", re.IGNORECASE)),
    ("OP-Nummer", re.compile(r"^(?P<k>OP[- ]?Nummer)\s*[:\-]?\s*(?P<v>\d{6,})$", re.IGNORECASE)),
    ("OP-Saal", re.compile(r"^(?P<k>OP[- ]?Saal)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)),
    ("Operateur/in", re.compile(r"^(?P<k>Operateur\/In|Operateur\/in|Operateur)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)),
    ("Assistenz", re.compile(r"^(?P<k>Assistenz)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)),
    ("Schnitt", re.compile(r"^(?P<k>Schnitt)\s*[:\-]?\s*(?P<v>\d{2}:\d{2}\s*Uhr)?$", re.IGNORECASE)),
    ("Naht", re.compile(r"^(?P<k>Naht)\s*[:\-]?\s*(?P<v>\d{2}:\d{2}\s*Uhr)?$", re.IGNORECASE)),
]

SECTION_HEADINGS = {
    "Diagnose": re.compile(r"^Diagnose\s*:\s*$", re.IGNORECASE),
    "Operation": re.compile(r"^Operation\s*:\s*$", re.IGNORECASE),
    "Bericht": re.compile(r"^Bericht\s*:\s*$", re.IGNORECASE),
    "Procedere": re.compile(r"^Procedere\s*:\s*$", re.IGNORECASE),
}

# some PDFs place "Fall-Nr." label on left and value far right/bottom; catch lone numbers too
FALLNR_FUZZY = re.compile(r"^\d{6,}$")


def extract_header_fields(lines: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """
    Extract key-value header fields where possible; return (fields, remaining_lines).
    Strategy:
      - Look for explicit "Key: Value" lines
      - Also capture a plausible Fall-Nr numeric token if we saw "Fall-Nr" label nearby
    """
    fields: Dict[str, str] = {}
    remaining: List[str] = []

    saw_fallnr_label = False

    for ln in lines:
        # detect label-only line
        if re.match(r"^Fall[- ]?Nr\.?$", ln.strip(), re.IGNORECASE):
            saw_fallnr_label = True
            continue

        matched = False
        for fname, pat in FIELD_PATTERNS:
            m = pat.match(ln.strip())
            if m:
                v = (m.group("v") or "").strip()
                # normalize key names
                key_norm = fname
                if v:
                    fields[key_norm] = v
                matched = True
                break

        if matched:
            continue

        # fuzzy Fall-Nr: if label occurred earlier and we see a long integer later
        if saw_fallnr_label and "Fall-Nr" not in fields and FALLNR_FUZZY.match(ln.strip()):
            fields["Fall-Nr"] = ln.strip()
            continue

        remaining.append(ln)

    return fields, remaining


def split_sections(lines: List[str]) -> Dict[str, List[str]]:
    """
    Split content into sections by heading lines like 'Diagnose:' etc.
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
            continue

        sections.setdefault(current, []).append(ln)

    return sections


def clean_footer_noise(lines: List[str]) -> List[str]:
    """
    Remove common footer/header noise while keeping medical content intact.
    Adjust patterns for your document family.
    """
    drop_patterns = [
        re.compile(r"^Druckdatum\s*:", re.IGNORECASE),
        re.compile(r"^Seite\s+\d+\/\d+", re.IGNORECASE),
        re.compile(r"^Patient\s*:\s*\*?\s*Fall\s*:", re.IGNORECASE),
        re.compile(r"^Hauptstraße\s+\d+", re.IGNORECASE),
    ]
    out = []
    for ln in lines:
        if any(p.search(ln) for p in drop_patterns):
            continue
        out.append(ln)
    return out


# ---------------------------
# Markdown writer
# ---------------------------

def md_escape(s: str) -> str:
    # minimal escaping for tables
    return s.replace("|", r"\|").strip()


def fields_to_md_table(fields: Dict[str, str]) -> str:
    if not fields:
        return ""
    rows = []
    for k in sorted(fields.keys()):
        rows.append(f"| {md_escape(k)} | {md_escape(fields[k])} |")
    return "\n".join([
        "| Feld | Wert |",
        "|---|---|",
        *rows,
        ""
    ])


def section_to_md(name: str, lines: List[str]) -> str:
    if not lines:
        return ""
    body = "\n".join(lines).strip()
    if not body:
        return ""
    return f"## {name}\n\n{body}\n\n"


def convert_pdf_to_markdown(pdf_path: str, use_ocr_fallback: bool = True, min_words_threshold: int = 50) -> str:
    """
    Convert PDF to markdown with optional OCR fallback.
    
    Args:
        pdf_path: Path to PDF file
        use_ocr_fallback: If True, use OCR when text layer is poor (default: True)
        min_words_threshold: Minimum words per page to consider text layer good (default: 50)
    
    Returns:
        Markdown string
    """
    all_lines: List[str] = []
    with fitz.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf):
            # Try text extraction first
            words = extract_words(page)
            
            # Check if OCR fallback is needed
            if use_ocr_fallback and OCR_AVAILABLE and len(words) < min_words_threshold:
                print(f"    [WARN] Page {page_num + 1}: Only {len(words)} words extracted, using OCR fallback...")
                try:
                    words = extract_words_ocr(page)
                    print(f"    [OK] Page {page_num + 1}: OCR extracted {len(words)} words")
                except Exception as e:
                    print(f"    [WARN] Page {page_num + 1}: OCR failed ({e}), using text layer")
            
            # Group words into lines
            lines_from_page: List[List[Word]] = []
            if words:
                words = sorted(words, key=lambda w: (w.top, w.x0))
                for w in words:
                    placed = False
                    for line in lines_from_page:
                        if abs(w.top - line[0].top) <= 2.5:
                            line.append(w)
                            placed = True
                            break
                    if not placed:
                        lines_from_page.append([w])
                
                # Sort words within each line and render
                for line_words in lines_from_page:
                    line_words.sort(key=lambda w: w.x0)
                    line_text = rebuild_line(line_words, gap_tol=6.0)
                    if line_text:
                        all_lines.append(line_text)

    # de-noise
    all_lines = clean_footer_noise(all_lines)

    # header fields extraction
    fields, rest = extract_header_fields(all_lines)

    # sectioning
    sections = split_sections(rest)

    # build markdown
    md_parts: List[str] = []
    md_parts.append("# OP-Bericht (Markdown-Extraktion)\n")

    table = fields_to_md_table(fields)
    if table:
        md_parts.append("## Metadaten\n")
        md_parts.append(table)

    # keep original reading order for sections, but show common ones first if present
    for sec in ["Diagnose", "Operation", "Bericht", "Procedere"]:
        if sec in sections:
            md_parts.append(section_to_md(sec, sections[sec]))

    # any remaining text
    if sections.get("Text"):
        md_parts.append(section_to_md("Text", sections["Text"]))

    return "".join(md_parts).strip() + "\n"


def main():
    import time
    from pathlib import Path
    
    ap = argparse.ArgumentParser(description="Layout-aware PDF to Markdown with OCR fallback.")
    ap.add_argument("pdf", nargs="?", default=None, help="Path to PDF")
    ap.add_argument("-o", "--out", default=None, help="Output Markdown file path")
    ap.add_argument("--doi-hash", default=None, help="DOI hash; reads data/<hash>/<hash>.pdf")
    ap.add_argument("--data-dir", default="data", help="Data directory (default: data)")
    ap.add_argument(
        "--pipeline-override",
        action="store_true",
        help="Write into pipeline files: <hash>_text.md and <hash>.md (backs up existing). Requires --doi-hash.",
    )
    ap.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR fallback (use only text layer extraction)",
    )
    ap.add_argument(
        "--min-words",
        type=int,
        default=50,
        help="Minimum words per page threshold for OCR fallback (default: 50)",
    )
    args = ap.parse_args()

    if not args.pdf and not args.doi_hash:
        ap.error("Provide pdf positional arg or --doi-hash")

    if args.doi_hash:
        data_dir = Path(args.data_dir)
        doi_hash = str(args.doi_hash)
        pdf_path = data_dir / doi_hash / f"{doi_hash}.pdf"
    else:
        pdf_path = Path(args.pdf)

    if not pdf_path.exists():
        ap.error(f"PDF not found: {pdf_path}")

    print(f"[START] Processing {pdf_path.name}")
    if not args.no_ocr and OCR_AVAILABLE:
        print(f"  [INFO] OCR fallback enabled (threshold: {args.min_words} words/page)")
    elif not args.no_ocr:
        print(f"  [WARN] OCR not available (install: pip install pytesseract pillow)")
    
    md = convert_pdf_to_markdown(
        str(pdf_path),
        use_ocr_fallback=not args.no_ocr,
        min_words_threshold=args.min_words
    )

    if args.pipeline_override:
        if not args.doi_hash:
            ap.error("--pipeline-override requires --doi-hash")
        doi_dir = Path(args.data_dir) / str(args.doi_hash)
        text_md = doi_dir / f"{args.doi_hash}_text.md"
        combined_md = doi_dir / f"{args.doi_hash}.md"

        def backup_if_exists(p: Path) -> Optional[Path]:
            if not p.exists():
                return None
            ts = time.strftime("%Y%m%d_%H%M%S")
            bak = p.with_suffix(p.suffix + f".bak.{ts}")
            bak.write_bytes(p.read_bytes())
            return bak

        bak1 = backup_if_exists(text_md)
        bak2 = backup_if_exists(combined_md)

        text_md.write_text(md, encoding="utf-8")
        combined_md.write_text(md, encoding="utf-8")

        if bak1:
            print(f"[OK] Backed up {text_md.name} -> {bak1.name}")
        if bak2:
            print(f"[OK] Backed up {combined_md.name} -> {bak2.name}")
        print(f"[OK] Pipeline override wrote: {text_md} and {combined_md}")
    elif args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[OK] Wrote {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()