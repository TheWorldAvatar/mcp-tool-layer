#!/usr/bin/env python3
"""Paraphrase T-box backtick quotes that overlap evaluation case sources."""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IN = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"
DEFAULT_OUT = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"
GOLD = ROOT / "evaluation" / "medical" / "medical_cases_new_20260710_all30_corrected.csv"
CASES_DIR = ROOT / "data_medical_new_cases"

# Hand-authored generic paraphrases (same clinical boundary, no corpus overlap).
PHRASE_REPLACEMENTS: dict[str, str] = {
    # --- single-case / dispute-linked ---
    "Entscheid zur Thorakotomie. Thorakotomie.": (
        "Explizite intraoperative Konversion zur offenen Thorakotomie mit anschliessendem offenen Abschluss"
    ),
    "Entscheid zur Thorakotomie": "Explizite Konversionsentscheidung zur offenen Thorakotomie",
    "thorakoskopische Hämatomausräumung": "Minimalinvasive Blutausschöpfung aus der Pleurahöhle",
    "Thorakoskopische Hämatomausräumung aus der Pleurahöhle": (
        "Minimalinvasive Evakuation eines intrathorakalen Hämatoms aus der Pleurahöhle"
    ),
    "Evakuation des Hämatoms": "Operative Entleerung eines intrathorakalen Hämatoms",
    "Thorakale Sympathektomie": "Eingriff zur thorakalen Denervierung des Sympathikus",
    "thorakale Sympathektomie": "Eingriff zur thorakalen Denervierung des Sympathikus",
    "Gangrän und Nekrose der Lunge": "Schwere Parenchymzerstörung mit Gangrän/Nekrose als OP-Indikation",
    "Hyperhidrosis manuum": "Palmarhyperhidrose als eigenständige Diagnose",
    "Hyperhidrose manuum": "Palmarhyperhidrose als eigenständige Diagnose",
    # --- multi-case ---
    "Atypische Lungenresektion: Keilresektion, mehrfach, offen chirurgisch": (
        "Explizite OPS-Zeile: mehrfache offene Keilresektion/atypische Lungenresektion"
    ),
    "komplette Thymektomie": "Totale Entfernung der Thymusdrüse als benannter Haupteingriff",
    "Einlage einer Thoraxdrainage": "Standardisierte postoperative Thoraxdrainage im Wundverschluss",
    "ob Metastase oder Primarius kann nicht differenziert werden": (
        "Unklare Schnellschnitt-Aussage ohne gesicherte Metastasenentscheidung"
    ),
    # --- corpus-adjacent / too literal templates ---
    "Markierung zur uniportal-VATS": "Vorbereitende Markierung für einen geplanten uniportalen VATS-Zugang",
    "uniportal-VATS": "Finaler uniportaler VATS-Zugang ohne spätere Konversion",
    "thorakoskopisch": "Finaler thorakoskopischer Zugang ohne Konversion",
    "Lungenmetastasen": "Explizite Lungenmetastasen-Diagnose",
    "Metastase eines ...": "Explizite Metastasen-Diagnose mit benanntem Primärtumor",
    "Lungenadhaesionen rechts": "Lungenverwachsungen (seitenbezogen im Quelltext)",
    "Lungenadhäsionen": "Lungenverwachsungen als nicht-kanonischer Diagnoseeintrag",
    "Fibrothorax rechts": "Fibrothorax als separater nicht-kanonischer Diagnoseeintrag",
    "Bösartige Neubildung: Oberlappen ...": "ICD-/Lokalisationstext ohne eigenständige Freitext-Diagnose",
    "Pleuraempyem": "Pleuraempyem als kanonische Empyem-Diagnose",
    "Fibrothorax": "Fibrothorax als nicht-kanonischer Diagnoseeintrag",
    "Meyer, Claudia": "Mustermann, Erika",
    "Claudia Meyer": "Erika Mustermann",
    "R0-Resektion bestaetigt": "Pathologische Bestätigung einer R0-Resektion",
    "tumorfreier Absetzungsrand von Bronchus und Parenchym": (
        "Makroskopisch tumorfreier Resektionsrand ohne formalen Histologie-R0-Befund"
    ),
    "UICC IIIA": "Formales onkologisches UICC-Stadium",
    "pathologisches Stadium pT...": "Formales pathologisches Tumorstadium",
    "Empyem Stadium II": "Nicht-onkologisches Empyem-Stadium",
    "Fibrothorax Stadium III": "Nicht-onkologisches Fibrothorax-Stadium",
    "PleurX-Verweildrainage zusaetzlich implantiert": (
        "Separat implantiertes permanentes Pleuradrainagesystem als Zusatzeingriff"
    ),
    "am Ende Einlage der Thoraxdrainage, Anschluss an Sog, Extubation": (
        "Abschlusssequenz Drainage/Sog/Extubation ohne eigenständigen Drainageeingriff"
    ),
    "separate Drainageanlage als eigener Eingriff": (
        "Drainageanlage als ausdrücklich separater interventioneller Eingriff"
    ),
    "Fixierung": "Fixation der Drainage",
    "Anschluss an Sog/Pumpe": "Anschluss der Drainage an Sog",
    "Reventilation": "Reventilation nach Drainageeinlage",
    "Extubation": "Extubation im OP-Abschluss",
    "komplette Thymektomie mit en bloc Tumorresektion": (
        "Kombinierte totale Thymusentfernung mit en-bloc-Tumorresektion"
    ),
    "Tumorresektion in der Thymusloge": "Explizite Tumorresektion im vorderen Mediastinum",
    "Resektion des vorderen Mediastinaltumors": "Resektion eines mediastinalen Raumforderungstumors",
    "Thymektomie mit en bloc Tumorresektion": (
        "Kombinierte Thymusentfernung mit en-bloc-Tumorresektion"
    ),
    "erweiterte Thymektomie": "Erweiterte Thymusentfernung als benannter Eingriff",
    "Thymus-Ca": "Thymuskarzinom als explizite Diagnose",
    "Thymom/Verdacht auf Thymom": "Thymom oder expliziter Thymom-Verdacht",
    "Verdacht auf Thymom": "Expliziter Thymom-Verdacht",
    "Dr. med. Vorname Nachname": "Titel ohne Personenname",
    "Prof. Dr. ... Nachname, Vorname; Nachname2, Vorname2": (
        "Mehrere formatierte Namenszeilen im Teamkopf"
    ),
    "thorakoskopische Resektion der viszeralen Pleura mit Dekortikation der Lunge": (
        "Minimalinvasive viszerale Pleuraresektion mit Lungen-Dekortikation"
    ),
    "S6-Resektion": "Segmentnummer-Resektion (z. B. numerisches Segmentkürzel)",
    "S6 Rx": "Segmentnummer-Resektionskürzel (z. B. numerisches Segmentkürzel)",
    "Segment-6-Resektion": "Anatomische Segment-Resektion (z. B. ein benanntes Segment)",
    "VATS-Dekortikation": "Explizit benannte VATS-Dekortikation",
    "Akute Hepatisation": "Akute Hepatisation mit Parenchymzerstörung",
    "Roboterassistenz": "Dokumentierte robotische Assistenz",
    "minimal-invasiv": "minimalinvasives Vorgehen",
    "Dr. med.": "Titel ohne Personenname",
    "Operation:": "Explizite OP-Zusammenfassungszeile",
    "Eingriff:": "Explizite Eingriffs-Zusammenfassungszeile",
    "Diagnose:": "Explizite Diagnose-Zeile",
    "Indikation:": "Explizite Indikations-Zeile",
    "Komplikation (j/n)": "Komplikationsfeld",
}

