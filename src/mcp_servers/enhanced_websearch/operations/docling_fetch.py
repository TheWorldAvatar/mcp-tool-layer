"""
URL to markdown conversion.

Docling is used for ordinary vendor/article pages, under a hard timeout and retry.
PubChem compound URLs use a compact PUG REST card. ACS DOI URLs use Crossref.
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.mcp_servers.enhanced_websearch.operations.timeout import (
    call_with_retry,
    env_float,
    env_int,
)

_DEFAULT_TIMEOUT_SEC = 25.0
_DEFAULT_ATTEMPTS = 3
_BLOCKED_HOST_SUFFIXES = (
    "www.ncbi.nlm.nih.gov",
)
_ACS_HOST_SUFFIXES = (
    "pubs.acs.org",
)
_PUBCHEM_HOST_SUFFIXES = (
    "pubchem.ncbi.nlm.nih.gov",
)


def _timeout_seconds() -> float:
    return env_float("URL_TO_MARKDOWN_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC)


def _attempts() -> int:
    return env_int("URL_TO_MARKDOWN_ATTEMPTS", _DEFAULT_ATTEMPTS)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _matches_host(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _is_blocked_host(host: str) -> bool:
    return _matches_host(host, _BLOCKED_HOST_SUFFIXES)


def _is_pubchem_host(host: str) -> bool:
    return _matches_host(host, _PUBCHEM_HOST_SUFFIXES)


def _is_acs_host(host: str) -> bool:
    return _matches_host(host, _ACS_HOST_SUFFIXES)


def url_to_markdown(url: str) -> str:
    """
    Fetch a URL and convert it to markdown.

    PubChem compound URLs return a compact REST card. ACS DOI URLs return
    a Crossref title/abstract card. Other URLs go through Docling under a
    hard timeout.
    """
    host = _host(url)
    if _is_pubchem_host(host):
        from src.mcp_servers.enhanced_websearch.operations.pubchem_compact import (
            compact_pubchem_markdown,
        )

        try:
            return compact_pubchem_markdown(url)
        except Exception as exc:
            return f"Error fetching the URL: compact PubChem summary failed: {exc}"
    if _is_acs_host(host):
        from src.mcp_servers.enhanced_websearch.operations.acs_compact import (
            compact_acs_markdown,
        )

        try:
            return compact_acs_markdown(url)
        except Exception as exc:
            return f"Error fetching the URL: compact ACS/Crossref summary failed: {exc}"
    if _is_blocked_host(host):
        return (
            f"Error: url_to_markdown will not fetch {host} HTML. "
            "Use google_search snippets or a more specific compound/DOI URL."
        )

    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return "Error: Docling library not installed. Please install it with: pip install docling"

    timeout_seconds = _timeout_seconds()
    attempts = _attempts()

    def _convert() -> str:
        converter = DocumentConverter()
        doc = converter.convert(url).document
        return doc.export_to_markdown()

    try:
        return call_with_retry(
            f"url_to_markdown({url!r})",
            _convert,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
        )
    except TimeoutError as e:
        return f"Error fetching the URL: {e}"
    except Exception as e:
        return f"Error fetching the URL: {e}"


if __name__ == "__main__":
    print(url_to_markdown("https://www.cd-bioparticles.net/p/9912/3355-azobenzenetetracarboxylic-acid"))
