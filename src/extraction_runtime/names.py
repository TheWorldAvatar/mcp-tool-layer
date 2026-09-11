"""Stable hash and filesystem names used by every runtime step."""

from __future__ import annotations

import hashlib
import re
import unicodedata


def generate_hash(doi: str) -> str:
    """Return the 8-character SHA-256 prefix used as the per-paper folder name."""
    return hashlib.sha256(str(doi).encode("utf-8")).hexdigest()[:8]


def entity_artifact_name(label: str) -> str:
    """Return the stable label-derived name used by extraction artifacts."""
    normalized = unicodedata.normalize("NFKC", str(label or "entity"))
    for character in [":", "：", "﹕", "∶", "꞉", "︰", "\uf03a"]:
        normalized = normalized.replace(character, ":")
    normalized = (
        normalized.replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
        .replace("Α", "Alpha")
        .replace("Β", "Beta")
        .replace("Γ", "Gamma")
        .replace("Δ", "Delta")
    )
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    safe_label = re.sub(r"_+", "_", safe_label).strip("_") or "entity"
    if len(safe_label) > 64:
        digest = hashlib.sha256(str(label or "").encode("utf-8")).hexdigest()[:12]
        safe_label = f"{safe_label[:48].rstrip('._-')}--{digest}"
    return safe_label


def entity_scope_name(label: str, uri: str) -> str:
    """Return a filesystem-safe, collision-resistant entity scope."""
    normalized = unicodedata.normalize("NFKC", str(label or "entity"))
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    safe_label = re.sub(r"_+", "_", safe_label).strip("._") or "entity"
    safe_label = safe_label[:32].rstrip("._") or "entity"
    uri_hash = hashlib.sha256(str(uri or "").encode("utf-8")).hexdigest()[:12]
    return f"{safe_label}--{uri_hash}"


def local_name(iri: str) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]
