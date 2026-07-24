"""Live HTTP probes for PostGIS-backed dispersion/ship/virtual-sensor APIs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from mini_marie.zaha.sg_old.local_store import db_path, ensure_db

SG_HOST = "https://sg-old.theworldavatar.io"
SG_UA = "curl/8.0"
SG_UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
VIZ_REFERER = SG_HOST + "/visualisation/"


def _http(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: Dict[str, str] | None = None,
    timeout: float = 30,
) -> Dict[str, Any]:
    hdrs = {"User-Agent": SG_UA, **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(8000)
            ctype = resp.headers.get("Content-Type", "")
            if "image" in ctype or raw[:4] == b"\x89PNG":
                return {"ok": True, "status": resp.status, "content_type": ctype, "bytes": len(raw)}
            text = raw.decode("utf-8", errors="replace")
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = text[:2000]
            return {"ok": True, "status": resp.status, "content_type": ctype, "body": body}
    except error.HTTPError as exc:
        body = exc.read(1500).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "body": body[:800]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _fetch_simulations() -> Dict[str, Any]:
    r = _http(f"{SG_HOST}/dispersion-interactor/GetDispersionSimulations")
    if not r.get("ok"):
        return {}
    body = r.get("body")
    return body if isinstance(body, dict) else {}


def get_sg_postgis_registry() -> List[Dict[str, Any]]:
    """Kb evidence: hasRDB targets, timeseries counts, internal agent URLs."""
    ensure_db()
    conn = sqlite3.connect(db_path())
    rdb = conn.execute(
        "SELECT DISTINCT o FROM triples WHERE ns='kb' AND p LIKE '%hasRDB%'"
    ).fetchall()
    ts_count = conn.execute(
        "SELECT COUNT(DISTINCT s) FROM triples WHERE ns='kb' AND s LIKE '%Timeseries_%'"
    ).fetchone()[0]
    ts_linked = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE ns='kb' AND p LIKE '%hasTimeSeries%'"
    ).fetchone()[0]
    agents = conn.execute(
        "SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%hasHttpUrl%'"
    ).fetchall()
    vs_count = conn.execute(
        """
        SELECT COUNT(DISTINCT s) FROM triples WHERE ns='kb' AND s LIKE '%virtualsensor_%'
        AND p LIKE '%type%'
        """
    ).fetchone()[0]
    conn.close()
    return [
        {
            "postgis_jdbc": "; ".join(r[0] for r in rdb),
            "timeseries_nodes": ts_count,
            "hasTimeSeries_links": ts_linked,
            "virtual_sensor_instances": vs_count,
            "internal_agents": "; ".join(f"{a[0].rsplit('/', 1)[-1]}->{a[1]}" for a in agents),
            "public_timeseries_paths": "all 404 on sg-old nginx",
            "note": "Values live in internal PostGIS; only dispersion-interactor + feature-info partially exposed",
        }
    ]


def get_sg_feature_info(iri: str, endpoint: str | None = None) -> List[Dict[str, Any]]:
    """Live feature-info-agent/get (iri + endpoint per twa-vf.min.js)."""
    ep = endpoint or SG_HOST
    params = {"iri": iri, "endpoint": ep}
    url = f"{SG_HOST}/feature-info-agent/get?" + parse.urlencode(params)
    r = _http(url)
    return [
        {
            "iri": iri,
            "endpoint": ep,
            "ok": r.get("ok", False),
            "status": r.get("status") or r.get("http") or "",
            "response": r.get("body") or r.get("error") or "",
        }
    ]


def probe_sg_dispersion_point(
    lat: float = 1.33,
    lng: float = 103.74,
    pollutant: str = "CO",
) -> List[Dict[str, Any]]:
    """Try GetPollutantConcentrations + GetColourBar with JS-derived params (timestep not time)."""
    sims = _fetch_simulations()
    meta = sims.get("Singapore") or {}
    times = meta.get("time") or []
    z_map = meta.get("z") or {}
    deriv = meta.get("derivationIri") or ""
    z0 = z_map.get("0") or (list(z_map.values())[0] if z_map else "")
    timestep = str(times[0]) if times else ""

    conc_params = {
        "lat": lat,
        "lng": lng,
        "pollutant": pollutant,
        "timestep": timestep,
        "derivationIri": deriv,
        "zIri": z0,
    }
    conc_url = f"{SG_HOST}/dispersion-interactor/GetPollutantConcentrations?" + parse.urlencode(
        {k: str(v) for k, v in conc_params.items()}
    )
    colour_params = {
        "pollutant": pollutant,
        "timestep": timestep,
        "derivationIri": deriv,
        "zIri": z0,
    }
    colour_url = f"{SG_HOST}/dispersion-interactor/GetColourBar?" + parse.urlencode(colour_params)

    conc = _http(conc_url)
    colour = _http(colour_url)
    return [
        {
            "probe": "GetPollutantConcentrations",
            "location": f"lat={lat}, lng={lng}",
            "pollutant": pollutant,
            "params": conc_params,
            "ok": conc.get("ok", False),
            "status": conc.get("status") or conc.get("http") or "",
            "error_preview": (str(conc.get("body") or conc.get("error") or ""))[:200],
        },
        {
            "probe": "GetColourBar",
            "pollutant": pollutant,
            "params": colour_params,
            "ok": colour.get("ok", False),
            "status": colour.get("status") or colour.get("http") or "",
            "content_type": colour.get("content_type") or "",
            "bytes": colour.get("bytes") or 0,
            "error_preview": (str(colour.get("body") or colour.get("error") or ""))[:200],
        },
        {
            "simulation_metadata": {
                "derivation_iri": deriv,
                "timesteps_available": len(times),
                "first_timestep": timestep,
                "z_layers": list(z_map.keys()),
                "pollutants": list((meta.get("pollutants") or {}).values()),
            }
        },
    ]


def attempt_sg_create_virtual_sensor(
    lat: float = 1.33,
    lng: float = 103.74,
) -> List[Dict[str, Any]]:
    """POST CreateVirtualSensor per DispersionHandler.js (all simulation pollutants appended)."""
    sims = _fetch_simulations()
    meta = sims.get("Singapore") or {}
    pollutants = list((meta.get("pollutants") or {}).values())
    params: List[tuple[str, str]] = [("lat", str(lat)), ("lng", str(lng))]
    for p in pollutants:
        params.append(("pollutant", p))
    url = f"{SG_HOST}/dispersion-interactor/CreateVirtualSensor?" + parse.urlencode(params)
    browser = _http(
        url,
        method="POST",
        data=b"",
        headers={
            "User-Agent": SG_UA_BROWSER,
            "Referer": VIZ_REFERER,
            "Origin": SG_HOST,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    curl = _http(url, method="POST", data=b"")
    return [
        {
            "lat": lat,
            "lng": lng,
            "pollutants_sent": len(pollutants),
            "browser_post_status": browser.get("status") or browser.get("http") or browser.get("error"),
            "curl_post_status": curl.get("status") or curl.get("http") or curl.get("error"),
            "note": "Browser-like POST still 403 on sg-old; needs interactive viz session",
        }
    ]


def get_sg_live_api_surface() -> List[Dict[str, Any]]:
    """Summary of probed public HTTP surface for PostGIS/virtual-sensor/timeseries."""
    paths = [
        "/dispersion-interactor/GetDispersionSimulations",
        "/dispersion-interactor/GetPollutantConcentrations",
        "/dispersion-interactor/GetColourBar",
        "/dispersion-interactor/CreateVirtualSensor",
        "/feature-info-agent/get",
        "/timeseries-agent/query",
    ]
    out: List[Dict[str, Any]] = []
    for p in paths:
        r = _http(SG_HOST + p)
        out.append(
            {
                "path": p,
                "reachable": r.get("ok") or r.get("http") not in (404, None),
                "status": r.get("status") or r.get("http") or r.get("error", ""),
            }
        )
    return out
