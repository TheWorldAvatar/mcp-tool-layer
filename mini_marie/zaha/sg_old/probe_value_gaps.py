"""
Probe intermediate nodes between measures/timeseries and numeric values (Q15/Q16),
and build carpark/building label indexes for fuzzy nearest-carpark (Q17).

Usage:
  python -m mini_marie.zaha.sg_old.probe_value_gaps
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, parse, request

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.local_store import db_path, ensure_db
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"
OUT = mini_marie_cache_root() / "sg_old" / "value_gaps_probe.json"

SHIP_TS = "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
CO_TS = "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_ee9a9039-0da0-4caa-92b7-57f6c207a05c"
DERIV = "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"


def _http(url: str, *, method: str = "GET", data: bytes | None = None, headers: Dict[str, str] | None = None, timeout: float = 25) -> Dict[str, Any]:
    hdrs = {"User-Agent": UA, **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(12000).decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:3000]}
    except error.HTTPError as exc:
        body = exc.read(1200).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:800]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "ms": round((time.perf_counter() - t0) * 1000)}


def probe_timeseries_node_gaps() -> Dict[str, Any]:
    print("=== Timeseries node gap mining (kb SQLite) ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: Dict[str, Any] = {}

    for label, iri in [("ship_speed_ts", SHIP_TS), ("co_conc_ts", CO_TS)]:
        out[f"{label}_all_triples"] = conn.execute(
            "SELECT p,o FROM triples WHERE ns='kb' AND s=? ORDER BY p", (iri,)
        ).fetchall()
        out[f"{label}_incoming"] = conn.execute(
            "SELECT s,p FROM triples WHERE ns='kb' AND o=? ORDER BY p LIMIT 30", (iri,)
        ).fetchall()

    out["timeseries_distinct_preds"] = conn.execute(
        """
        SELECT p, COUNT(*) n FROM triples
        WHERE ns='kb' AND s LIKE '%Timeseries_%'
        GROUP BY p ORDER BY n DESC LIMIT 25
        """
    ).fetchall()

    out["hasRDB_samples"] = conn.execute(
        "SELECT s,o FROM triples WHERE ns='kb' AND p LIKE '%hasRDB%' LIMIT 10"
    ).fetchall()

    # Intermediate nodes on ship speed chain
    ship_measure = "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure"
    out["ship_speed_measure_triples"] = conn.execute(
        "SELECT p,o FROM triples WHERE ns='kb' AND s=? ORDER BY p", (ship_measure,)
    ).fetchall()

    # Any hasNumericalValue on measure nodes linked to timeseries
    out["measure_with_numerical_and_ts"] = conn.execute(
        """
        SELECT m.s, num.o, ts.o
        FROM triples m
        JOIN triples num ON num.ns='kb' AND num.s=m.s AND num.p LIKE '%hasNumericalValue%'
        JOIN triples ts ON ts.ns='kb' AND ts.s=m.s AND ts.p LIKE '%hasTimeSeries%'
        WHERE m.ns='kb' AND m.s LIKE '%Measure%'
        LIMIT 15
        """
    ).fetchall()

    # Virtual sensor / dispersion output links
    out["virtual_sensor_preds"] = conn.execute(
        """
        SELECT DISTINCT p, COUNT(*) n FROM triples
        WHERE ns='kb' AND (s LIKE '%VirtualSensor%' OR o LIKE '%VirtualSensor%')
        GROUP BY p ORDER BY n DESC LIMIT 15
        """
    ).fetchall()

    out["dispersion_output_triples"] = conn.execute(
        """
        SELECT s,p,o FROM triples WHERE ns='kb'
        AND (s LIKE '%DispersionOutput%' OR p LIKE '%hasDispersionMatrix%' OR p LIKE '%hasOutput%')
        LIMIT 20
        """
    ).fetchall()

    # Derivation output timeseries link (hasOutput / produces / etc.)
    out["derivation_all_preds_on_sample"] = conn.execute(
        "SELECT p,o FROM triples WHERE ns='kb' AND s=? ORDER BY p", (DERIV,)
    ).fetchall()

    conn.close()
    return out


def probe_timeseries_http_variants() -> Dict[str, Any]:
    print("=== Timeseries HTTP variants ===", flush=True)
    out: Dict[str, Any] = {}
    ts_id = "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
    paths = [
        f"/blazegraph/namespace/kb/sparql?query={parse.quote('SELECT * WHERE { <' + SHIP_TS + '> ?p ?o } LIMIT 20')}",
        f"/stack-data-uploader/timeseries/{ts_id}",
        f"/access-agent/timeseries/{ts_id}",
        f"/timeseries-agent/timeseries/{ts_id}",
        f"/timeseries-agent/data?id={ts_id}",
        "/metadata/timeseries/" + ts_id,
        "/visualisation/metadata/timeseries/" + ts_id,
    ]
    for p in paths:
        out[p] = _http(HOST + p if p.startswith("/") else p)
        print(f"  {p[:60]} -> {out[p].get('status') or out[p].get('http') or out[p].get('error')}", flush=True)

    # Mine component.min.js for timeseries URL patterns
    js = _http(HOST + "/visualisation/component/component.min.js", timeout=40)
    body = js.get("body", "")
    patterns = sorted(set(re.findall(r'["\'](/[^"\']*(?:timeseries|Timeseries|metadata)[^"\']*)["\']', body)))
    out["component_timeseries_paths"] = patterns[:30]
    return out


def probe_dispersion_value_apis() -> Dict[str, Any]:
    print("=== Dispersion value APIs ===", flush=True)
    out: Dict[str, Any] = {}
    js = _http(HOST + "/visualisation/DispersionHandler.js", timeout=40).get("body", "")

    # Extract fetch/ajax URL + param snippets around dispersion-interactor calls
    snippets: List[str] = []
    for m in re.finditer(r"dispersion-interactor/[A-Za-z]+", js):
        start = max(0, m.start() - 120)
        snippets.append(js[start : m.end() + 200])
    out["dispersion_js_snippets"] = snippets[:6]

    sim = _http(HOST + "/dispersion-interactor/GetDispersionSimulations")
    out["get_dispersion_simulations"] = sim
    if sim.get("ok"):
        try:
            payload = json.loads(sim["body"])
            out["simulation_keys"] = {k: list(v.keys()) for k, v in payload.items()}
        except json.JSONDecodeError:
            pass

    # Try GetColourBar with query params from simulation JSON
    colour_params = [
        {"pollutant": "CO", "derivationIri": DERIV},
        {"pollutantIri": "https://www.theworldavatar.com/kg/ontodispersion/CO", "derivationIri": DERIV},
        {"location": "Jurong Island", "pollutant": "CO"},
        {"lat": "1.33", "lon": "103.74", "pollutant": "CO", "derivationIri": DERIV},
        {"x": "103.74", "y": "1.33", "pollutant": "CO"},
    ]
    out["get_colour_bar"] = {}
    for i, params in enumerate(colour_params):
        url = HOST + "/dispersion-interactor/GetColourBar?" + parse.urlencode(params)
        out["get_colour_bar"][str(i)] = _http(url)
        print(f"  GetColourBar {i}: {out['get_colour_bar'][str(i)].get('status') or out['get_colour_bar'][str(i)].get('http')}", flush=True)

    # Live kb SPARQL for virtual sensor at Jurong coords
    jurong_q = """
