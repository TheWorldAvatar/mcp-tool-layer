"""Success/error JSON envelopes for generated MCP tools.

Callers cannot overwrite reserved keys (`status`, `code`, `message`).
See rdf/README.md.
"""

from __future__ import annotations

import json
from typing import Any


_SUCCESS_RESERVED_ENVELOPE_KEYS = frozenset({"status", "code", "message", "iri"})


_ERROR_RESERVED_ENVELOPE_KEYS = frozenset({"status", "code", "message"})


def _envelope_metadata(
    metadata: dict[str, Any],
    *,
    reserved: frozenset[str],
) -> dict[str, Any]:
    """Drop reserved envelope keys so callers cannot overwrite the contract."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in reserved
    }


def success_result(
    *,
    iri: str = "",
    message: str = "",
    **metadata: Any,
) -> dict[str, Any]:
    """Return the shared JSON-safe success envelope for generated tools.

    ``status`` is always ``\"ok\"``. Callers must not pass ``status=``; it is ignored.
    """
    return {
        "status": "ok",
        "iri": str(iri),
        "message": str(message),
        **_envelope_metadata(metadata, reserved=_SUCCESS_RESERVED_ENVELOPE_KEYS),
    }


def error_result(
    *,
    code: str,
    message: str,
    **metadata: Any,
) -> dict[str, Any]:
    """Return the shared JSON-safe error envelope for generated tools.

    ``status`` is always ``\"rejected\"``. Put the machine-readable reason in
    ``code`` (for example ``PROPOSED_ENTITY_EVIDENCE_REQUIRED``). Callers must
    not pass ``status=`` / ``code=`` / ``message=`` via metadata; they are ignored.
    """
    return {
        "status": "rejected",
        "code": str(code),
        "message": str(message),
        **_envelope_metadata(metadata, reserved=_ERROR_RESERVED_ENVELOPE_KEYS),
    }


def result_json(result: dict[str, Any]) -> str:
    """Serialize one standard result envelope for public MCP tool transport."""
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def success_json(
    *,
    iri: str = "",
    message: str = "",
    **metadata: Any,
) -> str:
    """Return a serialized standard success envelope for public MCP tools.

    Envelope ``status`` is always ``\"ok\"``; a caller-supplied ``status=`` is ignored.
    """
    return result_json(success_result(iri=iri, message=message, **metadata))


def error_json(
    *,
    code: str,
    message: str,
    **metadata: Any,
) -> str:
    """Return a serialized standard rejection envelope for public MCP tools.

    Envelope ``status`` is always ``\"rejected\"``. Use ``code=`` for the rejection
    reason (for example ``PROPOSED_ENTITY_EVIDENCE_REQUIRED``). A caller-supplied
    ``status=`` that repeats the code string must not overwrite ``rejected``.
    """
    return result_json(error_result(code=code, message=message, **metadata))
