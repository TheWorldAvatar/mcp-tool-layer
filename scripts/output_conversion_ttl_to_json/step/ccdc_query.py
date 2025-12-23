from pathlib import Path
from typing import Dict, List

from rdflib import Graph


def load_graph_from_ttl(ttl_path: str) -> Graph:
    g = Graph()
    g.parse(ttl_path, format="turtle")
    return g


def query_ccdc_numbers(graph: Graph) -> Dict[str, List[str]]:
    """Return mapping synthesis URI -> list of CCDC number values via:
    ChemicalSynthesis -> hasChemicalOutput -> hasCCDCNumber -> hasCCDCNumberValue
    """
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>

    SELECT DISTINCT ?synthesis ?ccdcVal
    WHERE {
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis ontosyn:hasChemicalOutput ?output .
      ?output ontospecies:hasCCDCNumber ?ccdc .
      ?ccdc ontospecies:hasCCDCNumberValue ?ccdcVal .
    }
    """
    mapping: Dict[str, List[str]] = {}
    for row in graph.query(query):
        syn = str(row.synthesis)
        val = str(row.ccdcVal)
        mapping.setdefault(syn, []).append(val)
    return mapping


if __name__ == "__main__":
    # Example usage against a provided TTL (hardcoded path)
    repo_root = Path(__file__).resolve().parents[3]
    ttl = repo_root / "evaluation" / "data" / "merged_tll" / "1b9180ec" / "1b9180ec.ttl"
    print(f"Loading graph: {ttl}")
    graph = load_graph_from_ttl(str(ttl))
    m = query_ccdc_numbers(graph)
    print(f"Found CCDC numbers for {len(m)} syntheses")
    for syn, vals in list(m.items()):
        print(f"Synthesis: {syn}")
        print("  CCDC:", "; ".join(vals))

