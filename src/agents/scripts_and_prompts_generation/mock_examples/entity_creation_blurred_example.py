"""
BLURRED EXAMPLE — do NOT copy names verbatim.

Purpose: show the *structure* for a generated entity `create_*` function that is:
- guarded via `@_guard_noncheck` (decorator, not a call)
- uses `locked_graph()` for TTL-backed persistence
- deduplicates by (rdf:type, rdfs:label)
- mints IRIs via `_mint_hash_iri(class_local)` with EXACTLY 1 argument
- returns a JSON STRING (not a dict)
"""

# Imports here are illustrative; the generator should emit the exact imports required by the target script.
import json
from typing import Optional
from rdflib import RDF, RDFS, URIRef, Literal as RDFLiteral


# Pretend these come from base/universal utils:
def locked_graph(): ...
def _sanitize_label(s: str) -> str: ...
def _find_by_type_and_label(g, rdf_type: URIRef, label: str) -> Optional[URIRef]: ...
def _set_single_label(g, iri: URIRef, label: str) -> None: ...
def _mint_hash_iri(class_local: str) -> URIRef: ...
def _guard_noncheck(func): ...


ONTOX = URIRef("https://example.org/onto/")  # placeholder; real code uses Namespace(...)


@_guard_noncheck
def create_BlurredEntity(
    label: str,
    optional_text: Optional[str] = None,
) -> str:
    try:
        with locked_graph() as g:
            lbl = _sanitize_label(label)
            if not lbl:
                return json.dumps({"status": "error", "created": False, "iri": None, "code": "VALIDATION_FAILED", "message": "label is required"})

            existing = _find_by_type_and_label(g, ONTOX, lbl)
            if existing is not None:
                return json.dumps({"status": "ok", "created": False, "iri": str(existing), "code": None, "message": "Already exists"})

            iri = _mint_hash_iri("BlurredEntity")
            g.add((iri, RDF.type, ONTOX))
            _set_single_label(g, iri, lbl)

            if optional_text is not None:
                g.add((iri, URIRef("https://example.org/onto/hasOptionalText"), RDFLiteral(str(optional_text))))

            return json.dumps({"status": "ok", "created": True, "iri": str(iri), "code": None, "message": "Created"})
    except Exception as e:
        return json.dumps({"status": "error", "created": False, "iri": None, "code": "INTERNAL_ERROR", "message": str(e)})

