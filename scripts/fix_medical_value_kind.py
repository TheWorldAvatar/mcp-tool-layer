#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

from rdflib import OWL, RDF, Graph, Namespace

TTL = Path("medical_case/medical_case_schema_de_non_flat_v3.ttl")
FIX = [
    "VATS",
    "VATS_Dekortikation_5_344_3_5_345_4",
    "Fistel",
    "Haematothorax",
    "Empyem_Komplikation",
    "Bronchusstumpf_Anastomoseninsuffizienz",
    "Pneumonie",
    "kardiovaskulaer",
    "Niereninsuffizienz",
    "Wundheilungsstoerung",
    "Tod",
    "Drainage_Punktion",
    "Reintubation",
    "Transfusion",
    "Endoskopie",
    "Reoperation",
    "resp_Insuffizienz",
]


def main() -> None:
    text = TTL.read_text(encoding="utf-8")
    for name in FIX:
        pat = rf'(med:{re.escape(name)}\s+a\s+owl:DatatypeProperty\s*;[\s\S]*?med:valueKind\s+")([^"]+)(")'
        text2, n = re.subn(pat, r"\1binary_checklist\3", text, count=1)
        print(("fixed" if n else "MISS"), name)
        text = text2
    TTL.write_text(text, encoding="utf-8")
    g = Graph()
    g.parse(TTL)
    med = Namespace("https://www.theworldavatar.com/kg/medical/")
    for name in ["VATS", "offen", "sonst_Eingriff", "Destroyed_Lung", "VATS_Dekortikation_5_344_3_5_345_4"]:
        print(name, list(g.objects(med[name], med.valueKind)))


if __name__ == "__main__":
    main()
