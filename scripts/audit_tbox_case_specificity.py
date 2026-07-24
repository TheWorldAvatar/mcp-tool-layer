#!/usr/bin/env python3
"""Audit T-box rdfs:comments for case-specific leakage."""

from __future__ import annotations

import csv
import re
import argparse
from collections import defaultdict
from pathlib import Path

from rdflib import Graph, Namespace, RDFS

ROOT = Path(__file__).resolve().parents[1]
TBOX = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v3.ttl"
GOLD = ROOT / "evaluation" / "medical" / "medical_cases_new_20260710_all30_corrected.csv"
CASES_DIR = ROOT / "data_medical_new_cases"
MED = Namespace("https://www.theworldavatar.com/kg/medical/")


def load_tbox_comments(tbox_path: Path | None = None) -> list[tuple[str, str, str]]:
    graph = Graph()
    graph.parse(str(tbox_path or TBOX), format="turtle")
    rows: list[tuple[str, str, str]] = []
    for subject, _, header in graph.triples((None, MED.csvHeader, None)):
        for _, _, comment in graph.triples((subject, RDFS.comment, None)):
            rows.append((str(header), str(subject).split("/")[-1], str(comment)))
    return rows


def load_case_metadata() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with GOLD.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            h = row["_doi_hash"]
            rows[h] = {
                "name": row["Name"].strip(),
                "fall_nr": row["Fall-Nr"].strip(),
                "pdf": row["_ttl_file"].replace(".ttl", ".pdf"),
            }
    return rows


def load_case_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for case_dir in CASES_DIR.iterdir():
        if not case_dir.is_dir() or len(case_dir.name) != 8:
            continue
        stitched = list(case_dir.glob("*_stitched.md"))
        if stitched:
            sources[case_dir.name] = stitched[0].read_text(encoding="utf-8")
    return sources


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def extract_backtick_phrases(comment: str) -> list[str]:
    return re.findall(r"`([^`]+)`", comment)


def tokenize_name(name: str) -> list[str]:
    return [p for p in re.split(r"\s+", name.strip()) if len(p) >= 4]


