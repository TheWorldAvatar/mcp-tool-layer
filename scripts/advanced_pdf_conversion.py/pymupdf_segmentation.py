#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Quick PyMuPDF-based visual block inspection for OP reports.

This is intentionally simple: it opens a PDF, walks through each page,
and prints the visual text blocks (bounding boxes + text) so you can
see how PyMuPDF groups the layout.

Additionally (Option A, improved): key/value pairing with a more robust
"same line" test that tolerates small y-misalignment by using vertical
overlap (not just y-centers) and a slightly larger y tolerance.

Run from the repo root, for example:

  python scripts/advanced_pdf_conversion.py/pymupdf_segmentation.py ^
      "raw_data/OP Bericht 1.pdf"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, Dict, Any, List, Tuple, Optional

try:
    import fitz  # PyMuPDF
except ImportError as e:
    raise RuntimeError("PyMuPDF (fitz) is required. Install with: pip install PyMuPDF") from e


BBox = Tuple[float, float, float, float]


def _bbox_union(b1: Optional[BBox], b2: BBox) -> BBox:
    if b1 is None:
        return b2
    return (min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3]))


def _bbox_height(b: BBox) -> float:
    return max(0.0, b[3] - b[1])


def _y_overlap(a: BBox, b: BBox) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _same_visual_line(a: BBox, b: BBox, *, y_tol: float, min_overlap_ratio: float) -> bool:
    """
    Robust same-line test:
    - allow small y-offsets by checking vertical overlap ratio
    - and fallback to baseline proximity via y0 difference
    """
    ha = max(1.0, _bbox_height(a))
    hb = max(1.0, _bbox_height(b))
    overlap = _y_overlap(a, b)

    # Primary: overlap relative to the smaller height
    if overlap >= min_overlap_ratio * min(ha, hb):
        return True

    # Fallback: top alignment within tolerance (handles thin bboxes)
    if abs(a[1] - b[1]) <= y_tol:
        return True

    return False


def iter_text_blocks(page: "fitz.Page") -> Iterable[Dict[str, Any]]:
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if "lines" not in block:
            continue

        bbox_raw = block.get("bbox", (0, 0, 0, 0))
        bbox: BBox = (float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3]))

        lines: List[str] = []
        for line in block.get("lines", []):
            parts: List[str] = []
            for span in line.get("spans", []):
                txt = (span.get("text") or "").strip()
                if txt:
                    parts.append(txt)
            if parts:
                lines.append(" ".join(parts))

        text = "\n".join(lines).strip()
        if not text:
            continue

        yield {"bbox": bbox, "text": text}


def iter_text_lines_with_tight_bbox(page: "fitz.Page") -> Iterable[Dict[str, Any]]:
    """
    Yield line-level items with:
      - 'text'
      - 'bbox' (line bbox)
      - 'tight_bbox' (union of span bboxes with non-empty text)
    """
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if "lines" not in block:
            continue

        for line in block.get("lines", []):
            line_bbox_raw = line.get("bbox", (0, 0, 0, 0))
            line_bbox: BBox = (
                float(line_bbox_raw[0]),
                float(line_bbox_raw[1]),
                float(line_bbox_raw[2]),
                float(line_bbox_raw[3]),
            )

            parts: List[str] = []
            tight: Optional[BBox] = None

            for span in line.get("spans", []):
                txt = (span.get("text") or "")
                txt_stripped = txt.strip()
                if not txt_stripped:
                    continue
                parts.append(txt_stripped)

                sb = span.get("bbox", None)
                if sb is not None and len(sb) == 4:
                    sbbox: BBox = (float(sb[0]), float(sb[1]), float(sb[2]), float(sb[3]))
                    tight = _bbox_union(tight, sbbox)

            text = " ".join(parts).strip()
            if not text:
                continue

            if tight is None:
                tight = line_bbox

            yield {"text": text, "bbox": line_bbox, "tight_bbox": tight}


