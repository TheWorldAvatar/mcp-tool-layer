"""Probe feature-info-agent with JS-derived iri+endpoint params."""
from __future__ import annotations

import json
from urllib import error, parse, request

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"

IRIS = [
    ("ship", "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320"),
    ("speed_measure", "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure"),
    ("ship_ts", "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"),
    ("co_ts", "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_ee9a9039-0da0-4caa-92b7-57f6c207a05c"),
    ("virtual_sensor", "https://www.theworldavatar.com/kg/ontoems/virtualsensor_0b8e0f0a-5f0d-4e0a-9f0a-0b8e0f0a5f0d"),
    ("derivation", "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"),
]

ENDPOINTS = [
    HOST,
    HOST + "/blazegraph/namespace/kb/sparql",
    "https://sg-old.theworldavatar.io/blazegraph/namespace/kb/sparql",
]


def get(params: dict) -> dict:
    url = HOST + "/feature-info-agent/get?" + parse.urlencode(params)
    req = request.Request(url, headers={"User-Agent": UA})
    try:
        with request.urlopen(req, timeout=30) as r:
            body = r.read(6000).decode("utf-8", errors="replace")
            return {"status": r.status, "body": body[:2500]}
    except error.HTTPError as e:
        return {"http": e.code, "body": e.read(2000).decode("utf-8", errors="replace")[:800]}


def main() -> None:
    # First get a real virtual sensor IRI from kb if possible
    import sqlite3
    from mini_marie.zaha.sg_old.local_store import db_path, ensure_db
    ensure_db()
    conn = sqlite3.connect(db_path())
    vs = conn.execute(
        "SELECT s FROM triples WHERE ns='kb' AND s LIKE '%virtualsensor_%' AND p LIKE '%type%' LIMIT 1"
    ).fetchone()
    conn.close()
    if vs:
        IRIS.append(("vs_real", vs[0]))

    for name, iri in IRIS:
        for ep in ENDPOINTS[:2]:
            r = get({"iri": iri, "endpoint": ep})
            tag = r.get("status") or r.get("http")
            preview = (r.get("body") or "")[:120].replace("\n", " ")
            print(f"{name} ep={ep[-20:]}: {tag} {preview}")


if __name__ == "__main__":
    main()
