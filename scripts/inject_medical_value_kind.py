#!/usr/bin/env python3
"""Inject med:valueKind annotations into the medical non-flat v3 TTL."""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, Literal, Namespace, URIRef

TTL = Path("medical_case/medical_case_schema_de_non_flat_v3.ttl")
MED = Namespace("https://www.theworldavatar.com/kg/medical/")
VALUE_KIND = MED.valueKind

FREE_TEXT_FALLBACK = {
    "sonst_Eingriff",
    "sonst_Diagnose",
    "Art_der_Metastasen",
    "Art_des_Mediastinaltumors",
    "Kommentar",
}
FREE_TEXT = {
    "Name",
    "Fall_Nr",
    "Geburtsdatum",
    "OP_Datum",
    "Entlassdatum",
    "praeop_TuKo",
    "Verweildauer",
    "Dauer_d_zwischen_TuKo_und_OP",
    "Operateur_in",
    "Assistent_in",
    "Stadium",
    "Clavien_Dindo",
    "Same_Day_Surgery",
}
DERIVED = {"Alter"}


def local_name(iri: URIRef | str) -> str:
    s = str(iri)
    return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def classify(name: str, comment: str) -> str:
    if name in DERIVED:
        return "derived"
    if name in FREE_TEXT_FALLBACK or "FREITEXT-FALLBACK" in comment or "free_text_fallback" in comment:
        return "free_text_fallback"
    if name in FREE_TEXT:
        return "free_text"
    if "BINARY CHECKLIST" in comment:
        return "binary_checklist"
    if 'Liegt vor: "1"' in comment or '="1"' in comment.replace(" ", "") or '"1" einzutragen' in comment:
        return "binary_checklist"
    if "sonst keine Eintragung" in comment or "sonst kein Eintrag" in comment:
        return "binary_checklist"
    if '"1"' in comment and ("eintragen" in comment.lower() or "liegt vor" in comment.lower()):
        return "binary_checklist"
    return "free_text"


def main() -> None:
    g = Graph()
    g.parse(TTL, format="turtle")
    kinds: dict[str, str] = {}
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        name = local_name(prop)
        existing = list(g.objects(prop, VALUE_KIND))
        if existing:
            kinds[name] = str(existing[0])
            continue
        comment = " ".join(str(c) for c in g.objects(prop, RDFS.comment))
        kinds[name] = classify(name, comment)

    text = TTL.read_text(encoding="utf-8")
    for name, kind in sorted(kinds.items()):
        # Skip if already present on this property block.
        pattern = rf"(med:{re.escape(name)}\s+a\s+owl:DatatypeProperty\s*;[\s\S]*?)(\n\n|\Z)"
        match = re.search(pattern, text)
        if not match:
            print(f"WARN: property block not found for {name}")
            continue
        block = match.group(1)
        if "med:valueKind" in block:
            continue
        # Insert before trailing comment or at end of property statements.
        if block.rstrip().endswith("."):
            new_block = block.rstrip()[:-1] + f' ;\n  med:valueKind "{kind}" .\n'
        else:
            new_block = block.rstrip() + f'\n  med:valueKind "{kind}" ;\n'
        text = text[: match.start(1)] + new_block + text[match.end(1) :]
        print(f"{name}: {kind}")

    TTL.write_text(text, encoding="utf-8")
    print(f"Updated {TTL}")


if __name__ == "__main__":
    main()
