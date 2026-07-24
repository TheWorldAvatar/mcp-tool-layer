"""
Probe legacy 174.138.23.221:3839 vs sg-old for PostGIS/timeseries public exposure.

Usage:
  python -m mini_marie.zaha.sg_old.probe_singapore_endpoints
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib import error, parse, request

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

UA = "curl/8.0"
OUT = mini_marie_cache_root() / "sg_old" / "probe_singapore_endpoints.json"

LEGACY = "http://174.138.23.221:3839"
SG_OLD = "https://sg-old.theworldavatar.io"

ENDPOINTS: Dict[str, Dict[str, str]] = {
    "ontop_api": {
        "legacy": f"{LEGACY}/ontop/sparql/",
        "sg_old": f"{SG_OLD}/ontop/sparql/",
    },
    "ontop_ui": {
        "legacy": f"{LEGACY}/ontop/ui/sparql",
        "sg_old": f"{SG_OLD}/ontop/ui/sparql",
    },
    "dispersion_kb": {
        "legacy": f"{LEGACY}/blazegraph/namespace/kb/sparql",
        "sg_old": f"{SG_OLD}/blazegraph/namespace/kb/sparql",
    },
    "plot": {
        "legacy": f"{LEGACY}/blazegraph/namespace/plot/sparql",
        "sg_old": f"{SG_OLD}/blazegraph/namespace/plot/sparql",
    },
    "carpark": {
        "legacy": f"{LEGACY}/blazegraph/namespace/carpark/sparql",
        "sg_old": f"{SG_OLD}/blazegraph/namespace/carpark/sparql",
    },
    "feature_info": {
        "legacy": f"{LEGACY}/feature-info-agent/get",
        "sg_old": f"{SG_OLD}/feature-info-agent/get",
    },
    "pollutant_concentration": {
        "legacy": f"{LEGACY}/dispersion-interactor/GetPollutantConcentrations",
        "sg_old": f"{SG_OLD}/dispersion-interactor/GetPollutantConcentrations",
    },
    "dispersion_simulations": {
        "legacy": f"{LEGACY}/dispersion-interactor/GetDispersionSimulations",
        "sg_old": f"{SG_OLD}/dispersion-interactor/GetDispersionSimulations",
    },
    "timeseries_agent": {
        "legacy": f"{LEGACY}/timeseries-agent/query",
        "sg_old": f"{SG_OLD}/timeseries-agent/query",
    },
    "access_agent": {
        "legacy": f"{LEGACY}/access-agent/timeseries",
        "sg_old": f"{SG_OLD}/access-agent/timeseries",
    },
}

VALUE_QUERIES = {
    "ship_count_ontop": """
        SELECT (COUNT(?s) AS ?n) WHERE {
          ?s a <https://www.theworldavatar.com/kg/ontodispersion/Ship> .
        }
    """,
    "timeseries_count_ontop": """
        SELECT (COUNT(?t) AS ?n) WHERE {
          ?t a <https://www.theworldavatar.com/kg/ontotimeseries/TimeSeries> .
        }
    """,
    "ship_speed_via_ontop": """
        SELECT ?ship ?label ?speed WHERE {
          ?ship a <https://www.theworldavatar.com/kg/ontodispersion/Ship> .
          OPTIONAL { ?ship <http://www.w3.org/2000/01/rdf-schema#label> ?label }
          ?ship <https://www.theworldavatar.com/kg/ontodispersion/hasSpeedMeasure> ?m .
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?speed .
        } LIMIT 3
    """,
    "hasTimeSeries_links_ontop": """
        SELECT ?m ?ts WHERE {
          ?m <https://www.theworldavatar.com/kg/ontotimeseries/hasTimeSeries> ?ts .
        } LIMIT 5
    """,
    "ship_speed_via_kb": """
        SELECT ?p ?o WHERE {
          <https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure> ?p ?o .
        } LIMIT 15
    """,
    "co_conc_via_kb": """
        SELECT ?q ?val WHERE {
          ?q a ?t .
          FILTER(CONTAINS(STR(?t), "CarbonMonoxideConcentration"))
          ?q <https://www.theworldavatar.com/kg/ontoems/hasValue> ?m .
          ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?val .
        } LIMIT 3
    """,
}

SHIP_TS = "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
SHIP_IRI = "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320"
SPEED_MEASURE = "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure"
DERIV = "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"


def tcp_probe(host: str, port: int, timeout: float = 4) -> Dict[str, Any]:
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return {"open": True, "ms": round((time.perf_counter() - t0) * 1000)}
    except OSError as exc:
        return {"open": False, "ms": round((time.perf_counter() - t0) * 1000), "error": str(exc)}
    finally:
        sock.close()


def http_get(url: str, *, timeout: float = 25) -> Dict[str, Any]:
    t0 = time.perf_counter()
    req = request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(6000)
            ctype = resp.headers.get("Content-Type", "")
            if "image" in ctype:
                return {"ok": True, "status": resp.status, "ctype": ctype, "bytes": len(raw), "ms": round((time.perf_counter() - t0) * 1000)}
            text = raw.decode("utf-8", errors="replace")
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = text[:1500]
            return {"ok": True, "status": resp.status, "ctype": ctype, "body": body, "ms": round((time.perf_counter() - t0) * 1000)}
    except error.HTTPError as exc:
        body = exc.read(1200).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "body": body[:600], "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:250], "ms": round((time.perf_counter() - t0) * 1000)}


def sparql_rows(endpoint: str, query: str, timeout: int = 45) -> Dict[str, Any]:
    try:
        rows = execute_sparql_get(query, endpoint, timeout=timeout)
        return {"ok": True, "row_count": len(rows), "rows": rows[:8]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400]}


def probe_reachability() -> Dict[str, Any]:
    print("=== TCP + endpoint reachability ===", flush=True)
    out: Dict[str, Any] = {"tcp": {}, "endpoints": {}}
    out["tcp"]["174.138.23.221:3839"] = tcp_probe("174.138.23.221", 3839)
    out["tcp"]["sg-old:443"] = tcp_probe("sg-old.theworldavatar.io", 443)
    print(f"  legacy 3839: {out['tcp']['174.138.23.221:3839']}", flush=True)
    print(f"  sg-old 443: {out['tcp']['sg-old:443']}", flush=True)

    for name, urls in ENDPOINTS.items():
        out["endpoints"][name] = {}
        for label, url in urls.items():
            if label == "legacy" and not out["tcp"]["174.138.23.221:3839"]["open"]:
                out["endpoints"][name][label] = {"skipped": "tcp_closed"}
                continue
            if "sparql" in url:
                r = sparql_rows(url, "ASK { }", timeout=20)
                out["endpoints"][name][label] = r
                tag = "OK" if r.get("ok") else r.get("error", "?")[:40]
            else:
                r = http_get(url)
                out["endpoints"][name][label] = {
                    "ok": r.get("ok"),
                    "status": r.get("status") or r.get("http") or r.get("error"),
                    "ms": r.get("ms"),
                    "preview": str(r.get("body", ""))[:120],
                }
                tag = out["endpoints"][name][label]["status"]
            print(f"  {name}/{label}: {tag}", flush=True)
    return out


def probe_value_queries() -> Dict[str, Any]:
    print("\n=== Value-bearing SPARQL (Ontop + kb) ===", flush=True)
    out: Dict[str, Any] = {}
    targets = [
        ("legacy_ontop", ENDPOINTS["ontop_api"]["legacy"]),
        ("sg_old_ontop", ENDPOINTS["ontop_api"]["sg_old"]),
        ("legacy_kb", ENDPOINTS["dispersion_kb"]["legacy"]),
        ("sg_old_kb", ENDPOINTS["dispersion_kb"]["sg_old"]),
    ]
    for target_name, ep in targets:
        if "174.138" in ep and not tcp_probe("174.138.23.221", 3839, timeout=2)["open"]:
            out[target_name] = {"skipped": "tcp_closed"}
            continue
        out[target_name] = {}
        for qname, q in VALUE_QUERIES.items():
            if "ontop" in qname and "kb" in ep:
                continue
            if "kb" in qname and "ontop" in ep:
                continue
            r = sparql_rows(ep, q, timeout=60)
            out[target_name][qname] = r
            rc = r.get("row_count", 0) if r.get("ok") else r.get("error", "?")[:50]
            print(f"  {target_name}/{qname}: {rc}", flush=True)
    return out


def probe_http_value_patterns() -> Dict[str, Any]:
    print("\n=== HTTP value patterns (feature-info, pollutant, timeseries) ===", flush=True)
    out: Dict[str, Any] = {}
    legacy_open = tcp_probe("174.138.23.221", 3839, timeout=2)["open"]

    # Fetch simulation metadata for timestep params
    sim_meta: Dict[str, Any] = {}
    for label, base in [("sg_old", SG_OLD), ("legacy", LEGACY)]:
        if label == "legacy" and not legacy_open:
            continue
        r = http_get(f"{base}/dispersion-interactor/GetDispersionSimulations")
        if r.get("ok") and isinstance(r.get("body"), dict):
            sim_meta[label] = (r["body"].get("Singapore") or {})

    timestep = ""
    deriv = DERIV
    z0 = "https://www.theworldavatar.com/kg/ontodispersion/eff43285-7a14-4c07-8dfa-121850b4f3d1"
    if sim_meta.get("sg_old"):
        times = sim_meta["sg_old"].get("time") or []
        timestep = str(times[0]) if times else ""
        deriv = sim_meta["sg_old"].get("derivationIri") or deriv
        zmap = sim_meta["sg_old"].get("z") or {}
        z0 = zmap.get("0") or z0

    hosts = [("sg_old", SG_OLD)]
    if legacy_open:
        hosts.append(("legacy", LEGACY))

    for host_label, base in hosts:
        out[host_label] = {}

        # feature-info (iri + endpoint per twa-vf.min.js)
        fi_params = [
            {"iri": SHIP_IRI, "endpoint": base},
            {"iri": SPEED_MEASURE, "endpoint": base},
            {"iri": f"https://www.theworldavatar.com/kg/ontotimeseries/{SHIP_TS}", "endpoint": base},
            {"iri": SHIP_IRI, "endpoint": base + "/blazegraph/namespace/kb/sparql"},
        ]
        out[host_label]["feature_info"] = {}
        for i, p in enumerate(fi_params):
            url = f"{base}/feature-info-agent/get?" + parse.urlencode(p)
            out[host_label]["feature_info"][str(i)] = http_get(url)

        # GetPollutantConcentrations with full JS params
        conc_params = {
            "lat": "1.33", "lng": "103.74", "pollutant": "CO",
            "timestep": timestep, "derivationIri": deriv, "zIri": z0,
        }
        url = f"{base}/dispersion-interactor/GetPollutantConcentrations?" + parse.urlencode(conc_params)
        out[host_label]["pollutant_concentration"] = http_get(url)

        # timeseries-agent patterns
        ts_paths = [
            f"/timeseries-agent/query?timeseries={SHIP_TS}",
            f"/timeseries-agent/query?id={SHIP_TS}&limit=5",
            f"/access-agent/timeseries/{SHIP_TS}",
            f"/stack-data-uploader/timeseries/{SHIP_TS}",
            f"/timeseries-agent/timeseries/{SHIP_TS}",
        ]
        out[host_label]["timeseries_paths"] = {}
        for p in ts_paths:
            out[host_label]["timeseries_paths"][p] = http_get(base + p)

        # Extra legacy-only path variants on 3839
        if host_label == "legacy":
            extra = [
                "/ontotimeseries/",
                "/VirtualSensorAgent/query",
                "/ShipDataAgent/query?mmsi=563071320",
                "/postgis/",
                "/adminer/",
            ]
            out[host_label]["extra_paths"] = {p: http_get(base + p) for p in extra}

        fi0 = out[host_label]["feature_info"].get("0", {})
        pc = out[host_label]["pollutant_concentration"]
        print(
            f"  {host_label}: feature-info[0]={fi0.get('status') or fi0.get('http')}; "
            f"pollutant={pc.get('status') or pc.get('http')}",
            flush=True,
        )
    return out


def build_report() -> Dict[str, Any]:
    t0 = time.perf_counter()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reachability": probe_reachability(),
        "value_queries": probe_value_queries(),
        "http_value_patterns": probe_http_value_patterns(),
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
