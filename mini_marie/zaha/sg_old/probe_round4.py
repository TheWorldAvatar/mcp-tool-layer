"""
Round 4: push Q15/Q16/Q17 — feature-info, JS-mined APIs, virtual sensors, geocode batch.

Usage:
  python -m mini_marie.zaha.sg_old.probe_round4
  python -m mini_marie.zaha.sg_old.probe_round4 --geocode-live
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, parse, request

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old import label_store as ls
from mini_marie.zaha.sg_old.local_store import db_path, ensure_db
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"
OUT = mini_marie_cache_root() / "sg_old" / "probe_round4.json"

SHIP_TS = "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
DERIV = "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"
Z0 = "https://www.theworldavatar.com/kg/ontodispersion/eff43285-7a14-4c07-8dfa-121850b4f3d1"
JURONG = (1.33, 103.74)


def _http(url: str, *, method: str = "GET", data: bytes | None = None, timeout: float = 25) -> Dict[str, Any]:
    hdrs = {"User-Agent": UA}
    if data:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(8000).decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:2500]}
    except error.HTTPError as exc:
        body = exc.read(1200).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:600]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "ms": round((time.perf_counter() - t0) * 1000)}


def mine_js_apis() -> Dict[str, Any]:
    print("=== JS API mining ===", flush=True)
    out: Dict[str, Any] = {"paths": [], "snippets": []}
    for rel in ["visualisation/DispersionHandler.js", "visualisation/lib/twa-vf.min.js", "visualisation/component/component.min.js"]:
        r = _http(HOST + "/" + rel, timeout=40)
        body = r.get("body", "")
        paths = sorted(set(re.findall(r'["\'](/[a-zA-Z0-9_./\-]+(?:agent|interactor|timeseries|metadata|feature)[a-zA-Z0-9_./\-]*)["\']', body, re.I)))
        urls = sorted(set(re.findall(r"https?://[a-zA-Z0-9._:/\-?&=%]+", body)))
        twa = [u for u in urls if "theworldavatar" in u]
        out[rel] = {"paths": paths[:40], "twa_urls": twa[:20]}
        out["paths"].extend(paths)
        for token in ["timeseries", "GetColourBar", "VirtualSensor", "feature-info", "ShipData", "queryData", "parseData"]:
            i = body.find(token)
            if i >= 0:
                out["snippets"].append({"file": rel, "token": token, "ctx": body[max(0, i - 80) : i + 220]})
    out["paths"] = sorted(set(out["paths"]))[:50]
    return out


def probe_feature_info_and_agents() -> Dict[str, Any]:
    print("=== feature-info + agent paths ===", flush=True)
    out: Dict[str, Any] = {}
    paths = [
        "/feature-info-agent/get",
        "/feature-info-agent/",
        "/ShipDataAgent/",
        "/ShipDataAgent/query",
        "/VirtualSensorAgent/",
        "/AermodAgent/",
        "/stack-data-uploader/",
        "/access-agent/",
        "/ontotimeseries/",
    ]
    for p in paths:
        out[p] = _http(HOST + p)
        print(f"  {p}: {out[p].get('status') or out[p].get('http') or out[p].get('error')}", flush=True)

    # feature-info with coords (Jurong + CREATE)
    for name, lat, lon in [("Jurong", 1.33, 103.74), ("CREATE", 1.3039, 103.7735)]:
        params = {"lat": str(lat), "lon": str(lon), "x": str(lon), "y": str(lat)}
        out[f"feature_info_{name}"] = _http(HOST + "/feature-info-agent/get?" + parse.urlencode(params))
    return out


def probe_dispersion_jurong() -> Dict[str, Any]:
    print("=== Dispersion Jurong probes ===", flush=True)
    out: Dict[str, Any] = {}
    sim = _http(HOST + "/dispersion-interactor/GetDispersionSimulations")
    out["simulations"] = sim
    times: List[int] = []
    if sim.get("ok"):
        try:
            payload = json.loads(sim["body"])
            times = (payload.get("Singapore") or {}).get("time") or []
        except json.JSONDecodeError:
            pass

    colour_attempts = [
        {"derivationIri": DERIV, "zIri": Z0, "pollutant": "CO"},
        {"derivationIri": DERIV, "zIri": Z0, "pollutantIri": "https://www.theworldavatar.com/kg/ontodispersion/CO"},
    ]
    if times:
        colour_attempts.append(
            {"derivationIri": DERIV, "zIri": Z0, "pollutant": "CO", "time": str(times[0])}
        )
    out["colour_bar"] = {}
    for i, params in enumerate(colour_attempts):
        url = HOST + "/dispersion-interactor/GetColourBar?" + parse.urlencode(params)
        out["colour_bar"][str(i)] = _http(url)

    # Extra paths from common TWA patterns
    for path in [
        "/dispersion-interactor/GetRaster",
        "/dispersion-interactor/GetDispersionLayer",
        "/dispersion-interactor/GetVirtualSensorValues",
        f"/dispersion-interactor/GetVirtualSensorValues?lat={JURONG[0]}&lng={JURONG[1]}&pollutant=CO",
    ]:
        out[path] = _http(HOST + path)

    return out


def probe_ship_speed_http() -> Dict[str, Any]:
    print("=== Ship speed HTTP ===", flush=True)
    mmsi = "563071320"
    out: Dict[str, Any] = {}
    paths = [
        f"/timeseries-agent/query?timeseries={SHIP_TS}",
        f"/timeseries-agent/query?id={SHIP_TS}&limit=5",
        f"/access-agent/timeseries/{SHIP_TS}",
        f"/stack-data-uploader/timeseries/{SHIP_TS}",
        f"/ShipDataAgent/ship/{mmsi}",
        f"/ShipDataAgent/query?mmsi={mmsi}",
        f"/blazegraph/namespace/kb/sparql?query={parse.quote('SELECT ?p ?o WHERE { <https://www.theworldavatar.com/kg/ontotimeseries/' + SHIP_TS + '> ?p ?o }')}",
    ]
    for p in paths:
        out[p] = _http(HOST + p)
    return out


def probe_kb_virtual_sensors_and_reports() -> Dict[str, Any]:
    print("=== kb virtual sensors + ontoems reports ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: Dict[str, Any] = {}

    out["ontoems_reports_samples"] = conn.execute(
        """
        SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%reports%'
        LIMIT 15
        """
    ).fetchall()

    out["reporting_station_wkt"] = conn.execute(
        """
        SELECT s, p, o FROM triples WHERE ns='kb'
        AND (s LIKE '%weatherstation%' OR s LIKE '%ReportingStation%')
        AND (p LIKE '%wkt%' OR p LIKE '%asWKT%' OR LOWER(o) LIKE '%point%')
        LIMIT 10
        """
    ).fetchall()

    out["ship_numerical_anywhere"] = conn.execute(
        """
        SELECT s, p, o FROM triples WHERE ns='kb'
        AND (s LIKE '%563071320%' OR o LIKE '%563071320%')
        AND p LIKE '%hasNumericalValue%'
        LIMIT 10
        """
    ).fetchall()

    # Concentration measure with numerical?
    out["concentration_numerical"] = conn.execute(
        """
        SELECT m.s, num.o FROM triples m
        JOIN triples num ON num.ns='kb' AND num.s=m.s AND num.p LIKE '%hasNumericalValue%'
        WHERE m.ns='kb' AND m.s LIKE '%measure_%'
        LIMIT 10
        """
    ).fetchall()

    conn.close()
    return out


def geocode_near_create_live() -> Dict[str, Any]:
    print("=== Geocode near-CREATE (live) ===", flush=True)
    stats = ls.warm_carpark_geocodes_near_create(live_geocode=True, sleep_s=2.5)
    nearest = ls.find_nearest_carpark_to_create(limit=10)
    return {"warm": stats, "nearest": nearest}


def build_report(*, geocode_live: bool) -> Dict[str, Any]:
    t0 = time.perf_counter()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "js_apis": mine_js_apis(),
        "feature_info": probe_feature_info_and_agents(),
        "dispersion_jurong": probe_dispersion_jurong(),
        "ship_speed_http": probe_ship_speed_http(),
        "kb_sensors": probe_kb_virtual_sensors_and_reports(),
        "elapsed_seconds": 0.0,
    }
    if geocode_live:
        report["geocode"] = geocode_near_create_live()
    else:
        report["geocode"] = {"nearest": ls.find_nearest_carpark_to_create(limit=10)}
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--geocode-live", action="store_true")
    args = parser.parse_args()
    report = build_report(geocode_live=args.geocode_live)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUT} ({report['elapsed_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
