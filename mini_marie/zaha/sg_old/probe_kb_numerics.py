"""Mine kb cache for numerical values on location/speed/emission chains."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mini_marie.zaha.sg_old.local_store import db_path, ensure_db

HAS_NUM = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue"


def main() -> int:
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: dict = {}

    queries = {
        "emission_with_numerical": f"""
            SELECT e.s, num.o
            FROM triples e
            JOIN triples num ON num.ns='kb' AND num.s=e.s AND num.p='{HAS_NUM}'
            WHERE e.ns='kb' AND e.p LIKE '%type%' AND e.o LIKE '%Emission%'
            LIMIT 10
        """,
        "lat_measure_numerical": f"""
            SELECT m.s, num.o FROM triples m
            JOIN triples num ON num.ns='kb' AND num.s=m.s AND num.p='{HAS_NUM}'
            WHERE m.ns='kb' AND m.s LIKE '%LatMeasure%'
            LIMIT 10
        """,
        "lon_measure_numerical": f"""
            SELECT m.s, num.o FROM triples m
            JOIN triples num ON num.ns='kb' AND num.s=m.s AND num.p='{HAS_NUM}'
            WHERE m.ns='kb' AND m.s LIKE '%LonMeasure%'
            LIMIT 10
        """,
        "speed_measure_numerical": f"""
            SELECT m.s, num.o FROM triples m
            JOIN triples num ON num.ns='kb' AND num.s=m.s AND num.p='{HAS_NUM}'
            WHERE m.ns='kb' AND m.s LIKE '%SpeedMeasure%'
            LIMIT 10
        """,
        "location_measure_numerical": f"""
            SELECT m.s, num.o FROM triples m
            JOIN triples num ON num.ns='kb' AND num.s=m.s AND num.p='{HAS_NUM}'
            WHERE m.ns='kb' AND m.s LIKE '%LocationMeasure%'
            LIMIT 10
        """,
        "pollutant_emission_chain": """
            SELECT e.s, pid.o, num.o FROM triples e
            JOIN triples pid ON pid.ns='kb' AND pid.s=e.s AND pid.p LIKE '%hasPollutantID%'
            LEFT JOIN triples num ON num.ns='kb' AND num.s=e.s AND num.p LIKE '%hasNumericalValue%'
            WHERE e.ns='kb' AND e.p LIKE '%type%' AND e.o LIKE '%Emission%'
            LIMIT 15
        """,
        "derivation_subjects": """
            SELECT DISTINCT s FROM triples WHERE ns='kb' AND s LIKE '%Derivation%' LIMIT 15
        """,
        "simulation_subjects": """
            SELECT s,p,o FROM triples WHERE ns='kb'
            AND (LOWER(s) LIKE '%simul%' OR LOWER(o) LIKE '%simul%') LIMIT 15
        """,
    }

    for name, q in queries.items():
        try:
            rows = conn.execute(q).fetchall()
            out[name] = rows
            print(f"=== {name} ({len(rows)}) ===")
            for r in rows[:8]:
                print(" ", r)
        except Exception as exc:
            out[name] = f"ERR: {exc}"
            print(f"=== {name} ERR {exc}")

    conn.close()
    path = Path("data/mini_marie_cache/sg_old/kb_numerics_probe.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
