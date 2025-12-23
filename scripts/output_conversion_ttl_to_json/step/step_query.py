from pathlib import Path
from typing import Dict, List

from rdflib import Graph, URIRef

from scripts.output_conversion_ttl_to_json.step.chemicalinput_query import (
    query_all_syntheses,
)


def load_graph_from_ttl(ttl_path: str) -> Graph:
    g = Graph()
    g.parse(ttl_path, format="turtle")
    return g


def query_synthesis_steps(graph: Graph, synthesis_uri: str) -> List[Dict[str, str]]:
    """
    Return one row per step for a synthesis via ontosyn:hasSynthesisStep.
    - Deduplicate multiple labels via MIN over lowercased string for stability.
    - Deduplicate multiple orders via MIN and cast to xsd:integer for correct sort.
    """
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX xsd:     <http://www.w3.org/2001/XMLSchema#>

    SELECT ?step (MIN(?labelCanon) AS ?label) (MIN(?orderInt) AS ?order)
    WHERE {
      ?synthesis a ontosyn:ChemicalSynthesis ;
                 ontosyn:hasSynthesisStep ?step .

      OPTIONAL {
        ?step rdfs:label ?lbl .
        BIND(LCASE(STR(?lbl)) AS ?labelCanon)
      }

      OPTIONAL {
        ?step ontosyn:hasOrder ?ordRaw .
        BIND(xsd:integer(?ordRaw) AS ?orderInt)
      }
    }
    GROUP BY ?step
    ORDER BY ?order ?step
    """
    results = graph.query(query, initBindings={"synthesis": URIRef(synthesis_uri)})
    out: List[Dict[str, str]] = []
    for row in results:
        out.append(
            {
                "uri": str(row.step),
                "label": str(row.label) if row.label else "",
                "order": str(row.order) if row.order else "",
            }
        )
    return out


if __name__ == "__main__":
    # Example usage against a provided TTL (hardcoded path)
    repo_root = Path(__file__).resolve().parents[3]
    ttl = repo_root / "evaluation" / "data" / "merged_tll" / "1b9180ec" / "1b9180ec.ttl"
    print(f"Loading graph: {ttl}")
    graph = load_graph_from_ttl(str(ttl))
    syntheses = query_all_syntheses(graph)
    print(f"Found {len(syntheses)} syntheses")
    for syn in syntheses:
        steps = query_synthesis_steps(graph, syn)
        print(f"Synthesis: {syn}")
        for st in steps:
            print(f"  - {st['order'] or 'N/A'} | {st['label']} | {st['uri']}")

