"""Follow-up Ontop probes after DispersionPolygon discovery."""
from __future__ import annotations

import json

from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

QUERIES = {
    "co2measure_num": """
        SELECT ?m ?v WHERE {
          ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> .
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
          FILTER(CONTAINS(STR(?m), "co2measure"))
        } LIMIT 5""",
    "ontoems_measure_num": """
        SELECT ?m ?v WHERE {
          ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> .
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
          FILTER(CONTAINS(STR(?m), "measure_"))
        } LIMIT 5""",
    "mmsi_any": """
        SELECT ?s ?p ?o WHERE {
          ?s ?p ?o .
          FILTER(CONTAINS(STR(?s), "563071320") || CONTAINS(STR(?o), "563071320"))
        } LIMIT 10""",
    "speed_predicate": """
        SELECT ?s ?p ?o WHERE {
          ?s ?p ?o .
          FILTER(CONTAINS(LCASE(STR(?p)), "speed"))
        } LIMIT 10""",
    "dispersion_polygon_preds": """
        SELECT ?p ?o WHERE {
          <https://www.theworldavatar.com/kg/ontodispersion/6bb552bc-4f42-4fee-9d1c-c48b3133fe06> ?p ?o .
        }""",
    "dispersion_classes": """
        SELECT ?c (COUNT(?x) AS ?n) WHERE {
          ?x a ?c .
          FILTER(CONTAINS(LCASE(STR(?c)), "dispersion") || CONTAINS(LCASE(STR(?c)), "pollutant")
               || CONTAINS(LCASE(STR(?c)), "concentration"))
        } GROUP BY ?c ORDER BY DESC(?n)""",
    "measure_uri_ship": """
        SELECT ?m ?v WHERE {
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
          FILTER(CONTAINS(STR(?m), "Ship"))
        } LIMIT 10""",
    "count_ontoems_measures": """
        SELECT (COUNT(?m) AS ?n) WHERE {
          ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> .
          FILTER(CONTAINS(STR(?m), "ontoems/measure_"))
        }""",
}


def main() -> None:
    for name, q in QUERIES.items():
        print(f"\n=== {name} ===")
        try:
            rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=90)
            print(json.dumps(rows[:8], indent=2)[:1800])
            print(f"({len(rows)} rows)")
        except Exception as exc:
            print(f"ERR: {exc}")


if __name__ == "__main__":
    main()
