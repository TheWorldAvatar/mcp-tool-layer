#!/usr/bin/env python3
"""Inject T-box rdfs:comment blocks into medical_disputes_examples_en_de.md."""

from __future__ import annotations

import re
from pathlib import Path

from extract_tbox_comments_for_disputes import (
    DEFAULT_TTL,
    bold_backtick_quotes,
    load_comments,
)

ROOT = Path(__file__).resolve().parents[1]
DISPUTES_MD = (
    ROOT
    / "data_medical_new_cases"
    / "evaluation_results"
    / "medical_disputes_examples_en_de.md"
)

# Section heading prefix -> csvHeader field names (in display order).
SECTION_FIELDS: dict[str, list[str]] = {
    "## 1. Approach conversion": ["offen", "VATS"],
    "## 2. Uncertain metastasis": ["Metastasen"],
    "## 3. Local / partial pleurectomy": ["Pleurektomie (5-344.1-2 u 4-5)"],
    "## 4. Isolated `Hämatothorax`": ["Thoraxtrauma (Hämatothorax, Rippenfraktur)"],
    "## 5. Secondary diagnoses": ["sonst. (Diagnose)"],
    "## 6. `Thymektomie` vs": [
        "Thymom",
        "Mediastinaltumorresektion (5-342)",
        "Thymektomie",
    ],
    "## 7. Pathology `R0`": ["R0"],
    "## 8. `Pleurodese` vs": [
        "Pleurodese (5-345 ohne 5-345.1 und ohne 5-345.4)",
        "Pleurektomie (5-344.1-2 u 4-5)",
        "Pneumothorax (Diagnose)",
    ],
    "## 9. `Segmentresektion`": [
        "Segmenresektion (5-323.4-7)",
        "atypische Resektion (5-322)",
    ],
    "## 10. `Art der Metastasen`": ["Metastasen", "Art der Metastasen"],
    "## 11. Drainage coding": ["Thoraxdrainageneinlage (8-144.0 und 5.340.0)"],
    "## 12. Empyema diagnosis": ["Empyem (Diagnose)"],
}

TBOX_HEADER = "**Current T-box rules** (quoted phrases show class boundaries applied by the extractor):"


def extract_quoted_phrases(comment: str) -> list[str]:
    return re.findall(r"`([^`]+)`", comment)


def render_tbox_section(fields: list[str], comments: dict[str, str]) -> str:
    parts = ["", TBOX_HEADER, ""]
    for field in fields:
        comment = comments.get(field)
        if not comment:
            parts.append(f"*T-box comment missing for `{field}`.*")
            parts.append("")
            continue
        body = bold_backtick_quotes(comment)
        phrases = list(dict.fromkeys(extract_quoted_phrases(comment)))
        parts.append(f"**`{field}`**")
        parts.append("")
        parts.append(f"> {body.replace(chr(10), chr(10) + '> ')}")
        parts.append("")
        if phrases:
            parts.append("Quoted boundary phrases from T-box:")
            for phrase in phrases:
                parts.append(f"- **{phrase}**")
            parts.append("")
    return "\n".join(parts)


def strip_existing_tbox_blocks(text: str) -> str:
    text = re.sub(r"\n<!-- TBOX:START -->.*?<!-- TBOX:END -->\n", "\n", text, flags=re.DOTALL)
    pattern = rf"\n\n{re.escape(TBOX_HEADER)}.*?(?=\n### Example|\n## |\Z)"
    return re.sub(pattern, "", text, flags=re.DOTALL)


def inject_tbox_blocks(text: str, comments: dict[str, str]) -> str:
    text = strip_existing_tbox_blocks(text)
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)

        section_key = next((k for k in SECTION_FIELDS if line.startswith(k)), None)
        if section_key:
            i += 1
            while i < len(lines) and not lines[i].startswith("### Example"):
                out.append(lines[i])
                i += 1
            i -= 1

            block = render_tbox_section(SECTION_FIELDS[section_key], comments)
            out.extend(block.splitlines())
            out.append("")

        i += 1

    return "\n".join(out) + "\n"


def update_legend(text: str) -> str:
    legend_row = (
        "| **T-box** | Ontology `rdfs:comment` from v4 T-box; "
        "**bold** phrases are generic boundary examples (not verbatim case text) |"
    )
    # Drop any older T-box legend rows before ensuring the current one exists.
    text = re.sub(r"\n\| \*\*T-box\*\* \|[^\n]*\|", "", text)
    return text.replace(
        "| **Pred** | Model extraction |",
        "| **Pred** | Model extraction |\n" + legend_row,
    )


def main() -> None:
    comments = load_comments(DEFAULT_TTL)
    text = DISPUTES_MD.read_text(encoding="utf-8")
    text = update_legend(text)
    text = inject_tbox_blocks(text, comments)
    text = re.sub(r"\n{3,}", "\n\n", text)
    DISPUTES_MD.write_text(text, encoding="utf-8")
    print(f"Updated {DISPUTES_MD}")


if __name__ == "__main__":
    main()
