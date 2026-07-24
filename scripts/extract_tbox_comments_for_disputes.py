#!/usr/bin/env python3
"""Extract rdfs:comment text from medical T-box by csvHeader field name."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from rdflib import Graph, Namespace, RDFS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TTL = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"
MED = Namespace("https://www.theworldavatar.com/kg/medical/")


def load_comments(ttl_path: Path) -> dict[str, str]:
    graph = Graph()
    graph.parse(str(ttl_path), format="turtle")
    comments: dict[str, str] = {}
    for subject, _, header in graph.triples((None, MED.csvHeader, None)):
        for _, _, comment in graph.triples((subject, RDFS.comment, None)):
            comments[str(header)] = str(comment)
    return comments


def bold_backtick_quotes(text: str) -> str:
    return re.sub(r"`([^`]+)`", r"**\1**", text)


def format_markdown_block(field: str, comment: str) -> str:
    body = bold_backtick_quotes(comment)
    return f"**T-box comment — `{field}`**\n\n> {body.replace(chr(10), chr(10) + '> ')}\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ttl", type=Path, default=DEFAULT_TTL)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write markdown blocks to this file (UTF-8). Defaults to stdout.",
    )
    parser.add_argument("fields", nargs="*", help="csvHeader field names")
    args = parser.parse_args()

    comments = load_comments(args.ttl)
    lines: list[str] = []
    for field in args.fields:
        comment = comments.get(field)
        if comment is None:
            lines.append(f"MISSING: {field}\n")
            continue
        lines.append(format_markdown_block(field, comment))

    output = "".join(lines)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