def extract_kv_pairs(page: "fitz.Page") -> Dict[str, str]:
    """
    Option A: Extract key/value pairs using spatial proximity.

    Improvements for your specific issue:
    - "same line" uses vertical overlap ratio + y0 tolerance, so 2–3 units y drift still matches
    - limits horizontal search distance to avoid pairing across unrelated regions
    - Fall-Nr. prefers numeric-only value
    """
    lines = list(iter_text_lines_with_tight_bbox(page))
    if not lines:
        return {}

    page_h = float(page.rect.height)

    # Allow a bit more y drift; your example differs by 2.3 units.
    y_tol = max(8.0, 0.010 * page_h)          # ~8–9 on A4
    min_overlap_ratio = 0.20                  # tolerate partial overlap due to bbox quirks
    max_dx_default = 260.0                    # don't jump across to far right column
    overlap_tol = 3.0                         # allow slight horizontal overlap

    known_keys = {"Fall-Nr."}

    def is_potential_key(t: str) -> bool:
        t = t.strip()
        return bool(t) and (t in known_keys or ":" in t)

    items: List[Dict[str, Any]] = []
    for it in lines:
        items.append(
            {
                "text": it["text"].strip(),
                "tight_bbox": it["tight_bbox"],
                "is_key": is_potential_key(it["text"]),
            }
        )

    digit_re = re.compile(r"^\d{4,}$")

    def find_value_for_key(key_item: Dict[str, Any], *, prefer_digits: bool = False) -> Optional[str]:
        kb = key_item["tight_bbox"]
        kx_end = kb[2]

        # Keep Fall-Nr. local in the left info box; allow enough to reach the value,
        # but not enough to jump to the right column.
        max_dx = 220.0 if key_item["text"] in known_keys else max_dx_default

        best_score = float("inf")
        best_text: Optional[str] = None

        for cand in items:
            if cand is key_item:
                continue
            if cand["is_key"]:
                continue

            cb = cand["tight_bbox"]

            # Must be on same visual line (robust to slight y misalignment)
            if not _same_visual_line(kb, cb, y_tol=y_tol, min_overlap_ratio=min_overlap_ratio):
                continue

            # Must be to the right (allow tiny overlap)
            dx = cb[0] - kx_end
            if dx < -overlap_tol:
                continue
            if dx > max_dx:
                continue

            cand_text = cand["text"]
            if prefer_digits and not digit_re.match(cand_text.replace(" ", "")):
                continue

            # Score: closest to the right wins
            score = max(0.0, dx)

            # Avoid long paragraph values for short keys
            if len(cand_text) > 60:
                score += 200.0

            if score < best_score:
                best_score = score
                best_text = cand_text

        return best_text

    kv: Dict[str, str] = {}

    for it in items:
        if not it["is_key"]:
            continue

        text = it["text"]

        if ":" in text:
            left, right = text.split(":", 1)
            key = left.strip()
            val_inline = right.strip()

            if val_inline:
                kv[key] = val_inline
                continue

            # "Key:" with empty inline value
            val = find_value_for_key(it, prefer_digits=False)
            if val:
                kv[key] = val
            continue

        if text in known_keys:
            val = find_value_for_key(it, prefer_digits=True)
            if val:
                kv[text] = val

    return kv


def inspect_pdf_blocks(pdf_path: Path) -> None:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    print(f"[INFO] Inspecting blocks for: {pdf_path}")

    with fitz.open(str(pdf_path)) as doc:
        total_pages = doc.page_count
        for page_index in range(total_pages):
            page = doc.load_page(page_index)

            print("=" * 80)
            print(f"Page {page_index + 1} / {total_pages}")
            print("=" * 80)

            for i, block in enumerate(iter_text_blocks(page), start=1):
                x0, y0, x1, y1 = block["bbox"]
                text_preview = block["text"].replace("\n", " ⏎ ")
                if len(text_preview) > 200:
                    text_preview = text_preview[:200] + "…"

                print(f"[Block {i:03d}] bbox=({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})")
                print(f"  {text_preview}")
                print("-" * 80)

            kv = extract_kv_pairs(page)
            if kv:
                print("[INFO] Inferred key/value pairs (Option A):")
                for k, v in kv.items():
                    print(f"  - {k}: {v}")
            else:
                print("[INFO] No key/value pairs inferred on this page.")
            print()


