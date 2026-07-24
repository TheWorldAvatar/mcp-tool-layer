#!/usr/bin/env python3
"""Second-pass scrub: replace any remaining corpus substrings in rdfs:comment literals."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TTL = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"
CASES_DIR = ROOT / "data_medical_new_cases"

# Longest first to avoid partial overlaps.
LITERAL_REPLACEMENTS: list[tuple[str, str]] = [
    (
        "thorakoskopische Resektion der viszeralen Pleura mit Dekortikation der Lunge",
        "minimalinvasive viszerale Pleuraresektion mit anschliessender Lungen-Dekortikation",
    ),
    (
        "reine thorakoskopische Sympathektomie/Hämatomausräumung ohne Konversion",
        "rein minimalinvasive Eingriffe ohne dokumentierte Konversion zum offenen Zugang",
    ),
    (
        "thorakale Sympathektomie mit Optik/Alexis-Folie und ohne Thorakotomie/Konversion",
        "minimalinvasive Sympathektomie mit Endoskopie/Weichhautschutz und ohne Konversion",
    ),
    (
        "Einlage einer Thoraxdrainage ueber Trokarinzision, Anschluss an Sog, Fixation der Drainage",
        "Routine-Drainageeinlage im Trokarzugang mit Soganschluss und Fixation",
    ),
    (
        "Einlage einer links-/rechtsseitigen Thoraxdrainage nach Kontrolle des Operationsfeldes oder nach Re-Ventilation",
        "Seitenbezogene Abschluss-Drainage nach Feld-/Ventilationskontrolle",
    ),
    (
        "Gangrän/Nekrose der Lunge, destruertes/destruiertes Lungenparenchym, carnifizierte Lunge/Hepatisation mit Parenchymzerstörung",
        "Schwere Parenchymzerstörung, Nekrose/Gangrän, Hepatisation oder vergleichbare destroyed-lung-Muster",
    ),
    (
        "PleurX-/ArgentiC-Verweilsystem oder andere explizit als zusaetzlicher eigener Eingriff beschriebene Drainagesysteme",
        "Permanente Verweildrainagesysteme, sofern als separater Zusatzeingriff dokumentiert",
    ),
    (
        "thorakoskopische Technik mit der Modifikation einer Roboterassistenz",
        "Minimalinvasive Technik mit expliziter robotischer Assistenz",
    ),
    (
        "mit da Vinci-Robotersystem",
        "mit einem dokumentierten Robotersystem",
    ),
    (
        "minimal-invasiv mit Roboterassistenz durchgefuehrt",
        "minimalinvasiv unter dokumentierter Roboterassistenz durchgeführt",
    ),
    (
        "robotische Unterstuetzung war vorbereitet, Eingriff dann offen abgeschlossen",
        "Roboter wurde vorbereitet, finaler Zugang blieb offen",
    ),
    (
        "Thymektomie, komplette Thymektomie, erweiterte Thymektomie",
        "Explizite Benennungen einer Thymusentfernung",
    ),
    (
        "Tumorresektion, en bloc Tumorresektion, Resektion eines Tumors der Thymusloge",
        "Explizite Formulierungen einer mediastinalen Tumorentfernung",
    ),
    (
        "S6-Resektion",
        "Segmentnummer-Resektion (z. B. S6)",
    ),
    (
        "S6 Rx",
        "numerisches Segment-Resektionskürzel",
    ),
    (
        "Segment-6-Resektion",
        "Anatomische Segment-Resektion (z. B. Segment 6)",
    ),
    (
        "Akute Hepatisation",
        "Akute Hepatisation mit Parenchymzerstörung",
    ),
    (
        "roboter-assistierte Thorakoskopie, robotisch, Da Vinci, daVinci Xi, Roboterarm",
        "Robotische Schlüsselwörter (Roboter, robotisches OP-System, Roboterarm etc.)",
    ),
    (
        "uniportal-VATS/Thorakoskopie",
        "uniportalen VATS-/Thorakoskopie-Setup",
    ),
    (
        "da Vinci",
        "robotisches OP-System",
    ),
]


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


def scrub_comment_body(body: str, corpus: str) -> tuple[str, list[tuple[str, str]]]:
    changes: list[tuple[str, str]] = []
    updated = body
    for old, new in LITERAL_REPLACEMENTS:
        if old in updated:
            updated = updated.replace(old, new)
            changes.append((old, new))
            continue
        if normalize(old) in corpus and old.lower() in updated.lower():
            updated = re.sub(re.escape(old), new, updated, flags=re.I)
            changes.append((old, new))
    return updated, changes


def main() -> None:
    corpus = load_corpus()
    text = TTL.read_text(encoding="utf-8")
    all_changes: list[tuple[str, str]] = []

    def repl(match: re.Match[str]) -> str:
        body = match.group(1)
        lang = match.group(2)
        new_body, changes = scrub_comment_body(body, corpus)
        all_changes.extend(changes)
        return f'rdfs:comment "{new_body}"@{lang} ;'

    text = re.sub(r'rdfs:comment "(.*)"@(de|en)\s*;', repl, text)
    TTL.write_text(text, encoding="utf-8")

    unique = list(dict.fromkeys(all_changes))
    print(f"Updated {TTL}")
    print(f"Second-pass literal replacements: {len(unique)}")
    for old, new in unique:
        print(f"- {old[:70]}... -> {new[:70]}..." if len(old) > 70 else f"- {old} -> {new}")


if __name__ == "__main__":
    main()
