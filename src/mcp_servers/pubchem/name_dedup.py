"""Deduplicate PubChem synonym dumps to unique chemical names.

The PubChem MCP previously returned every synonym plus a large physchem
payload. Extraction agents then pasted catalog purity grades and registry IDs
into hints. This helper always runs the deterministic junk filter, then an LLM
collapse using the same model as extraction. Set PUBCHEM_NAME_DEDUP_LLM=0 to
disable the LLM pass.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CACHE_VERSION = "pubchem-name-dedup.v4"
_DEFAULT_DEDUP_MODEL = "gpt-4.1"


def llm_name_dedup_enabled() -> bool:
    raw = os.environ.get("PUBCHEM_NAME_DEDUP_LLM", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _dedup_model() -> str:
    try:
        from src.extraction_runtime.models_map import get_extraction_model

        return get_extraction_model("advanced_model", default=_DEFAULT_DEDUP_MODEL)
    except Exception:
        return _DEFAULT_DEDUP_MODEL


def _resolve_use_llm(explicit: bool | None) -> bool:
    return llm_name_dedup_enabled() if explicit is None else bool(explicit)

_PURITY_GRADE = re.compile(
    r",\s*\d+(?:\.\d+)?\s*%(?:\s*[;,].*)?$",
    re.IGNORECASE,
)
_REGISTRY_PREFIX = re.compile(
    r"(?i)^\s*(?:dtxsid|dtxcid|unii|einecs|ccris|refchem|schembl|"
    r"chebi:|cas\b|nsc[- ]?|ec\s+\d)"
)
_CAS_ONLY = re.compile(r"^\d{2,7}-\d{2}-\d$")
_BARE_UNII = re.compile(r"^(?=.*\d)[A-Z0-9]{10}$")
_PERCENT_TOKEN = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_CATALOG_JUNK = re.compile(
    r"(?i)(?:"
    r"\beye[\s-]?wash(?:es)?\b|"
    r"\beyewash(?:es)?\b|"
    r"\beyesaline\b|"
    r"\beyesalve\b|"
    r"\bneutral-eyes\b|"
    r"\bsunscreen\b|"
    r"\b(?:hair|sun|skincare|nourishing)\s+(?:hair|serum)\b|"
    r"\bfirst[\s-]?aid\b|"
    r"\bskincare\b|"
    r"\bgel relajante\b|"
    r"\bflower remedies\b|"
    r"\bcertified reference material\b|"
    r"\bpharmaceutical secondary standard\b|"
    r"\bdiluent for\b|"
    r"\beye[\s-]?relief\b|"
    r"\beye[\s-]?irrigat|"
    r"\bpur-wash\b|"
    r"\bskin[\s-]?rinse\b|"
    r"\beye[\s-]?(?:stream|clean|lert)\b|"
    r"\bwith eyecup\b|"
    r"\bpure flow\b|"
    r"\beye wash station\b"
    r")"
)
_REPEAT_SPLIT = re.compile(r"\s*;\s*")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _cache_dir() -> Path:
    raw = os.environ.get("PUBCHEM_NAME_DEDUP_CACHE_DIR", "").strip()
    if raw:
        return Path(raw)
    return _repo_root() / "evaluation" / "cache" / "pubchem_name_dedup"


def _normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def split_name_blob(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        parts: list[str] = []
        for item in value:
            parts.extend(split_name_blob(item))
        return parts
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in _REPEAT_SPLIT.split(text) if part.strip()]


def is_redundant_name(name: str) -> bool:
    text = name.strip()
    if not text:
        return True
    if _CAS_ONLY.match(text):
        return True
    if _BARE_UNII.match(text.upper()):
        return True
    if _REGISTRY_PREFIX.match(text):
        return True
    if _PERCENT_TOKEN.search(text):
        return True
    if _PURITY_GRADE.search(text):
        return True
    if _CATALOG_JUNK.search(text):
        return True
    return False


def deterministic_name_filter(names: Iterable[Any], *, limit: int | None = None) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for raw in names:
        for part in split_name_blob(raw):
            cleaned = _PURITY_GRADE.sub("", part).strip(" .;,")
            if is_redundant_name(cleaned):
                continue
            key = _normalize_key(cleaned)
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(cleaned)
            if limit is not None and len(kept) >= limit:
                return kept
    return kept


def _cache_path(query: str, candidates: list[str]) -> Path:
    payload = json.dumps(
        {
            "version": CACHE_VERSION,
            "model": _dedup_model(),
            "query": query,
            "candidates": candidates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return _cache_dir() / f"{digest}.json"


def _read_cache(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    names = data.get("names")
    if not isinstance(names, list):
        return None
    return [str(item).strip() for item in names if str(item).strip()]


def _write_cache(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": CACHE_VERSION, "names": names},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _invoke_name_dedup(query: str, candidates: list[str]) -> list[str]:
    from src.extraction_runtime.llm import invoke_json

    model = _dedup_model()
    prompt = (
        "Collapse a PubChem synonym dump to a compact chemical-identity set.\n"
        "Return only JSON: {\"names\": [\"...\", \"...\"]}\n"
        "Rules:\n"
        "- Keep the query name when it is a valid chemical name.\n"
        "- Keep at most a few identity names: one common name, one real abbreviation, "
        "and one systematic or IUPAC name.\n"
        "- Drop product, brand, trade, store-brand, and consumer-product labels.\n"
        "- Drop catalog SKUs, purity grades, supplier stock text, and registry IDs "
        "(DTXSID, UNII, EINECS, CCRIS, RefChem, SCHEMBL, CAS-only strings).\n"
        "- Collapse spelling, punctuation, hydration-notation, and translation variants "
        "to one representative.\n"
        "- Do not invent names that are not in the candidates.\n"
        "- Return at most 12 names. Prefer fewer when extra names add no new identity.\n"
        "- Put the query name first when it is valid.\n"
        f"Query name: {query or '(none)'}\n"
        "Candidates:\n"
        + "\n".join(f"- {item}" for item in candidates)
    )
    result = invoke_json(prompt, model_name=model)
    raw_names = result.get("names")
    if not isinstance(raw_names, list):
        raise ValueError("LLM name dedup did not return a names list")
    return deterministic_name_filter(raw_names)


def dedup_pubchem_names(
    names: Iterable[Any],
    *,
    query: str = "",
    use_llm: bool | None = None,
) -> list[str]:
    """Return a compact name-only alias list with junk and variants removed."""
    seeded = list(split_name_blob(query)) + list(names)
    filtered = deterministic_name_filter(seeded)
    if not filtered:
        return deterministic_name_filter(split_name_blob(query))
    if not _resolve_use_llm(use_llm):
        return filtered

    cache_path = _cache_path(query, filtered)
    cached = _read_cache(cache_path)
    if cached:
        return cached
    try:
        cleaned = _invoke_name_dedup(query, filtered)
    except Exception as exc:
        logger.warning("PubChem LLM name dedup failed; using deterministic names: %s", exc)
        return filtered
    if not cleaned:
        cleaned = filtered
    _write_cache(cache_path, cleaned)
    return cleaned


def slim_compound_record(
    record: dict[str, Any],
    *,
    query: str = "",
    use_llm: bool | None = None,
) -> dict[str, Any]:
    """Keep identity names only; drop PubChem physchem and raw synonym dumps."""
    if not isinstance(record, dict):
        return {"error": "invalid PubChem record"}
    if record.get("error"):
        return {"error": str(record.get("error"))}
    raw_names = [
        record.get("iupac_name"),
        *(record.get("synonyms") or []),
        *(record.get("names") or []),
    ]
    names = dedup_pubchem_names(raw_names, query=query, use_llm=use_llm)
    slim: dict[str, Any] = {"names": names}
    cid = record.get("cid")
    if cid not in (None, ""):
        slim["cid"] = cid
    formula = record.get("molecular_formula") or record.get("formula")
    if formula:
        slim["formula"] = formula
    smiles = record.get("canonical_smiles") or record.get("isomeric_smiles")
    if smiles:
        slim["canonical_smiles"] = smiles
    if record.get("source"):
        slim["source"] = record.get("source")
    return slim


def slim_pubchem_payload(payload: Any, *, query: str = "", use_llm: bool | None = None) -> Any:
    if isinstance(payload, list):
        return [
            slim_compound_record(item, query=query, use_llm=use_llm)
            if isinstance(item, dict)
            else item
            for item in payload
        ]
    if isinstance(payload, dict):
        return slim_compound_record(payload, query=query, use_llm=use_llm)
    return payload


def empty_lookup_result(query: str = "") -> list[dict[str, Any]]:
    """Explicit miss so a 404/empty hit is never serialized as success + blank content."""
    label = str(query or "").strip() or "the given query"
    return [
        {
            "ok": False,
            "matched": False,
            "error": f"No PubChem compound found for {label!r}",
            "query": label,
            "instruction": (
                "Lookup did not match any compound. Leave the lookup unresolved "
                "rather than inventing values."
            ),
        }
    ]


def _is_empty_lookup(payload: Any) -> bool:
    return payload in (None, [], {})


def as_record_list(payload: Any, *, query: str = "") -> list[dict[str, Any]]:
    """Coerce any PubChem payload to the list schema FastMCP validates."""
    if isinstance(payload, dict):
        if not payload:
            return empty_lookup_result(query)
        return [payload]
    if isinstance(payload, list):
        records = [item for item in payload if isinstance(item, dict)]
        if records:
            return records
        return empty_lookup_result(query)
    if _is_empty_lookup(payload):
        return empty_lookup_result(query)
    return [{"error": f"unexpected PubChem payload type: {type(payload).__name__}"}]


def as_record_dict(payload: Any, *, query: str = "") -> dict[str, Any]:
    """Coerce any PubChem payload to the single-record CID-tool schema."""
    if isinstance(payload, dict):
        return payload if payload else empty_lookup_result(query)[0]
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return empty_lookup_result(query)[0]
    if _is_empty_lookup(payload):
        return empty_lookup_result(query)[0]
    return {"error": f"unexpected PubChem payload type: {type(payload).__name__}"}


def finalize_pubchem_payload(payload: Any, *, query: str = "", use_llm: bool | None = None) -> Any:
    if _is_empty_lookup(payload):
        return empty_lookup_result(query)
    slimmed = slim_pubchem_payload(payload, query=query, use_llm=use_llm)
    if _is_empty_lookup(slimmed):
        return empty_lookup_result(query)
    return slimmed


def finalize_pubchem_list(
    payload: Any, *, query: str = "", use_llm: bool | None = None
) -> list[dict[str, Any]]:
    return as_record_list(
        finalize_pubchem_payload(payload, query=query, use_llm=use_llm),
        query=query,
    )


def finalize_pubchem_record(
    payload: Any, *, query: str = "", use_llm: bool | None = None
) -> dict[str, Any]:
    return as_record_dict(
        finalize_pubchem_payload(payload, query=query, use_llm=use_llm),
        query=query,
    )