def audit(tbox_path: Path | None = None) -> dict[str, list[dict[str, str]]]:
    comments = load_tbox_comments(tbox_path)
    metadata = load_case_metadata()
    sources = load_case_sources()

    names = {m["name"] for m in metadata.values()}
    name_tokens = {tok for name in names for tok in tokenize_name(name)}
    fall_nrs = {m["fall_nr"] for m in metadata.values() if m["fall_nr"]}
    pdfs = {m["pdf"] for m in metadata.values()}
    hashes = set(metadata.keys())

    findings: dict[str, list[dict[str, str]]] = defaultdict(list)

    # Precompute which long phrases appear in how many case sources.
    phrase_case_hits: dict[str, set[str]] = defaultdict(set)
    for field, iri, comment in comments:
        for phrase in extract_backtick_phrases(comment):
            norm = normalize(phrase)
            if len(norm) < 20:
                continue
            for h, src in sources.items():
                if norm in normalize(src):
                    phrase_case_hits[norm].add(h)

    for field, iri, comment in comments:
        lower = comment.lower()

        for name in names:
            if name.lower() in lower:
                findings["patient_name"].append(
                    {"field": field, "iri": iri, "match": name, "detail": comment[:220]}
                )

        for tok in name_tokens:
            if re.search(rf"\b{re.escape(tok.lower())}\b", lower):
                findings["name_token"].append(
                    {"field": field, "iri": iri, "match": tok, "detail": comment[:220]}
                )

        for fall in fall_nrs:
            if fall and fall in comment:
                findings["fall_nr"].append(
                    {"field": field, "iri": iri, "match": fall, "detail": comment[:220]}
                )

        for pdf in pdfs:
            if pdf.lower() in lower or pdf.replace(".pdf", "").lower() in lower:
                findings["pdf"].append(
                    {"field": field, "iri": iri, "match": pdf, "detail": comment[:220]}
                )

        for h in hashes:
            if h in lower:
                findings["hash"].append(
                    {"field": field, "iri": iri, "match": h, "detail": comment[:220]}
                )

        for phrase in extract_backtick_phrases(comment):
            norm = normalize(phrase)
            if len(norm) < 20:
                continue
            hits = phrase_case_hits.get(norm, set())
            if len(hits) == 1:
                h = next(iter(hits))
                findings["single_case_phrase"].append(
                    {
                        "field": field,
                        "iri": iri,
                        "match": phrase,
                        "case": metadata[h]["name"],
                        "hash": h,
                        "pdf": metadata[h]["pdf"],
                    }
                )
            elif len(hits) >= 2:
                findings["multi_case_phrase"].append(
                    {
                        "field": field,
                        "iri": iri,
                        "match": phrase,
                        "cases": ", ".join(sorted(metadata[x]["name"] for x in hits)),
                        "count": str(len(hits)),
                    }
                )
            else:
                findings["not_in_corpus"].append(
                    {"field": field, "iri": iri, "match": phrase}
                )

    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ttl",
        type=Path,
        default=TBOX,
        help="T-box TTL path to audit",
    )
    args = parser.parse_args()

    findings = audit(args.ttl)

    comments = load_tbox_comments(args.ttl)
    sources = load_case_sources()
    metadata = load_case_metadata()
    dispute_hashes = {
        "f29211f2",
        "3d11cbcc",
        "e288d25a",
        "933dc913",
        "33c5b4d2",
        "23a00605",
        "7db20b59",
        "4beb9c08",
        "d626d799",
        "55aa99e4",
        "6cc343f0",
        "13f6bdac",
        "e5371149",
        "b151acc8",
        "a44317c5",
        "4402a2e9",
        "c3bb4fd5",
    }

    stats = {"total": 0, "single": 0, "multi": 0, "none": 0, "single_dispute": 0}
    for _, _, comment in comments:
        for phrase in extract_backtick_phrases(comment):
            if len(phrase.strip()) < 12:
                continue
            stats["total"] += 1
            norm = normalize(phrase)
            hits = [h for h, src in sources.items() if norm in normalize(src)]
            if not hits:
                stats["none"] += 1
            elif len(hits) == 1:
                stats["single"] += 1
                if hits[0] in dispute_hashes:
                    stats["single_dispute"] += 1
            else:
                stats["multi"] += 1

    print("# T-box case-specific audit\n")
    print("## phrase_overlap_stats")
    for key, value in stats.items():
        print(f"- {key}: {value}")
    print()

    for key in [
        "patient_name",
        "name_token",
        "fall_nr",
        "pdf",
        "hash",
        "single_case_phrase",
    ]:
        rows = findings.get(key, [])
        print(f"## {key}: {len(rows)} hit(s)")
        if not rows:
            print("(none)\n")
            continue
        for row in rows:
            if key == "single_case_phrase" and row["hash"] in dispute_hashes:
                row = {**row, "dispute_case": "yes"}
            print(f"- field `{row['field']}` ({row['iri']})")
            for k, v in row.items():
                if k not in {"field", "iri"}:
                    print(f"  {k}: {v}")
        print()

    multi = findings.get("multi_case_phrase", [])
    print(f"## multi_case_phrase: {len(multi)} quoted phrase(s) found in 2+ case sources")
    for row in multi[:20]:
        print(
            f"- `{row['field']}`: **{row['match'][:80]}...** "
            f"({row['count']} cases: {row['cases']})"
            if len(row["match"]) > 80
            else f"- `{row['field']}`: **{row['match']}** ({row['count']} cases: {row['cases']})"
        )
    if len(multi) > 20:
        print(f"... and {len(multi) - 20} more")

    none_rows = findings.get("not_in_corpus", [])
    print(f"\n## not_in_corpus: {len(none_rows)} quoted phrase(s) not found in any stitched source")
    for row in none_rows[:20]:
        print(f"- `{row['field']}`: **{row['match']}**")


if __name__ == "__main__":
    main()