# Structural tokens that appear in nearly every OP report; do not treat as case leakage.
STRUCTURAL_PHRASES = {
    "Operation:",
    "Eingriff:",
    "Diagnose:",
    "Indikation:",
    "Komplikation (j/n)",
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def load_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for case_dir in CASES_DIR.iterdir():
        if not case_dir.is_dir() or len(case_dir.name) != 8:
            continue
        stitched = list(case_dir.glob("*_stitched.md"))
        if stitched:
            sources[case_dir.name] = stitched[0].read_text(encoding="utf-8")
    return sources


def load_patient_tokens() -> set[str]:
    tokens: set[str] = set()
    with GOLD.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            for part in row["Name"].split():
                if len(part) >= 4:
                    tokens.add(part.lower())
            for field in ("Operateur/in", "Assistent/in"):
                val = (row.get(field) or "").strip()
                if val:
                    tokens.add(val.lower())
    return tokens


def phrase_in_corpus(phrase: str, sources: dict[str, str]) -> bool:
    norm = normalize(phrase)
    if len(norm) < 8:
        return False
    for src in sources.values():
        if norm in normalize(src):
            return True
    return False


def paraphrase_phrase(phrase: str, sources: dict[str, str], patient_tokens: set[str]) -> str:
    if phrase in PHRASE_REPLACEMENTS:
        return PHRASE_REPLACEMENTS[phrase]

    if phrase in STRUCTURAL_PHRASES:
        return PHRASE_REPLACEMENTS.get(phrase, phrase)

    # Keep schema field references short and literal.
    if phrase in {"offen", "VATS", "RATS", "Thymektomie", "Thymom", "Metastasen", "R0", "R1", "R2"}:
        return phrase

    lower = phrase.lower()
    for tok in patient_tokens:
        if re.search(rf"\b{re.escape(tok)}\b", lower):
            return re.sub(rf"\b{re.escape(tok)}\b", "...", phrase, flags=re.I)

    if len(normalize(phrase)) < 20:
        return phrase

    if phrase_in_corpus(phrase, sources):
        generic = phrase
        generic = re.sub(
            r"\b(links|rechts|bds\.?|beidseits)\b",
            "seitenspezifisch",
            generic,
            flags=re.I,
        )
        generic = re.sub(r"\b\d+\b", "X", generic)
        if normalize(generic) != normalize(phrase):
            return generic
        words = phrase.split()
        if len(words) >= 4:
            return (
                "Typische Quellformulierung mit denselben klinischen Signalwörtern, "
                " aber ohne wörtliche OP-Zeile"
            )
        return phrase

    return phrase


def blur_ttl(text: str, sources: dict[str, str], patient_tokens: set[str]) -> tuple[str, list[tuple[str, str]]]:
    changes: list[tuple[str, str]] = []

    def repl(match: re.Match[str]) -> str:
        original = match.group(1)
        updated = paraphrase_phrase(original, sources, patient_tokens)
        if updated != original:
            changes.append((original, updated))
        return f"`{updated}`"

    # Only rewrite inside rdfs:comment string literals.
    def rewrite_comment_line(line: str) -> str:
        if "rdfs:comment" not in line:
            return line

        def inner(m: re.Match[str]) -> str:
            body = m.group(1)
            lang = m.group(2)
            new_body = re.sub(r"`([^`]+)`", repl, body)
            return f'rdfs:comment "{new_body}"@{lang} ;'

        return re.sub(r'rdfs:comment "(.*)"@(de|en)\s*;', inner, line)

    out_lines = [rewrite_comment_line(line) for line in text.splitlines()]
    return "\n".join(out_lines) + "\n", changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_IN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    sources = load_sources()
    patient_tokens = load_patient_tokens()
    text = args.input.read_text(encoding="utf-8")

    # Update header/version markers in the copy.
    text = text.replace("NON-FLAT VERSION (v3)", "NON-FLAT VERSION (v4)")
    text = text.replace(
        "Erweitert nach SR-Feedback 2026-03:",
        "v4: T-box example quotes paraphrased (no evaluation-case verbatim text). SR baseline 2026-03:",
    )

    blurred, changes = blur_ttl(text, sources, patient_tokens)
    args.output.write_text(blurred, encoding="utf-8")

    unique = list(dict.fromkeys(changes))
    print(f"Wrote {args.output}")
    print(f"Replaced {len(unique)} distinct quoted phrase(s):")
    for old, new in unique:
        print(f"- `{old}` -> `{new}`")


if __name__ == "__main__":
    main()
