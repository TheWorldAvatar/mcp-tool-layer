from pathlib import Path
from typing import Any, Dict, List

from rdflib import Graph, URIRef


def load_graph_from_ttl(ttl_path: str) -> Graph:
    g = Graph()
    g.parse(ttl_path, format="turtle")
    return g


def _local_name(uri: str) -> str:
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rstrip("/").rsplit("/", 1)[-1]


def _camelize(name: str) -> str:
    if not name:
        return name
    out = name[0].lower() + name[1:]
    return out


def query_step_type_label(graph: Graph, step_uri: str) -> str:
    """Return simple type label for a step (e.g., Add, HeatChill, Sonicate)."""
    q = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    SELECT DISTINCT ?t WHERE {
      ?step a ?t .
      FILTER(?step = ?S)
      FILTER(STRSTARTS(STR(?t), "https://www.theworldavatar.com/kg/OntoSyn/"))
    }
    """
    results = list(graph.query(q, initBindings={"S": URIRef(step_uri)}))
    # Prefer a type that is not ontosyn:SynthesisStep
    preferred: List[str] = []
    fallback: List[str] = []
    for row in results:
        t_uri = str(row.t)
        if t_uri.rstrip("/").endswith("/SynthesisStep"):
            fallback.append(t_uri)
        else:
            preferred.append(t_uri)
    if preferred:
        return _local_name(preferred[0])
    if fallback:
        # When the only type is the generic SynthesisStep, return a safer placeholder 'Step'
        return "Step"
    return "Step"


def query_step_details_all_fields(graph: Graph, step_uri: str) -> Dict[str, Any]:
    """Collect all direct properties of a step, preferring labels for object IRIs.

    Output shape: { <StepTypeLabel>: { <fieldName>: value or [values] } }
    """
    step_type = query_step_type_label(graph, step_uri)

    # Query all outgoing properties except rdf:type
    q_props = """
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    SELECT DISTINCT ?p ?o ?olabel WHERE {
      ?s ?p ?o .
      FILTER(?s = ?S)
      FILTER(?p != rdf:type)
      OPTIONAL { ?o rdfs:label ?olabel }
    }
    """
    results = graph.query(q_props, initBindings={"S": URIRef(step_uri)})

    fields: Dict[str, Any] = {}
    for row in results:
        p_uri = str(row.p)
        o = row.o
        o_label = str(row.olabel) if row.olabel else ""
        field_name = _camelize(_local_name(p_uri))

        # Normalize the object
        if isinstance(o, URIRef):
            # Prefer human label when available; drop IRIs from final step field values
            value = o_label or ""
        else:
            value = str(o)

        if field_name in fields:
            # Merge: list-append when already present
            if isinstance(fields[field_name], list):
                fields[field_name].append(value)
            else:
                fields[field_name] = [fields[field_name], value]
        else:
            fields[field_name] = value

    # Do not include raw URI in the output; keep only human-friendly fields

    # Attempt to surface the main rdfs:label for the step itself
    q_label = """
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?label WHERE { ?s rdfs:label ?label . FILTER(?s = ?S) }
    """
    labels = [str(r.label) for r in graph.query(q_label, initBindings={"S": URIRef(step_uri)})]
    if labels:
        # If multiple labels, keep them all
        fields["label"] = labels if len(labels) > 1 else labels[0]

    # Deduplicate object lists by label only
    for key, value in list(fields.items()):
        if isinstance(value, list):
            seen: List[str] = []
            merged_list: List[str] = []
            for it in value:
                lbl = it if isinstance(it, str) else ""
                if lbl and lbl not in seen:
                    seen.append(lbl)
                    merged_list.append(lbl)
            if merged_list:
                fields[key] = merged_list

    return {step_type: fields}


if __name__ == "__main__":
    # Example usage against a provided TTL (hardcoded path)
    repo_root = Path(__file__).resolve().parents[3]
    ttl = repo_root / "evaluation" / "data" / "merged_tll" / "1b9180ec" / "1b9180ec.ttl"
    print(f"Loading graph: {ttl}")
    g = load_graph_from_ttl(str(ttl))
    # Example: probe few step URIs by scanning for ontosyn:SynthesisStep
    q_any = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    SELECT DISTINCT ?s WHERE { ?s a ontosyn:SynthesisStep }
    LIMIT 5
    """
    for row in g.query(q_any):
        step_u = str(row.s)
        print("\nStep:", step_u)
        d = query_step_details_all_fields(g, step_u)
        print(d)

