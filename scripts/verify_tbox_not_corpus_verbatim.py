#!/usr/bin/env python3
"""Verify no T-box comment text still matches evaluation case sources verbatim."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from rdflib import Graph, Namespace, RDFS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TTL = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"
CASES_DIR = ROOT / "data_medical_new_cases"
MED = Namespace("https://www.theworldavatar.com/kg/medical/")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def load_corpus() -> str:
    chunks: list[str] = []
    for case_dir in CASES_DIR.iterdir():
        if not case_dir.is_dir() or len(case_dir.name) != 8:
            continue
        stitched = list(case_dir.glob("*_stitched.md"))
        if stitched:
            chunks.append(stitched[0].read_text(encoding="utf-8"))
    return normalize("\n".join(chunks))


def load_comments(ttl_path: Path) -> list[tuple[str, str]]:
    graph = Graph()
    graph.parse(str(ttl_path), format="turtle")
    rows: list[tuple[str, str]] = []
    for subject, _, header in graph.triples((None, MED.csvHeader, None)):
        for _, _, comment in graph.triples((subject, RDFS.comment, None)):
            rows.append((str(header), str(comment)))
    return rows


def verify(ttl_path: Path) -> list[tuple[str, str, str]]:
    corpus = load_corpus()
    hits: list[tuple[str, str, str]] = []
    for field, comment in load_comments(ttl_path):
        for phrase in re.findall(r"`([^`]+)`", comment):
            if len(normalize(phrase)) >= 15 and normalize(phrase) in corpus:
                hits.append((field, phrase, "backtick"))
        for chunk in re.split(r"[.;]", comment):
            chunk_norm = normalize(chunk)
            if len(chunk_norm) >= 35 and chunk_norm in corpus:
                hits.append((field, chunk.strip()[:120], "sentence"))
    return hits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ttl", type=Path, default=DEFAULT_TTL)
    args = parser.parse_args()
    hits = verify(args.ttl)
    print(f"Verified {args.ttl}")
    print(f"Remaining verbatim corpus hits: {len(hits)}")
    for field, text, kind in hits:
        print(f"- [{kind}] `{field}`: {text}")


if __name__ == "__main__":
    main()
