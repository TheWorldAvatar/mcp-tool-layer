"""
Probe candidate SPARQL endpoints across mini_marie domains (connectivity + minimal SELECT).

Usage:
  python -m mini_marie.zaha.probe_endpoint_matrix
  python -m mini_marie.zaha.probe_endpoint_matrix --json-out data/endpoint_probe.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.zaha.twa_city.twa_city_operations import (
    CITY_ENDPOINTS,
    probe_sparql_endpoint,
    tcp_port_open,
)

# Grouped by domain — MOF/MOP endpoints are mop_mof, not zaha.
CANDIDATE_URLS: List[Dict[str, str]] = [
    # zaha: European city buildings
    {"domain": "zaha", "url": CITY_ENDPOINTS["bremen"]},
    {"domain": "zaha", "url": CITY_ENDPOINTS["kaiserslautern"]},
    # zaha: Singapore legacy hosts (174.138.23.221)
    {"domain": "zaha", "url": "http://174.138.23.221:3838/ontop/sparql/"},
    {"domain": "zaha", "url": "http://174.138.23.221:3838/ontop/ui/sparql"},
    {"domain": "zaha", "url": "http://174.138.23.221:3839/ontop/sparql/"},
    {"domain": "zaha", "url": "http://174.138.23.221:3839/ontop/ui/sparql"},
    # mop_mof: OntoMOFs
    {"domain": "mop_mof", "url": "http://68.183.227.15:3840/ontop/sparql/"},
    # marie / legacy blazegraph hosts
    {"domain": "marie", "url": "http://68.183.227.15:3838/blazegraph/namespace/ontomops_ogm/sparql"},
    {"domain": "marie", "url": "http://68.183.227.15:3838/blazegraph/namespace/OntoSynthesisTestEncoding2/sparql"},
    {"domain": "marie", "url": "http://178.128.105.213:3838/blazegraph/namespace/ontospecies/sparql"},
    {"domain": "mop_mof", "url": "http://174.138.23.221:3838/blazegraph/namespace/ontomops_ogm/sparql"},
    {"domain": "mop_mof", "url": "http://174.138.23.221:3839/blazegraph/namespace/ontomops_ogm/sparql"},
]

TCP_MATRIX = [
    ("68.183.227.15", [3838, 3839, 3840, 3841]),
    ("174.138.23.221", [3838, 3839]),
    ("178.128.105.213", [3838]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe SPARQL endpoint matrix")
    parser.add_argument("--json-out", type=Path, help="Write JSON report")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    report: Dict[str, Any] = {"tcp": [], "sparql": []}

    for host, ports in TCP_MATRIX:
        for port in ports:
            report["tcp"].append(
                {"host": host, "port": port, "open": tcp_port_open(host, port)}
            )

    working: List[str] = []
    for entry in CANDIDATE_URLS:
        url = entry["url"]
        r = probe_sparql_endpoint(url, timeout=args.timeout)
        r["domain"] = entry["domain"]
        report["sparql"].append(r)
        if r.get("ok"):
            working.append(url)

    report["working"] = working
    report["summary"] = {
        "working_count": len(working),
        "zaha_city_bremen": CITY_ENDPOINTS["bremen"] in working,
        "zaha_city_kl": CITY_ENDPOINTS["kaiserslautern"] in working,
        "mop_mof_3840": "http://68.183.227.15:3840/ontop/sparql/" in working,
        "zaha_sg_3838": any("174.138.23.221:3838" in u for u in working),
        "zaha_sg_3839": any("174.138.23.221:3839" in u for u in working),
    }

    print("TCP ports:")
    for t in report["tcp"]:
        mark = "open" if t["open"] else "closed"
        print(f"  {t['host']}:{t['port']}  {mark}")

    print("\nSPARQL endpoints:")
    for s in report["sparql"]:
        status = "OK" if s.get("ok") else "FAIL"
        err = f"  ({s['error'][:80]}...)" if s.get("error") and len(str(s["error"])) > 80 else (
            f"  ({s['error']})" if s.get("error") else ""
        )
        print(f"  [{status}] [{s.get('domain', '?')}] {s['url']}{err}")

    print(f"\nWorking ({len(working)}):")
    for u in working:
        print(f"  {u}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json_out}")

    return 0 if working else 1


if __name__ == "__main__":
    raise SystemExit(main())
