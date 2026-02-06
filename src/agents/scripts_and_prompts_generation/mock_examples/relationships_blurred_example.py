"""
BLURRED EXAMPLE — do NOT copy names verbatim.

Purpose: show the *structure* for an add_* relationship function that:
- uses `locked_graph()` (TTL-backed persistence)
- validates existence of subject/object IRIs (best-effort)
- returns a JSON STRING (not a dict)
- optionally enforces ordered-membership contiguity at mutation time
"""

import json
from rdflib import URIRef, RDF, RDFS


def locked_graph(): ...


ONTOX = URIRef("https://example.org/onto/")  # placeholder; real code uses Namespace(...)


def _resource_exists(g, r: URIRef) -> bool:
    return (r, None, None) in g or (None, None, r) in g


def _parse_pos_int(v) -> int | None:
    try:
        n = int(str(v).strip())
        return n if n > 0 else None
    except Exception:
        return None


def _enforce_contiguous_orders(g, container: URIRef, membership_pred: URIRef, order_pred: URIRef) -> str | None:
    # Collect (member -> order)
    orders: dict[URIRef, int] = {}
    for member in g.objects(container, membership_pred):
        if not isinstance(member, URIRef):
            continue
        vals = list(g.objects(member, order_pred))
        if len(vals) != 1:
            return f"Member {member} must have exactly one order value."
        n = _parse_pos_int(vals[0])
        if n is None:
            return f"Member {member} has invalid order value: {vals[0]}"
        orders[member] = n

    seq = sorted(set(orders.values()))
    if not seq:
        return None
    exp = list(range(1, max(seq) + 1))
    if seq != exp:
        return f"Non-contiguous orders: got {seq}, expected {exp}"
    if len(seq) != len(orders):
        return "Duplicate order values detected."
    return None


def add_member_to_container(container_iri: str, member_iri: str) -> str:
    try:
        container = URIRef(container_iri.strip())
        member = URIRef(member_iri.strip())
        membership_pred = URIRef("https://example.org/onto/hasMember")  # placeholder
        order_pred = URIRef("https://example.org/onto/hasOrder")  # placeholder

        with locked_graph() as g:
            if not _resource_exists(g, container):
                return json.dumps({"status": "error", "created": False, "iri": None, "code": "MISSING_SUBJECT", "message": "Container does not exist"})
            if not _resource_exists(g, member):
                return json.dumps({"status": "error", "created": False, "iri": None, "code": "MISSING_OBJECT", "message": "Member does not exist"})

            g.add((container, membership_pred, member))

            # Optional mutation-time enforcement (if ontology implies ordering):
            err = _enforce_contiguous_orders(g, container, membership_pred, order_pred)
            if err:
                # Remove the just-added triple if enforcement fails
                g.remove((container, membership_pred, member))
                return json.dumps({"status": "error", "created": False, "iri": None, "code": "ORDER_INCONSISTENT", "message": err})

        return json.dumps({"status": "ok", "created": False, "iri": None, "code": None, "message": "Linked"})
    except Exception as e:
        return json.dumps({"status": "error", "created": False, "iri": None, "code": "INTERNAL_ERROR", "message": str(e)})

