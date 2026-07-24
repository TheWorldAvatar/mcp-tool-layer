"""Probe kb dispersion chains: emissions, derivations, scope bbox, concentrations."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mini_marie.zaha.sg_old.local_store import db_path, ensure_db

OM_VAL = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasValue"
OM_NUM = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue"


def main() -> int:
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: dict = {}

    sqls = {
        "emission_measure_numerical": f"""
            SELECT e.s, meas.s, num.o
            FROM triples e
            JOIN triples hv ON hv.ns='kb' AND hv.s=e.s AND hv.p='{OM_VAL}'
            JOIN triples meas ON meas.ns='kb' AND meas.s=hv.o
            JOIN triples num ON num.ns='kb' AND num.s=meas.s AND num.p='{OM_NUM}'
            WHERE e.ns='kb' AND e.s LIKE '%Emission_%'
            LIMIT 10
        """,
        "derivation_timeseries_preds": """
            SELECT DISTINCT p, COUNT(*) n FROM triples
            WHERE ns='kb' AND s LIKE '%DerivationWithTimeSeries%'
            GROUP BY p ORDER BY n DESC
        """,
        "derivation_sample_triples": """
            SELECT p,o FROM triples
            WHERE ns='kb' AND s='https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2'
        """,
        "scope_instances": """
            SELECT s FROM triples WHERE ns='kb' AND o LIKE '%Scope%' AND p LIKE '%type%' LIMIT 10
        """,
        "scope_triples_sample": """
            SELECT s,p,o FROM triples WHERE ns='kb' AND s LIKE '%Scope%' AND p NOT LIKE '%rdfs%' LIMIT 20
        """,
        "aermod_derivation": """
            SELECT s,p,o FROM triples WHERE ns='kb'
            AND o LIKE '%Aermod%' OR s LIKE '%Aermod%' LIMIT 15
        """,
        "concentration_type_instances": """
            SELECT s,o FROM triples WHERE ns='kb' AND o LIKE '%Concentration%' AND p LIKE '%type%' LIMIT 15
        """,
        "carpark_create_nus": """
            SELECT s,o FROM triples WHERE ns='carpark' AND p LIKE '%label%'
            AND (LOWER(o) LIKE '%create%' OR LOWER(o) LIKE '%nus%' OR LOWER(o) LIKE '%university%' OR LOWER(o) LIKE '%kent ridge%')
        """,
        "pollutant_labels": """
            SELECT s,p,o FROM triples WHERE ns='kb' AND s LIKE '%PM10_%' AND p LIKE '%label%' LIMIT 8
        """,
    }

    for name, q in sqls.items():
        rows = conn.execute(q).fetchall()
        out[name] = rows
        print(f"=== {name} ({len(rows)}) ===")
        for r in rows[:10]:
            print(" ", r)

    conn.close()
    path = Path("data/mini_marie_cache/sg_old/kb_dispersion_probe.json")
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
