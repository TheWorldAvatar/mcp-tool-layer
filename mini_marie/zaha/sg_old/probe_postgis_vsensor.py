"""
PostGIS + virtual sensor push: HTTP surface, browser-like POST, kb agent ops.

Usage:
  python -m mini_marie.zaha.sg_old.probe_postgis_vsensor
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

HOST = "https://sg-old.theworldavatar.io"
VIZ = HOST + "/visualisation/"
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
UA_CURL = "curl/8.0"
OUT = mini_marie_cache_root() / "sg_old" / "probe_postgis_vsensor.json"

SHIP_TS = "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
CO_TS = "Timeseries_ee9a9039-0da0-4caa-92b7-57f6c207a05c"
DERIV = "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"


def _http(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: Dict[str, str] | None = None,
    timeout: float = 30,
) -> Dict[str, Any]:
    hdrs = {"User-Agent": UA_CURL, **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(12000)
            ctype = resp.headers.get("Content-Type", "")
            if "image" in ctype or raw[:4] == b"\x89PNG":
                body = f"<binary {len(raw)} bytes content-type={ctype}>"
            else:
                body = raw.decode("utf-8", errors="replace")[:3000]
            return {"ok": True, "status": resp.status, "ctype": ctype, "ms": round((time.perf_counter() - t0) * 1000), "body": body}
    except error.HTTPError as exc:
        body = exc.read(1500).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:800]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:250], "ms": round((time.perf_counter() - t0) * 1000)}


def probe_http_surface() -> Dict[str, Any]:
    print("=== HTTP surface ===", flush=True)
    paths = [
        "/adminer/",
        "/adminer/ui/",
        "/stack-data-uploader/",
        "/stack-data-uploader/timeseries",
        "/access-agent/",
        "/access-agent/timeseries",
        "/timeseries-agent/",
        "/timeseries-agent/query",
        "/timeseries-agent/timeseries",
        "/JPSAccessAgent/",
        "/DerivationAgent/",
        "/MetainformationAgent/",
        "/VirtualSensorAgent/",
        "/VirtualSensorAgent/query",
        "/feature-info-agent/get",
        "/carpark-agent/",
        "/landplot-agent/",
    ]
    out = {}
    for p in paths:
        out[p] = _http(HOST + p)
        print(f"  {p}: {out[p].get('status') or out[p].get('http')}", flush=True)
    return out


def probe_timeseries_reads() -> Dict[str, Any]:
    print("=== Timeseries read variants ===", flush=True)
    out: Dict[str, Any] = {}
    bodies = [
        {"timeseries": SHIP_TS},
        {"id": SHIP_TS},
        {"iri": f"https://www.theworldavatar.com/kg/ontotimeseries/{SHIP_TS}"},
        {"timeseries": CO_TS, "limit": 5},
        {"table": SHIP_TS},
    ]
    base = HOST + "/timeseries-agent/query"
    for i, body in enumerate(bodies):
        payload = json.dumps(body).encode()
        out[f"post_json_{i}"] = _http(
            base,
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": UA_CURL},
        )
        out[f"get_{i}"] = _http(base + "?" + parse.urlencode({k: str(v) for k, v in body.items()}))

    for ts in [SHIP_TS, CO_TS]:
        for prefix in ["/access-agent/timeseries/", "/stack-data-uploader/timeseries/", "/timeseries-agent/timeseries/"]:
            out[prefix + ts] = _http(HOST + prefix + ts)
    return out


def probe_virtual_sensor_posts() -> Dict[str, Any]:
    print("=== Virtual sensor + dispersion POST ===", flush=True)
    out: Dict[str, Any] = {}
    jurong = {"lat": "1.33", "lng": "103.74"}
    create = {"lat": "1.3039", "lng": "103.7735"}

    for name, coords in [("jurong", jurong), ("create", create)]:
        for pollutant in ["CO", "PM2.5", "PM10"]:
            params = {**coords, "pollutant": pollutant}
            url = HOST + "/dispersion-interactor/CreateVirtualSensor?" + parse.urlencode(params)
            # Browser-like POST (jQuery $.post)
            out[f"create_vs_{name}_{pollutant}_browser"] = _http(
                url,
                method="POST",
                data=b"",
                headers={
                    "User-Agent": UA_BROWSER,
                    "Referer": VIZ,
                    "Origin": HOST,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            out[f"create_vs_{name}_{pollutant}_curl"] = _http(url, method="POST", data=b"")

    upd = HOST + "/dispersion-interactor/UpdateVirtualSensors?" + parse.urlencode({"derivation": DERIV})
    out["update_vs_browser"] = _http(
        upd,
        method="POST",
        data=b"",
        headers={"User-Agent": UA_BROWSER, "Referer": VIZ, "Origin": HOST},
    )

    # After update, try read endpoints
    for path in [
        "/dispersion-interactor/GetVirtualSensorValues",
        f"/dispersion-interactor/GetVirtualSensorValues?lat=1.33&lng=103.74&pollutant=CO&derivationIri={parse.quote(DERIV)}",
        f"/dispersion-interactor/GetPollutantConcentrations?lat=1.33&lng=103.74&pollutant=CO",
    ]:
        out[path] = _http(HOST + path)

    # GetColourBar full JS params + simulation time
    z0 = "https://www.theworldavatar.com/kg/ontodispersion/eff43285-7a14-4c07-8dfa-121850b4f3d1"
    sim = _http(HOST + "/dispersion-interactor/GetDispersionSimulations")
    t0 = ""
    if sim.get("ok"):
        try:
            t0 = str(json.loads(sim["body"])["Singapore"]["time"][0])
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    cb_params = {"derivationIri": DERIV, "zIri": z0, "pollutant": "CO", "time": t0} if t0 else {
        "derivationIri": DERIV,
        "zIri": z0,
        "pollutant": "CO",
    }
    out["colour_bar_full"] = _http(HOST + "/dispersion-interactor/GetColourBar?" + parse.urlencode(cb_params))
    return out


def probe_kb_agents() -> Dict[str, Any]:
    print("=== kb agent metadata ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: Dict[str, Any] = {}

    out["timeseries_preds"] = conn.execute(
        """
        SELECT p, COUNT(*) FROM triples WHERE ns='kb' AND s LIKE '%Timeseries_%'
        GROUP BY p
        """
    ).fetchall()

    out["incoming_timeseries_preds"] = conn.execute(
        """
        SELECT p, COUNT(*) FROM triples WHERE ns='kb' AND o LIKE '%Timeseries_%'
        GROUP BY p ORDER BY COUNT(*) DESC LIMIT 15
        """
    ).fetchall()

    out["hasRDB_distinct"] = conn.execute(
        "SELECT DISTINCT o FROM triples WHERE ns='kb' AND p LIKE '%hasRDB%'"
    ).fetchall()

    out["agent_http_urls"] = conn.execute(
        "SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%hasHttpUrl%'"
    ).fetchall()

    out["agent_operations"] = conn.execute(
        """
        SELECT s, p, o FROM triples WHERE ns='kb'
        AND (p LIKE '%hasOperation%' OR p LIKE '%hasEndpoint%')
        LIMIT 25
        """
    ).fetchall()

    # Virtual sensor full record sample
    vs = conn.execute(
        "SELECT s FROM triples WHERE ns='kb' AND s LIKE '%virtualsensor_%' AND p LIKE '%type%' LIMIT 1"
    ).fetchone()
    if vs:
        out["virtual_sensor_triples"] = conn.execute(
            "SELECT p, o FROM triples WHERE ns='kb' AND s=?", (vs[0],)
        ).fetchall()

    # CO measure -> timeseries full chain
    out["co_measure_chain"] = conn.execute(
        """
        SELECT m.s, ts.s, rdb.o, tu.o
        FROM triples q
        JOIN triples t ON t.ns='kb' AND t.s=q.s AND t.o LIKE '%CarbonMonoxideConcentration%'
        JOIN triples hv ON hv.ns='kb' AND hv.s=q.s AND hv.p LIKE '%hasValue%'
        JOIN triples m ON m.ns='kb' AND m.s=hv.o
        JOIN triples ts ON ts.ns='kb' AND ts.s=m.s AND ts.p LIKE '%hasTimeSeries%'
        JOIN triples rdb ON rdb.ns='kb' AND rdb.s=ts.o AND rdb.p LIKE '%hasRDB%'
        LEFT JOIN triples tu ON tu.ns='kb' AND tu.s=ts.o AND tu.p LIKE '%hasTimeUnit%'
        WHERE q.ns='kb'
        LIMIT 1
        """
    ).fetchone()

    conn.close()
    return out


def mine_dispersion_js() -> Dict[str, Any]:
    print("=== DispersionHandler JS ===", flush=True)
    r = _http(HOST + "/visualisation/DispersionHandler.js", timeout=40)
    body = r.get("body", "")
    snippets = []
    for fn in ["createVirtualSensor", "updateVirtualSensors", "GetColourBar", "queryForDispersions", "dispersion"]:
        i = 0
        while True:
            i = body.find(fn, i)
            if i < 0:
                break
            snippets.append(body[max(0, i - 100) : i + 400])
            i += len(fn)
    return {"snippet_count": len(snippets), "snippets": snippets[:8]}


def build_report() -> Dict[str, Any]:
    t0 = time.perf_counter()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "http_surface": probe_http_surface(),
        "timeseries_reads": probe_timeseries_reads(),
        "virtual_sensor_posts": probe_virtual_sensor_posts(),
        "kb_agents": probe_kb_agents(),
        "dispersion_js": mine_dispersion_js(),
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
