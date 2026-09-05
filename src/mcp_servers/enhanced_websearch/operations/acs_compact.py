"""Compact ACS article card via Crossref (no paywalled HTML / Docling)."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import requests

from src.mcp_servers.enhanced_websearch.operations.timeout import (
    call_with_retry,
    env_float,
    env_int,
)

_CROSSREF = "https://api.crossref.org/works"
_TIMEOUT = env_float("WEBSEARCH_HTTP_TIMEOUT_SEC", 12.0)
_ATTEMPTS = env_int("WEBSEARCH_HTTP_ATTEMPTS", 3)
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "mcp-enhanced-websearch/acs-compact (mailto:research@localhost)",
    }
)

_DOI_PREFIX = re.compile(r"^10\.\d{4,9}/", re.IGNORECASE)


def doi_from_acs_url(url: str) -> str:
    path = unquote(urlparse(url).path or "").strip("/")
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ""
    if parts[0].lower() != "doi":
        return ""
    rest = parts[1:]
    if rest and rest[0].lower() in {"abs", "full", "pdf", "pdfplus"}:
        rest = rest[1:]
    doi = "/".join(rest).strip()
    return doi if doi and _DOI_PREFIX.match(doi) else ""


def _work(doi: str) -> Optional[dict[str, Any]]:
    def _do() -> requests.Response:
        response = _SESSION.get(f"{_CROSSREF}/{doi}", timeout=_TIMEOUT)
        if response.status_code >= 500:
            raise RuntimeError(f"Crossref HTTP {response.status_code}")
        return response

    try:
        response = call_with_retry(
            f"crossref({doi})",
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
    message = payload.get("message") if isinstance(payload, dict) else None
    return message if isinstance(message, dict) else None


def _strip_tags(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _first_title(work: dict[str, Any]) -> str:
    titles = work.get("title") or []
    return _strip_tags(str(titles[0])) if titles else ""


def _abstract(work: dict[str, Any]) -> str:
    raw = str(work.get("abstract") or "").strip()
    if not raw:
        return ""
    return _strip_tags(raw)


def compact_acs_markdown(url: str) -> str:
    """Return title/abstract for an ACS DOI URL via Crossref."""
    doi = doi_from_acs_url(url)
    if not doi:
        return (
            "Error: could not parse a DOI from this ACS URL. "
            "Use google_search snippets instead of the paywalled HTML."
        )
    work = _work(doi)
    if not work:
        return (
            f"Error: Crossref has no record for DOI {doi}. "
            "ACS HTML is paywalled; use google_search snippets."
        )
    year = ""
    issued = ((work.get("issued") or {}).get("date-parts") or [[]])[0]
    if issued:
        year = str(issued[0])
    container = (work.get("container-title") or [""])[0]
    authors = []
    for person in (work.get("author") or [])[:8]:
        family = str(person.get("family") or "").strip()
        given = str(person.get("given") or "").strip()
        label = " ".join(part for part in (given, family) if part)
        if label:
            authors.append(label)
    abstract = _abstract(work)
    lines = [
        f"# {_first_title(work) or doi}",
        "",
        f"DOI: {doi}",
        f"Journal: {container}",
        f"Year: {year}",
        f"Authors: {', '.join(authors)}",
        f"Source: https://doi.org/{doi}",
    ]
    if abstract:
        lines.extend(["", "## Abstract", abstract])
    else:
        lines.extend(
            [
                "",
                "Note: no public abstract on Crossref. ACS HTML is paywalled "
                "and is not fetched.",
            ]
        )
    lines.extend(
        [
            "",
            "Note: compact Crossref record, not the ACS full text.",
        ]
    )
    return "\n".join(lines)
