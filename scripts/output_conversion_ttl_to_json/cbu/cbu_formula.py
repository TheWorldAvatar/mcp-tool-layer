from pathlib import Path
from typing import Any, Dict, List

from rdflib import Graph


def load_graph_from_ttl(ttl_path: str) -> Graph:
    g = Graph()
    g.parse(ttl_path, format="turtle")
    return g


def query_ccdc_to_cbus(graph: Graph) -> Dict[str, Dict[str, List[str]]]:
    """Return mapping: CCDC number -> { CBU formula(label): [names...] } using SPARQL.

    Path:
      MetalOrganicPolyhedron -> ontomops:hasCCDCNumber
      MetalOrganicPolyhedron -> ontomops:hasChemicalBuildingUnit -> CBU
      CBU rdfs:label (used as formula)
      CBU ontosyn:hasAlternativeNames (names)
      CBU owl:sameAs -> rdfs:label / ontosyn:hasAlternativeNames (more names)
    """
    query = """
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl:  <http://www.w3.org/2002/07/owl#>

    SELECT DISTINCT ?ccdc ?cbu ?cbuLabel ?cbuAlt ?sameLabel ?sameAlt
    WHERE {
      ?m a ontomops:MetalOrganicPolyhedron .
      OPTIONAL { ?m ontomops:hasCCDCNumber ?ccdc }
      ?m ontomops:hasChemicalBuildingUnit ?cbu .
      OPTIONAL { ?cbu rdfs:label ?cbuLabel }
      OPTIONAL { ?cbu ontosyn:hasAlternativeNames ?cbuAlt }
      OPTIONAL {
        ?cbu owl:sameAs ?same .
        OPTIONAL { ?same rdfs:label ?sameLabel }
        OPTIONAL { ?same ontosyn:hasAlternativeNames ?sameAlt }
      }
    }
    """
    out: Dict[str, Dict[str, List[str]]] = {}
    for row in graph.query(query):
        ccdc = str(row.ccdc) if row.ccdc else "N/A"
        if ccdc == "N/A":
            # Skip missing CCDC entries
            continue
        cbu_label = str(row.cbuLabel) if row.cbuLabel else "N/A"
        if cbu_label == "N/A":
            # Must have a formula-like label to index
            continue
        out.setdefault(ccdc, {})
        out[ccdc].setdefault(cbu_label, [])

        def add_name(val: Any) -> None:
            if val:
                s = str(val).strip()
                if s and s not in out[ccdc][cbu_label]:
                    out[ccdc][cbu_label].append(s)

        # Prefer alternative names; if none, include its own label as a name
        added_any = False
        if row.cbuAlt:
            add_name(row.cbuAlt)
            added_any = True
        if row.sameLabel:
            add_name(row.sameLabel)
            added_any = True
        if row.sameAlt:
            add_name(row.sameAlt)
            added_any = True
        if not added_any:
            add_name(row.cbuLabel)
    return out


def build_cbu_json_from_graph(graph: Graph) -> Dict[str, Any]:
    """Build CBU JSON using SPARQL helpers in this module."""
    ccdc_map = query_ccdc_to_cbus(graph)
    procedures: List[Dict[str, Any]] = []
    for ccdc, cbu_map in sorted(ccdc_map.items()):
        formulas = sorted(cbu_map.keys())
        cbu1 = formulas[0] if len(formulas) >= 1 else "N/A"
        cbu2 = formulas[1] if len(formulas) >= 2 else "N/A"
        names1 = cbu_map.get(cbu1, [])
        names2 = cbu_map.get(cbu2, [])
        procedures.append({
            "mopCCDCNumber": ccdc,
            "cbuFormula1": cbu1,
            "cbuSpeciesNames1": names1,
            "cbuFormula2": cbu2,
            "cbuSpeciesNames2": names2,
        })
    return {"synthesisProcedures": procedures}


if __name__ == "__main__":
    # Example usage against a provided TTL (hardcoded path)
    repo_root = Path(__file__).resolve().parents[3]
    ttl = repo_root / "evaluation" / "data" / "merged_tll" / "1b9180ec" / "1b9180ec.ttl"
    print(f"Loading graph: {ttl}")
    g = load_graph_from_ttl(str(ttl))
    result = build_cbu_json_from_graph(g)
    print(result)