SELECT ?vs ?p ?o WHERE {
  ?vs a <https://www.theworldavatar.com/kg/ontodispersion/VirtualSensor> .
  ?vs ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?o)), "jurong") || CONTAINS(LCASE(STR(?o)), "103.7"))
} LIMIT 10"""
    try:
        out["ontop_virtual_sensor_jurong"] = execute_sparql_get(jurong_q, ONTOP_ENDPOINT, timeout=60)
    except Exception as exc:
        out["ontop_virtual_sensor_jurong"] = {"error": str(exc)[:300]}

    bg_q = f"""
SELECT ?p ?o WHERE {{
  <{DERIV}> ?p ?o .
}} LIMIT 30"""
    try:
        out["derivation_live_sparql"] = execute_sparql_get(
            bg_q, HOST + "/blazegraph/namespace/kb/sparql", timeout=60
        )
    except Exception as exc:
        out["derivation_live_sparql"] = {"error": str(exc)[:300]}

    return out


def build_label_indexes() -> Dict[str, Any]:
    print("=== Label indexes (carpark + building + kb) ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(db_path())
    cache_dir = mini_marie_cache_root() / "sg_old"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Carparks: IRI + label
    carpark_rows = conn.execute(
        "SELECT s, o FROM triples WHERE ns='carpark' AND p LIKE '%label%'"
    ).fetchall()
    carpark_path = cache_dir / "carpark_labels.sqlite"
    if carpark_path.exists():
        carpark_path.unlink()
    cp = sqlite3.connect(carpark_path)
    cp.execute("CREATE TABLE carpark_label (iri TEXT PRIMARY KEY, label TEXT, label_lc TEXT)")
    cp.executemany(
        "INSERT OR REPLACE INTO carpark_label VALUES (?,?,?)",
        [(s, o, (o or "").lower()) for s, o in carpark_rows],
    )
    cp.execute("CREATE INDEX idx_carpark_label_lc ON carpark_label(label_lc)")
    cp.commit()
    cp.close()

    # kb labels (buildings, facilities, places in kb namespace)
    kb_label_rows = conn.execute(
        "SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%label%' AND LENGTH(o) > 2 LIMIT 50000"
    ).fetchall()
    kb_path = cache_dir / "kb_labels.sqlite"
    if kb_path.exists():
        kb_path.unlink()
    kp = sqlite3.connect(kb_path)
    kp.execute("CREATE TABLE kb_label (iri TEXT PRIMARY KEY, label TEXT, label_lc TEXT)")
    kp.executemany(
        "INSERT OR REPLACE INTO kb_label VALUES (?,?,?)",
        [(s, o, (o or "").lower()) for s, o in kb_label_rows],
    )
    kp.execute("CREATE INDEX idx_kb_label_lc ON kb_label(label_lc)")
    kp.commit()

    def fuzzy(kw: str, table: str = "kb_label", limit: int = 15) -> List[tuple]:
        return kp.execute(
            f"SELECT iri, label FROM {table} WHERE label_lc LIKE ? LIMIT ?",
            (f"%{kw.lower()}%", limit),
        ).fetchall()

    out: Dict[str, Any] = {
        "carpark_label_count": len(carpark_rows),
        "kb_label_count": len(kb_label_rows),
        "carpark_labels_db": str(carpark_path),
        "kb_labels_db": str(kb_path),
    }

    create_terms = ["create", "tower", "nus", "kent ridge", "one-north", "one north", "fusionopolis", "science park", "university town", "engineering drive", "create way"]
    out["kb_fuzzy_create"] = {t: fuzzy(t) for t in create_terms}
    out["kb_fuzzy_jurong"] = {t: fuzzy(t) for t in ["jurong", "jurong island", "jurong town"]}

    cp2 = sqlite3.connect(carpark_path)
    out["carpark_fuzzy_near_create"] = {}
    for t in ["kent ridge", "nus", "science park", "one north", "fusionopolis", "clementi", "pasir panjang", "lower kent ridge", "engineering", "create"]:
        out["carpark_fuzzy_near_create"][t] = cp2.execute(
            "SELECT iri, label FROM carpark_label WHERE label_lc LIKE ? LIMIT 8",
            (f"%{t}%",),
        ).fetchall()
    cp2.close()
    kp.close()

    # Ontop building names fuzzy
    from mini_marie.zaha.sg_old.ontop_store import connect, cache_ready

    if cache_ready():
        oc = connect()
        out["building_fuzzy_create"] = {}
        for t in create_terms:
            out["building_fuzzy_create"][t] = oc.execute(
                "SELECT building_iri, name FROM facet_building_name WHERE name_lc LIKE ? LIMIT 8",
                (f"%{t.lower()}%",),
            ).fetchall()
        oc.close()

    conn.close()
    return out


def probe_create_tower_coords() -> Dict[str, Any]:
    print("=== CREATE Tower coordinate hunt ===", flush=True)
    out: Dict[str, Any] = {}
    queries = {
        "kb_create_label": """
