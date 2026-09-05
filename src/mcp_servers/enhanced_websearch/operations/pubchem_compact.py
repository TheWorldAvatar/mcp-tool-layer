"""Compact PubChem compound summary via PUG REST (no HTML / Docling)."""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

import requests

from src.mcp_servers.enhanced_websearch.operations.timeout import (
    call_with_retry,
    env_float,
    env_int,
)

_PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_TIMEOUT = env_float("WEBSEARCH_HTTP_TIMEOUT_SEC", 12.0)
_ATTEMPTS = env_int("WEBSEARCH_HTTP_ATTEMPTS", 3)
_MAX_SYNONYMS = 12
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "mcp-enhanced-websearch/pubchem-compact",
    }
)


def _get_json(url: str) -> Optional[dict[str, Any]]:
    def _do() -> requests.Response:
        response = _SESSION.get(url, timeout=_TIMEOUT)
        if response.status_code >= 500:
            raise RuntimeError(f"PubChem REST HTTP {response.status_code}")
        return response

    try:
        response = call_with_retry(
            f"pubchem_rest({url})",
            _do,
            attempts=_ATTEMPTS,
        )
    except (requests.RequestException, RuntimeError, TimeoutError):
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def compound_token_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "").strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "compound":
        return ""
    return parts[1].strip()


def _lookup_cid(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    lowered = token.lower()
    if lowered.startswith("cid") and lowered[3:].isdigit():
        return int(lowered[3:])
    payload = _get_json(f"{_PUG}/compound/name/{quote(token, safe='')}/cids/JSON")
    cids = ((payload or {}).get("IdentifierList") or {}).get("CID") or []
    if not cids:
        return None
    try:
        return int(cids[0])
    except (TypeError, ValueError):
        return None


def _properties(cid: int) -> dict[str, Any]:
    props = (
        "Title,MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,"
        "InChIKey,IUPACName,Charge"
    )
    payload = _get_json(f"{_PUG}/compound/cid/{cid}/property/{props}/JSON")
    rows = ((payload or {}).get("PropertyTable") or {}).get("Properties") or []
    return rows[0] if rows else {}


def _cas_list(cid: int) -> list[str]:
    payload = _get_json(f"{_PUG}/compound/cid/{cid}/xrefs/RN/JSON")
    refs = ((payload or {}).get("InformationList") or {}).get("Information") or []
    numbers: list[str] = []
    seen: set[str] = set()
    for row in refs:
        for item in row.get("RN") or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                numbers.append(text)
    return numbers


def _synonyms(cid: int) -> list[str]:
    payload = _get_json(f"{_PUG}/compound/cid/{cid}/synonyms/JSON")
    info = ((payload or {}).get("InformationList") or {}).get("Information") or []
    names: list[str] = []
    seen: set[str] = set()
    for row in info:
        for item in row.get("Synonym") or []:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            names.append(text)
            if len(names) >= _MAX_SYNONYMS:
                return names
    return names


def _description(cid: int) -> str:
    payload = _get_json(f"{_PUG}/compound/cid/{cid}/description/JSON")
    info = ((payload or {}).get("InformationList") or {}).get("Information") or []
    for row in info:
        text = str(row.get("Description") or "").strip()
        if text:
            return text
    return ""


def compact_pubchem_markdown(url: str) -> str:
    """Return a short markdown card for a PubChem compound URL."""
    token = compound_token_from_url(url)
    if not token:
        return (
            "Error: this PubChem URL is not a /compound page. "
            "Pass a compound CID or name URL, or use the pubchem tools."
        )
    cid = _lookup_cid(token)
    if cid is None:
        return f"Error: PubChem compound not found for '{token}'."

    props = _properties(cid)
    cas = _cas_list(cid)
    synonyms = _synonyms(cid)
    description = _description(cid)
    source = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"

    lines = [
        f"# PubChem CID {cid}",
        "",
        f"Source: {source}",
        "",
        f"- Title: {props.get('Title') or ''}",
        f"- IUPAC: {props.get('IUPACName') or ''}",
        f"- Formula: {props.get('MolecularFormula') or ''}",
        f"- Weight: {props.get('MolecularWeight') or ''}",
        f"- Canonical SMILES: {props.get('ConnectivitySMILES') or props.get('CanonicalSMILES') or ''}",
        f"- SMILES: {props.get('SMILES') or props.get('IsomericSMILES') or ''}",
        f"- InChIKey: {props.get('InChIKey') or ''}",
        f"- CAS: {', '.join(cas) if cas else ''}",
        f"- Synonyms: {'; '.join(synonyms) if synonyms else ''}",
    ]
    if description:
        lines.extend(["", "## Description", description])
    lines.extend(
        [
            "",
            "Note: compact PUG REST summary, not the full PubChem HTML page.",
        ]
    )
    if not props and not cas and not synonyms:
        return json.dumps(
            {"error": f"PubChem REST returned no data for CID {cid}", "url": source},
            ensure_ascii=False,
        )
    return "\n".join(lines)
