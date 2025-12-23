"""
KG Grounding utilities

Provides helpers to ground free-text labels to KG instances by querying
SPARQL endpoints or a local TTL subgraph. Example usage: vessel types
from data/ontologies/vessel_type.ttl.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, List, Optional, Tuple, Dict, Any

try:
    import rdflib  # type: ignore
except Exception:  # pragma: no cover
    rdflib = None  # optional

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # optional

RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
SKOS_PREF = "http://www.w3.org/2004/02/skos/core#prefLabel"
SKOS_ALT = "http://www.w3.org/2004/02/skos/core#altLabel"


def _normalize(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _label_props(label_properties: Optional[Iterable[str]] = None) -> List[str]:
    if label_properties:
        return list(label_properties)
    return [RDFS_LABEL, SKOS_PREF, SKOS_ALT]


def _build_label_union(var: str, props: List[str]) -> str:
    # UNION across label properties
    parts = [f"?s <{p}> ?{var} ." for p in props]
    return " UNION ".join(f"{{ {p} }}" for p in parts)


def query_remote_sparql(
    endpoint_url: str,
    *,
    class_iri: Optional[str] = None,
    label: Optional[str] = None,
    label_properties: Optional[Iterable[str]] = None,
    limit: int = 25,
) -> List[Dict[str, str]]:
    if requests is None:
        raise RuntimeError("requests is required for remote SPARQL queries")

    props = _label_props(label_properties)
    filters: List[str] = []
    where: List[str] = []

    if class_iri:
        where.append(f"?s a <{class_iri}> .")

    # Attach label triples using UNION
    where.append(_build_label_union("label", props))

    if label:
        lab = _normalize(label)
        # Case-insensitive equality first; also allow contains as fallback
        filters.append(
            "FILTER(LCASE(STR(?label)) = '" + lab.replace("'", "\\'") + "')"
        )

    query = (
        "SELECT DISTINCT ?s ?label WHERE {\n"
        + "\n".join(where)
        + ("\n" + "\n".join(filters) if filters else "")
        + f"\n}} LIMIT {max(1, int(limit))}"
    )

    resp = requests.post(
        endpoint_url,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    out: List[Dict[str, str]] = []
    for b in payload.get("results", {}).get("bindings", []) or []:
        s = b.get("s", {}).get("value")
        l = b.get("label", {}).get("value")
        if s:
            out.append({"iri": s, "label": l or ""})
    # If no exact match and label provided, try CONTAINS fallback
    if not out and label:
        lab = _normalize(label)
        query2 = (
            "SELECT DISTINCT ?s ?label WHERE {\n"
            + "\n".join(where)
            + f"\nFILTER(CONTAINS(LCASE(STR(?label)), '{lab.replace("'", "\\'")}'))\n}} LIMIT {max(1, int(limit))}"
        )
        resp2 = requests.post(
            endpoint_url,
            data={"query": query2},
            headers={"Accept": "application/sparql-results+json"},
            timeout=30,
        )
        resp2.raise_for_status()
        payload2 = resp2.json()
        for b in payload2.get("results", {}).get("bindings", []) or []:
            s = b.get("s", {}).get("value")
            l = b.get("label", {}).get("value")
            if s:
                out.append({"iri": s, "label": l or ""})
    return out


def query_local_ttl(
    ttl_path: str,
    *,
    class_iri: Optional[str] = None,
    label: Optional[str] = None,
    label_properties: Optional[Iterable[str]] = None,
    limit: int = 50,
) -> List[Dict[str, str]]:
    props = _label_props(label_properties)
    if rdflib is None:
        # Fallback: naive scan of rdfs:label lines (sufficient for vessel_type.ttl)
        # Expects triples in Turtle: subject rdfs:label "..." .
        data = []
        content = open(ttl_path, "r", encoding="utf-8").read()
        subj = None
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("@"):
                continue
            if line.endswith(" a vs:VesselType ;") or line.endswith(" a <" + (class_iri or "") + "> ;"):
                subj = line.split()[0]
                continue
            if subj and "rdfs:label" in line:
                m = re.search(r'rdfs:label\s+"(.*?)"', line)
                if m:
                    lab = m.group(1)
                    data.append({"iri": subj, "label": lab})
                subj = None
        if label:
            labn = _normalize(label)
            data = [d for d in data if _normalize(d.get("label", "")) == labn]
        return data[: limit or 50]

    g = rdflib.Graph()
    g.parse(ttl_path, format="turtle")

    where = []
    if class_iri:
        where.append(f"?s a <{class_iri}> .")
    where.append(_build_label_union("label", props))

    filters = []
    if label:
        lab = _normalize(label)
        filters.append(
            "FILTER(LCASE(STR(?label)) = '" + lab.replace("'", "\\'") + "')"
        )

    q = (
        "SELECT DISTINCT ?s ?label WHERE {\n"
        + "\n".join(where)
        + ("\n" + "\n".join(filters) if filters else "")
        + f"\n}} LIMIT {max(1, int(limit))}"
    )

    out: List[Dict[str, str]] = []
    for row in g.query(q):  # type: ignore[arg-type]
        try:
            s = str(row[0])
            l = str(row[1])
        except Exception:
            continue
        out.append({"iri": s, "label": l})

    if not out and label:
        lab = _normalize(label)
        q2 = (
            "SELECT DISTINCT ?s ?label WHERE {\n"
            + "\n".join(where)
            + f"\nFILTER(CONTAINS(LCASE(STR(?label)), '{lab.replace("'", "\\'")}'))\n}} LIMIT {max(1, int(limit))}"
        )
        for row in g.query(q2):  # type: ignore[arg-type]
            try:
                s = str(row[0])
                l = str(row[1])
            except Exception:
                continue
            out.append({"iri": s, "label": l})
    return out


def ground_label(
    label: str,
    *,
    class_iri: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    local_ttl_path: Optional[str] = None,
    label_properties: Optional[Iterable[str]] = None,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """Ground a label to KG instances, trying remote endpoint then local TTL.

    Returns a list of { iri, label, score } with simple exact-match scoring first.
    """
    results: List[Dict[str, Any]] = []

    def _score(lbl: str) -> int:
        n = _normalize(lbl)
        m = _normalize(label)
        if n == m:
            return 100
        if n in m or m in n:
            return 50
        return 0

    if endpoint_url:
        try:
            rem = query_remote_sparql(
                endpoint_url,
                class_iri=class_iri,
                label=label,
                label_properties=label_properties,
                limit=limit,
            )
            for r in rem:
                r["score"] = _score(r.get("label", ""))
            results.extend(rem)
        except Exception:
            pass

    if (not results) and local_ttl_path:
        try:
            loc = query_local_ttl(
                local_ttl_path,
                class_iri=class_iri,
                label=label,
                label_properties=label_properties,
                limit=limit,
            )
            for r in loc:
                r["score"] = _score(r.get("label", ""))
            results.extend(loc)
        except Exception:
            pass

    # Sort by score desc, then label asc
    results = sorted(results, key=lambda d: (-int(d.get("score", 0)), d.get("label", "")))
    return results[:limit]


def ground_vessel_type(
    label: str,
    *,
    endpoint_url: Optional[str] = None,
    local_ttl_path: str = "data/ontologies/vessel_type.ttl",
) -> List[Dict[str, Any]]:
    """Ground a vessel type label to vs:VesselType instances.

    If endpoint_url is provided, queries that first; otherwise queries the local TTL.
    """
    vessel_class = "https://www.theworldavatar.com/kg/ontomops/vessel-type/VesselType"
    return ground_label(
        label,
        class_iri=vessel_class,
        endpoint_url=endpoint_url,
        local_ttl_path=local_ttl_path,
        label_properties=[RDFS_LABEL, SKOS_PREF, SKOS_ALT],
        limit=25,
    )


