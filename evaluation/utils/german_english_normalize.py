"""Deterministic German/English normalization for free-text comparison.

This is not a translator. It folds German orthography to ASCII and maps
common clinical German/English lemmas onto one token so umlaut spellings
and ordinary bilingual variants compare equal.
"""

from __future__ import annotations

import re
import unicodedata

_DIAERESIS = "\u0308"

# After German/ASCII folding and casefold. Longest key wins.
_LEMMAS: dict[str, str] = {
    "adhaesiolyse": "adhesiolysis",
    "adhaesionen": "adhesion",
    "adhaesion": "adhesion",
    "adhesions": "adhesion",
    "adhesion": "adhesion",
    "pleurolyse": "pleurolysis",
    "thorakoskopisch": "thoracoscopic",
    "thorakoskopie": "thoracoscopy",
    "mediastinoskopie": "mediastinoscopy",
    "mediastinal": "mediastinal",
    "hyperhidrose": "hyperhidrosis",
    "hyperhidrosis": "hyperhidrosis",
    "karzinose": "carcinosis",
    "karzinom": "carcinoma",
    "carcinoma": "carcinoma",
    "metastasen": "metastases",
    "metastase": "metastasis",
    "metastases": "metastases",
    "metastasis": "metastasis",
    "pneumothorax": "pneumothorax",
    "haematothorax": "hematothorax",
    "hematothorax": "hematothorax",
    "haemothorax": "hematothorax",
    "empyem": "empyema",
    "empyema": "empyema",
    "thymom": "thymoma",
    "thymoma": "thymoma",
    "resektion": "resection",
    "resection": "resection",
    "ektomie": "ectomy",
    "ectomy": "ectomy",
    "skopie": "scopy",
    "scopy": "scopy",
    "graphie": "graphy",
    "graphy": "graphy",
    "tomie": "tomy",
    "tomy": "tomy",
    "lyse": "lysis",
    "lysis": "lysis",
    "brustwand": "chestwall",
    "chestwall": "chestwall",
    "brustfell": "pleura",
    "pleura": "pleura",
    "pleural": "pleural",
    "pulmonal": "lung",
    "pulmonary": "lung",
    "lungen": "lung",
    "lunge": "lung",
    "lungs": "lung",
    "lung": "lung",
    "rippen": "rib",
    "rippe": "rib",
    "ribs": "rib",
    "rib": "rib",
    "beidseits": "bilateral",
    "beidseitig": "bilateral",
    "bilateral": "bilateral",
    "links": "left",
    "rechts": "right",
    "left": "left",
    "right": "right",
}

_LEMMA_KEYS = tuple(sorted(_LEMMAS, key=len, reverse=True))
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold_german_orthography(text: str) -> str:
    """Fold umlauts, ß, and leftover Latin diacritics to ASCII."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    chars: list[str] = []
    index = 0
    while index < len(decomposed):
        current = decomposed[index]
        nxt = decomposed[index + 1] if index + 1 < len(decomposed) else ""
        if nxt == _DIAERESIS and current.lower() in {"a", "o", "u"}:
            chars.append({"a": "ae", "o": "oe", "u": "ue"}[current.lower()])
            index += 2
            continue
        if current in {"ß", "ẞ"}:
            chars.append("ss")
            index += 1
            continue
        chars.append(current)
        index += 1
    folded = unicodedata.normalize("NFD", "".join(chars))
    return "".join(
        char for char in folded if unicodedata.category(char) != "Mn"
    )


def normalize_german_english(text: str) -> str:
    """Canonical comparable form for German/English clinical strings."""
    folded = fold_german_orthography(text).casefold()
    compact = _NON_ALNUM.sub("", folded)
    if not compact:
        return ""
    parts: list[str] = []
    unknown: list[str] = []
    index = 0

    def flush() -> None:
        if unknown:
            parts.append("".join(unknown))
            unknown.clear()

    while index < len(compact):
        hit = next((key for key in _LEMMA_KEYS if compact.startswith(key, index)), None)
        if hit is None:
            unknown.append(compact[index])
            index += 1
            continue
        flush()
        parts.append(_LEMMAS[hit])
        index += len(hit)
    flush()
    return " ".join(sorted(parts))


def same_german_english_text(left: str | None, right: str | None) -> bool:
    """True when both sides are non-empty and match after DE/EN normalization."""
    a = normalize_german_english(left or "")
    b = normalize_german_english(right or "")
    if not a or not b:
        return False
    return a == b
