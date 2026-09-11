"""Sanitize paper source markdown for stable downstream filenames / CCDC / KG.

Applied at PDF→MD write time (and stitch) so Greek letters, super/subscripts,
middle dots, and other non-ASCII chemistry glyphs become ASCII-safe text.
"""

from __future__ import annotations

import re
import unicodedata

# Explicit maps kept for clarity / speed; NFKC + unicodedata.name cover the rest.
_GREEK_LETTER_MAP = {
    "Α": "Alpha",
    "Β": "Beta",
    "Γ": "Gamma",
    "Δ": "Delta",
    "Ε": "Epsilon",
    "Ζ": "Zeta",
    "Η": "Eta",
    "Θ": "Theta",
    "Ι": "Iota",
    "Κ": "Kappa",
    "Λ": "Lambda",
    "Μ": "Mu",
    "Ν": "Nu",
    "Ξ": "Xi",
    "Ο": "Omicron",
    "Π": "Pi",
    "Ρ": "Rho",
    "Σ": "Sigma",
    "Τ": "Tau",
    "Υ": "Upsilon",
    "Φ": "Phi",
    "Χ": "Chi",
    "Ψ": "Psi",
    "Ω": "Omega",
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "ο": "omicron",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "ς": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    # Common variant forms seen in PDF extraction
    "ɑ": "alpha",
    "ϐ": "beta",
    "ϑ": "theta",
    "ϕ": "phi",
    "ϖ": "pi",
    "ϱ": "rho",
    "ϵ": "epsilon",
    "ϰ": "kappa",
}

_SUPER_SUB_MAP = {
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
    "⁺": "+",
    "⁻": "-",
    "ⁿ": "n",
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
    "₊": "+",
    "₋": "-",
    "₌": "=",
    "₍": "(",
    "₎": ")",
}

_PUNCT_MAP = {
    "·": "-",
    "∙": "-",
    "•": "-",
    "⋅": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "‐": "-",
    "‑": "-",
    "′": "'",
    "″": '"',
    "‴": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "×": "x",
    "÷": "/",
    "±": "+/-",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "≈": "~",
    "℃": "C",
    "Å": "A",
    "µ": "u",  # micro sign (distinct from Greek mu; NFKC may already fold)
    "˚": "deg",
    "°": "deg",
    "‰": "%o",
    "…": "...",
    "\u00a0": " ",  # NBSP
    "\u202f": " ",  # narrow NBSP
    "\u2007": " ",  # figure space
    "\u2008": " ",
    "\u2009": " ",
    "\u200a": " ",
}

_ZERO_WIDTH_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff\u00ad]"
)  # ZW*, WJ, BOM, soft hyphen
_REPLACEMENT_CHAR_RE = re.compile("\ufffd+")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_GREEK_NAME_RE = re.compile(
    r"^GREEK (CAPITAL|SMALL) LETTER ([A-Z]+)(?: WITH .+)?$"
)


def _greek_from_unicodedata_name(char: str) -> str | None:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    match = _GREEK_NAME_RE.match(name)
    if not match:
        return None
    letter = match.group(2).title()
    if match.group(1) == "SMALL":
        return letter.lower()
    return letter


def sanitize_source_markdown(text: str) -> str:
    """Normalize source markdown body text to ASCII-safe chemistry-friendly form.

    Preserves markdown structure and ordinary punctuation. Replaces Greek letters,
    super/subscripts, middle dots, fancy dashes/quotes, and strips zero-width /
    replacement characters that commonly break filenames, CCDC lookups, and logs.
    """
    if not text:
        return ""

    # NFKC folds many compatibility superscripts/subscripts and fullwidth forms.
    out = unicodedata.normalize("NFKC", str(text))
    out = _ZERO_WIDTH_RE.sub("", out)
    out = _REPLACEMENT_CHAR_RE.sub("", out)

    buf: list[str] = []
    for char in out:
        if char in _GREEK_LETTER_MAP:
            buf.append(_GREEK_LETTER_MAP[char])
            continue
        if char in _SUPER_SUB_MAP:
            buf.append(_SUPER_SUB_MAP[char])
            continue
        if char in _PUNCT_MAP:
            buf.append(_PUNCT_MAP[char])
            continue
        greek = _greek_from_unicodedata_name(char)
        if greek is not None:
            buf.append(greek)
            continue
        # Drop remaining non-printable controls except tab/newline/carriage-return.
        code = ord(char)
        if code < 32 and char not in "\t\n\r":
            continue
        if code == 127:
            continue
        buf.append(char)

    cleaned = "".join(buf)
    # Collapse runs of spaces created by replacements, but keep newlines intact.
    cleaned = "\n".join(
        _MULTI_SPACE_RE.sub(" ", line).rstrip() for line in cleaned.splitlines()
    )
    if text.endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned
