from pathlib import Path
from typing import Dict, List

from rdflib import Graph, URIRef


def load_graph_from_ttl(ttl_path: str) -> Graph:
    g = Graph()
    g.parse(ttl_path, format="turtle")
    return g


def query_all_syntheses(graph: Graph) -> List[str]:
    """Return URIs of all ontosyn:ChemicalSynthesis instances."""
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    SELECT DISTINCT ?synthesis WHERE { ?synthesis a ontosyn:ChemicalSynthesis }
    """
    return [str(row.synthesis) for row in graph.query(query)]


def query_synthesis_inputs(graph: Graph, synthesis_uri: str) -> List[Dict[str, any]]:
    """Find chemicals for a synthesis via ontosyn:hasChemicalInput (label, amount, alternative names, and IRI)."""
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?chemical ?label ?amount ?altName
    WHERE {
        ?synthesis ontosyn:hasChemicalInput ?chemical .
        OPTIONAL { ?chemical rdfs:label ?label }
        OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
        OPTIONAL { ?chemical ontosyn:hasAlternativeNames ?altName }
    }
    """
    results = graph.query(query, initBindings={"synthesis": URIRef(synthesis_uri)})
    
    # Group by chemical URI to collect all alternative names
    grouped: Dict[str, Dict[str, any]] = {}
    for row in results:
        chem_uri = str(row.chemical) if row.chemical else ""
        if chem_uri not in grouped:
            grouped[chem_uri] = {
                "uri": chem_uri,
                "label": str(row.label) if row.label else "N/A",
                "amount": str(row.amount) if row.amount else "N/A",
                "alternative_names": []
            }
        # Collect alternative names
        if row.altName:
            alt = str(row.altName)
            if alt and alt not in grouped[chem_uri]["alternative_names"]:
                grouped[chem_uri]["alternative_names"].append(alt)
    
    return list(grouped.values())


def query_inputs_for_all_syntheses(graph: Graph) -> Dict[str, List[Dict[str, str]]]:
    """Map synthesis URI -> list of input chemicals (uri, label, amount)."""
    mapping: Dict[str, List[Dict[str, str]]] = {}
    for syn in query_all_syntheses(graph):
        mapping[syn] = query_synthesis_inputs(graph, syn)
    return mapping


if __name__ == "__main__":
    # Example usage against a provided TTL (hardcoded path)
    repo_root = Path(__file__).resolve().parents[3]
    ttl = repo_root / "evaluation" / "data" / "merged_tll" / "1b9180ec" / "1b9180ec.ttl"
    print(f"Loading graph: {ttl}")
    graph = load_graph_from_ttl(str(ttl))
    syn_list = query_all_syntheses(graph)
    print(f"Found {len(syn_list)} syntheses")
    for syn in syn_list[:5]:
        inputs = query_synthesis_inputs(graph, syn)
        print(f"Synthesis: {syn}")
        for item in inputs:
            print(f"  - {item['label']} | {item['amount']} | {item['uri']}")

