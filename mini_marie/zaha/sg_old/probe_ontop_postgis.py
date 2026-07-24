"""Probe whether sg-old Ontop maps PostGIS timeseries (ship speed, concentrations)."""
from __future__ import annotations

import json
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

QUERIES = {
    "classes_timeseries": """
        SELECT ?c (COUNT(?x) AS ?n) WHERE {
          ?x a ?c .
          FILTER(CONTAINS(STR(?c), "timeseries") || CONTAINS(STR(?c), "Timeseries")
                 || CONTAINS(STR(?c), "Measure") || CONTAINS(STR(?c), "Ship"))
        } GROUP BY ?c ORDER BY DESC(?n) LIMIT 15
    """,
    "ship_speed_value": """
        SELECT ?ship ?label ?speed WHERE {
          ?ship a <https://www.theworldavatar.com/kg/ontodispersion/Ship> .
          OPTIONAL { ?ship <http://www.w3.org/2000/01/rdf-schema#label> ?label }
          ?ship <https://www.theworldavatar.com/kg/ontodispersion/hasSpeedMeasure> ?m .
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?speed .
        } LIMIT 5
    """,
    "ship_speed_measure_props": """
        SELECT ?p ?o WHERE {
          <https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure> ?p ?o .
        } LIMIT 20
    """,
    "timeseries_data_props": """
        SELECT ?ts ?p ?o WHERE {
          ?ts a <https://www.theworldavatar.com/kg/ontotimeseries/TimeSeries> .
          ?ts ?p ?o .
          FILTER(?p != <http://www.w3.org/1999/02/22-rdf-syntax-ns#type>)
        } LIMIT 20
    """,
    "co_concentration_value": """
        SELECT ?q ?val WHERE {
          ?q a ?t .
          FILTER(CONTAINS(STR(?t), "Concentration"))
          ?q <https://www.theworldavatar.com/kg/ontoems/hasValue> ?m .
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?val .
        } LIMIT 5
    """,
    "virtual_sensor_count": """
        SELECT (COUNT(?vs) AS ?n) WHERE {
          ?vs a <https://www.theworldavatar.com/kg/ontodispersion/VirtualSensor> .
        }
    """,
    "ask_ship": """
        ASK { <https://www.theworldavatar.com/kg/ontodispersion/Ship563071320> a ?t }
    """,
}


def main() -> None:
    print(f"Endpoint: {ONTOP_ENDPOINT}\n")
    report = {}
    for name, q in QUERIES.items():
        print(f"=== {name} ===")
        try:
            rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=60)
            report[name] = rows
            print(json.dumps(rows[:8], indent=2)[:1500])
            print(f"({len(rows)} rows)")
        except Exception as exc:
            report[name] = {"error": str(exc)[:300]}
            print("ERROR:", exc)
        print()

    from mini_marie.cache_paths import mini_marie_cache_root
    out = mini_marie_cache_root() / "sg_old" / "probe_ontop_postgis.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