def convert_pdf_to_markdown(pdf_path: str) -> str:
    """
    Convert a PDF into a markdown representation using visual text blocks.

    Note:
    - The medical pipeline depends on this function.
    - We include layout positions (bbox) and inferred key/value pairs because they
      are important signals for downstream extraction.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    out_lines: List[str] = []

    with fitz.open(str(path)) as doc:
        total_pages = doc.page_count
        for page_index in range(total_pages):
            page = doc.load_page(page_index)

            # Page header
            out_lines.append(f"# Page {page_index + 1} / {total_pages}")
            out_lines.append("")

            # Layout-aware blocks with positions (bbox)
            out_lines.append("## Blocks (with positions)")
            out_lines.append("")

            any_block = False
            for i, block in enumerate(iter_text_blocks(page), start=1):
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                any_block = True

                x0, y0, x1, y1 = block["bbox"]
                out_lines.append(f"- **Block {i:03d}** bbox=({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})")
                out_lines.append("")
                out_lines.append("```")
                out_lines.append(text)
                out_lines.append("```")
                out_lines.append("")

            if not any_block:
                out_lines.append("_No text blocks extracted on this page._")
                out_lines.append("")

            # Key/value inference (Option A)
            kv = extract_kv_pairs(page)
            out_lines.append("## [INFO] Inferred key/value pairs (Option A)")
            out_lines.append("")
            if kv:
                for k, v in kv.items():
                    out_lines.append(f"- **{k}**: {v}")
            else:
                out_lines.append("_None inferred on this page._")
            out_lines.append("")

            # Keep a lightweight plain-text view (helps LLMs not get lost in metadata)
            out_lines.append("## Plain text (blocks concatenated)")
            out_lines.append("")
            plain_parts: List[str] = []
            for block in iter_text_blocks(page):
                t = (block.get("text") or "").strip()
                if t:
                    plain_parts.append(t)
            if plain_parts:
                out_lines.append("\n\n".join(plain_parts))
            else:
                out_lines.append("")
            out_lines.append("")  # blank line between pages

    return "\n\n".join(out_lines).strip()


def convert_all_pdfs_in_raw_data(raw_dir: Path = Path("raw_data")) -> None:
    """
    Convenience helper: convert all PDFs in a folder (default: 'raw_data')
    to markdown files using this module's layout-aware conversion.

    For each `<name>.pdf` in `raw_data`, writes `<name>_text.md` next to it.
    """
    if not raw_dir.exists():
        print(f"[ERROR] Raw data directory not found: {raw_dir}")
        return

    pdf_files = sorted(raw_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"[WARN] No PDF files found in {raw_dir}")
        return

    print(f"[INFO] Converting {len(pdf_files)} PDF(s) in {raw_dir} to markdown using layout-aware segmentation.")

    for pdf_path in pdf_files:
        try:
            print(f"[INFO] Converting: {pdf_path}")
            md_content = convert_pdf_to_markdown(str(pdf_path))
            md_path = pdf_path.with_name(f"{pdf_path.stem}_text.md")
            md_path.write_text(md_content, encoding="utf-8")
            print(f"[OK] Wrote markdown: {md_path}")
        except Exception as e:
            print(f"[ERROR] Failed to convert {pdf_path}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simple PyMuPDF block visual inspection for OP report PDFs."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=None,
        help="Path to PDF. If omitted, defaults to 'raw_data/OP Bericht 1.pdf'.",
    )
    parser.add_argument(
        "--all-md",
        action="store_true",
        help="Convert all PDFs in 'raw_data' to '<name>_text.md' using layout-aware segmentation.",
    )
    parser.add_argument(
        "--raw-dir",
        type=str,
        default="raw_data",
        help="Directory containing PDFs for --all-md (default: 'raw_data').",
    )
    args = parser.parse_args()

    # Batch markdown conversion mode
    if args.all_md:
        convert_all_pdfs_in_raw_data(Path(args.raw_dir))
        return

    # Original inspection mode
    if args.pdf:
        pdf_path = Path(args.pdf)
    else:
        pdf_path = Path("raw_data") / "OP Bericht 1.pdf"

    inspect_pdf_blocks(pdf_path)


if __name__ == "__main__":
    main()