SELECT ?s ?l WHERE {
  ?s <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "create") && CONTAINS(LCASE(STR(?l)), "tower"))
} LIMIT 10""",
        "ontop_create_facility": """
SELECT ?b ?l ?wkt WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "create") || CONTAINS(LCASE(STR(?l)), "campus") || CONTAINS(LCASE(STR(?l)), "nus"))
  OPTIONAL { ?b <http://www.opengis.net/ont/geosparql#hasGeometry> ?g . ?g <http://www.opengis.net/ont/geosparql#asWKT> ?wkt . }
} LIMIT 15""",
        "ontop_lower_kent_ridge": """
SELECT ?b ?l WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "kent ridge") || CONTAINS(LCASE(STR(?l)), "lower kent"))
} LIMIT 10""",
    }
    for name, q in queries.items():
        ep = HOST + "/blazegraph/namespace/kb/sparql" if name.startswith("kb") else ONTOP_ENDPOINT
        try:
            rows = execute_sparql_get(q, ep, timeout=90)
            out[name] = {"ok": True, "rows": rows[:10]}
            print(f"  {name}: {len(rows)} rows", flush=True)
        except Exception as exc:
            out[name] = {"ok": False, "error": str(exc)[:300]}
            print(f"  {name}: err", flush=True)
    return out


def build_report() -> Dict[str, Any]:
    t0 = time.perf_counter()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "timeseries_gaps": probe_timeseries_node_gaps(),
        "timeseries_http": probe_timeseries_http_variants(),
        "dispersion_apis": probe_dispersion_value_apis(),
        "label_indexes": build_label_indexes(),
        "create_tower": probe_create_tower_coords(),
        "elapsed_seconds": 0.0,
    }
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    report = build_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUT} ({report['elapsed_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
