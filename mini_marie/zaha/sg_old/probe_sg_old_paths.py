"""Extra sg-old path sweep for PostGIS/timeseries public exposure."""
from __future__ import annotations

import json
from urllib import parse, request

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"
SHIP_TS = "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"

PATHS = [
    "/postgis/",
    "/postgis/sparql",
    "/ontotimeseries/",
    "/ontotimeseries/sparql",
    "/ontodispersion/sparql",
    "/stack-config/",
    "/stack-config/ontop",
    "/ontop/",
    "/ontop/obda",
    "/ontop/ontology",
    "/ontop/ui/",
    "/metadata/",
    "/metadata/timeseries/" + SHIP_TS,
    "/visualisation/metadata/timeseries/" + SHIP_TS,
    "/JPSAccessAgent/",
    "/DerivationAgent/",
    "/MetainformationAgent/",
    "/CReDoAccessAgent/",
    "/ShipDataAgent/",
    "/ShipDataAgent/query",
    "/ShipDataAgent/ship/563071320",
    "/VirtualSensorAgent/",
    "/VirtualSensorAgent/query",
    "/AermodAgent/",
    "/EmissionsAgent/",
    "/stack-data-uploader/",
    "/stack-data-uploader/timeseries/" + SHIP_TS,
    "/geoserver/",
    "/geoserver/wfs",
    "/geoserver/ows",
    "/adminer/",
    "/pgadmin/",
    "/api/timeseries/" + SHIP_TS,
    "/api/v1/timeseries/" + SHIP_TS,
]


def get(path: str) -> dict:
    url = HOST + path
    req = request.Request(url, headers={"User-Agent": UA})
    try:
        with request.urlopen(req, timeout=15) as r:
            body = r.read(400).decode("utf-8", errors="replace")
            return {"status": r.status, "ctype": r.headers.get("Content-Type", ""), "preview": body[:80]}
    except Exception as e:
        code = getattr(e, "code", None)
        return {"http": code, "error": str(e)[:60]}


def main() -> None:
    results = {p: get(p) for p in PATHS}
    ok = {p: r for p, r in results.items() if r.get("status") and r["status"] < 400}
    print("=== Non-404/OK paths ===")
    for p, r in sorted(ok.items()):
        print(f"  {p}: {r}")
    print(f"\nTotal paths: {len(PATHS)}, non-error: {len(ok)}")
    from mini_marie.cache_paths import mini_marie_cache_root
    out = mini_marie_cache_root() / "sg_old" / "probe_sg_old_paths.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
