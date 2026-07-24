#!/usr/bin/env python3
"""Deep recheck of T-box comments for evaluation-case leakage."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from rdflib import Graph, Namespace, RDFS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TTL = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"
CASES_DIR = ROOT / "data_medical_new_cases"
MED = Namespace("https://www.theworldavatar.com/kg/medical/")

KNOWN_BAD = [
    "Entscheid zur Thorakotomie",
    "ob Metastase oder Primarius kann nicht differenziert werden",
    "Hyperhidrosis manuum",
    "Hyperhidrose manuum",
    "Evakuation des Hämatoms",
    "Thorakoskopische Hämatomausräumung aus der Pleurahöhle",
    "thorakoskopische Hämatomausräumung",
    "Gangrän und Nekrose der Lunge",
    "Alexis-Folie",
    "PleurX",
    "komplette Thymektomie",
    "Einlage einer Thoraxdrainage",
    "Atypische Lungenresektion: Keilresektion, mehrfach, offen chirurgisch",
    "Meyer, Claudia",
    "Markierung zur uniportal-VATS",
    "da Vinci",
    "daVinci",
    "uniportal-VATS",
    "S6-Resektion",
    "S6 Rx",
]

# Schema/role labels that naturally appear in OP headers; not case leakage.
ALLOWLIST_BACKTICK = {
    "hämatothorax",
    "operateur/in",
    "assistenz",
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def load_sources() -> list[str]:
    sources: list[str] = []
    for case_dir in CASES_DIR.iterdir():
        if not case_dir.is_dir() or len(case_dir.name) != 8:
            continue
        stitched = list(case_dir.glob("*_stitched.md"))
        if stitched:
            sources.append(normalize(stitched[0].read_text(encoding="utf-8")))
    return sources


def load_comments(ttl_path: Path) -> list[tuple[str, str]]:
    graph = Graph()
    graph.parse(str(ttl_path), format="turtle")
    rows: list[tuple[str, str]] = []
    for subject, _, header in graph.triples((None, MED.csvHeader, None)):
        for _, _, comment in graph.triples((subject, RDFS.comment, None)):
            rows.append((str(header), str(comment)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ttl", type=Path, default=DEFAULT_TTL)
    args = parser.parse_args()

    comments = load_comments(args.ttl)
    sources = load_sources()
    all_text = "\n".join(c for _, c in comments)

    print(f"Checked {args.ttl}")
    print(f"Comments: {len(comments)} | Sources: {len(sources)}")
    print()

    print("=== known-bad phrase presence in v4 comments ===")
    known_hits = 0
    for phrase in KNOWN_BAD:
        present = phrase.lower() in all_text.lower()
        print(("HIT" if present else "ok "), phrase)
        known_hits += int(present)
    print(f"known-bad hits: {known_hits}")
    print()

    print("=== backtick phrases still in corpus (len>=12) ===")
    backtick_hits: list[tuple[str, str]] = []
    for field, comment in comments:
        for phrase in re.findall(r"`([^`]+)`", comment):
            norm = normalize(phrase)
            if len(norm) < 12:
                continue
            if any(norm in src for src in sources):
                backtick_hits.append((field, phrase))
    print(f"count: {len(backtick_hits)}")
    for field, phrase in backtick_hits:
        print(f"- `{field}`: {phrase}")
    print()

    print("=== long plain-text windows (8+ words) exact in corpus ===")
    window_hits: list[tuple[str, str]] = []
    for field, comment in comments:
        plain = re.sub(r"`[^`]+`", " ", comment)
        words = re.findall(r"\w[\w\-äöüÄÖÜß/]*", plain)
        found = False
        for n in (10, 8):
            for i in range(0, max(0, len(words) - n + 1)):
                window = " ".join(words[i : i + n]).lower()
                if len(window) < 40:
                    continue
                if any(window in src for src in sources):
                    window_hits.append((field, window))
                    found = True
                    break
            if found:
                break
    print(f"count: {len(window_hits)}")
    for field, window in window_hits[:30]:
        print(f"- `{field}`: {window[:140]}")


if __name__ == "__main__":
    main()
