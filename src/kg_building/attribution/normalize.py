"""Value fingerprints shared by atom matching and the ledger heuristic."""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDERS = {
    "",
    "n/a",
    "na",
    "n.a.",
    "none",
    "null",
    "-",
    "-1",
    "-1.0",
    "-1e+00",
    "-1e+0",
    "-1.00",
}

_UNICODE_MAP = str.maketrans(
    {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
        "·": ".",
        "•": ".",
        "⋅": ".",
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "μ": "mu",
        "µ": "mu",
        "\u00b5": "mu",
        "\u03bc": "mu",
    }
)


def is_placeholder(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return text in _PLACEHOLDERS


def fingerprint(value: Any) -> str:
    """Collapse a GT/Pred/ledger span for presence checks."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [fingerprint(item) for item in value]
        return " | ".join(part for part in parts if part)
    if isinstance(value, dict):
        return fingerprint(sorted(str(item) for item in value.values()))
    text = str(value).strip()
    if is_placeholder(text):
        return ""
    text = text.translate(_UNICODE_MAP)
    text = (
        text.replace("\u2034", "\u2033")
        .replace("\u2032", "'")
        .replace("\u2033", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = " ".join(text.split()).casefold()
    text = text.replace("''", '"').replace('""', '"')
    text = re.sub(r"\s*,\s*", ", ", text)
    return text


def value_tokens(value: Any) -> list[str]:
    """Split amounts such as ``17.5 mg, 0.06 mmol`` into searchable tokens."""
    if isinstance(value, (list, tuple, set)):
        tokens: list[str] = []
        for item in value:
            for token in value_tokens(item):
                if token not in tokens:
                    tokens.append(token)
        return tokens
    text = str(value or "").strip()
    if not text or is_placeholder(text):
        return []
    chunks = [part.strip() for part in re.split(r"[;,/]| and ", text) if part.strip()]
    tokens = [fingerprint(text)]
    for chunk in chunks:
        fp = fingerprint(chunk)
        if fp and fp not in tokens:
            tokens.append(fp)
    return [token for token in tokens if token]
