"""Probe live dispersion/ship value APIs with JS-derived parameter names."""
from __future__ import annotations

import json
from urllib import error, parse, request

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"
DERIV = "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"
JURONG = (1.33, 103.74)


def get(url: str) -> dict:
    req = request.Request(url, headers={"User-Agent": UA})
    try:
        with request.urlopen(req, timeout=30) as r:
            raw = r.read(8000)
            ctype = r.headers.get("Content-Type", "")
            if "image" in ctype or raw[:4] == b"\x89PNG":
                return {"status": r.status, "ctype": ctype, "bytes": len(raw), "preview": "binary_image"}
            return {"status": r.status, "ctype": ctype, "body": raw.decode("utf-8", errors="replace")[:2000]}
    except error.HTTPError as e:
        return {"http": e.code, "body": e.read(1500).decode("utf-8", errors="replace")[:800]}


def main() -> None:
    sim = get(HOST + "/dispersion-interactor/GetDispersionSimulations")
    print("=== GetDispersionSimulations ===")
    meta = {}
    if "body" in sim:
        payload = json.loads(sim["body"])
        meta = payload.get("Singapore") or {}
        print(json.dumps({k: (v if k != "time" else v[:3]) for k, v in meta.items()}, indent=2)[:2500])

    times = meta.get("time") or []
    z = meta.get("z") or {}
    pollutants = meta.get("pollutants") or {}
    z0 = list(z.values())[0] if z else ""
    timestep = str(times[0]) if times else ""

    print("\n=== GetColourBar (timestep not time) ===")
    for pollutant in ["CO", list(pollutants.values())[0] if pollutants else "CO"]:
        params = {
            "pollutant": pollutant,
            "timestep": timestep,
            "derivationIri": meta.get("derivationIri") or DERIV,
            "zIri": z0,
        }
        url = HOST + "/dispersion-interactor/GetColourBar?" + parse.urlencode(params)
        r = get(url)
        print(f"  {pollutant}: {r.get('status') or r.get('http')} {r.get('ctype') or r.get('preview') or r.get('body','')[:80]}")

    print("\n=== GetPollutantConcentrations variants ===")
    base = HOST + "/dispersion-interactor/GetPollutantConcentrations"
    attempts = [
        {"lat": JURONG[0], "lng": JURONG[1], "pollutant": "CO"},
        {"lat": JURONG[0], "lng": JURONG[1], "pollutant": "CO", "timestep": timestep},
        {"lat": JURONG[0], "lng": JURONG[1], "pollutant": "CO", "timestep": timestep, "derivationIri": meta.get("derivationIri") or DERIV},
        {"lat": JURONG[0], "lng": JURONG[1], "pollutant": "CO", "timestep": timestep, "derivationIri": meta.get("derivationIri") or DERIV, "zIri": z0},
        {"latitude": JURONG[0], "longitude": JURONG[1], "pollutant": "CO", "timestep": timestep, "derivationIri": meta.get("derivationIri") or DERIV, "zIri": z0},
    ]
    for i, p in enumerate(attempts):
        r = get(base + "?" + parse.urlencode({k: str(v) for k, v in p.items()}))
        print(f"  [{i}] {r.get('status') or r.get('http')}: {(r.get('body') or '')[:200]}")

    print("\n=== Agent proxy paths ===")
    for path in [
        "/sg-virtual-sensor-agent/VirtualSensorAgent/",
        "/sg-ship-data-agent/ShipDataAgent/",
        "/virtual-sensor-agent/",
        "/ship-data-agent/",
    ]:
        r = get(HOST + path)
        print(f"  {path}: {r.get('status') or r.get('http')}")


if __name__ == "__main__":
    main()
