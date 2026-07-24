"""
Probe TWA city data sources: TCP ports, SPARQL endpoints, schema discovery.

Usage:
  python -m mini_marie.zaha.twa_city.probe
  python -m mini_marie.zaha.twa_city.probe --endpoint http://HOST:PORT/ontop/sparql/
  python -m mini_marie.zaha.twa_city.probe --json-out mini_marie/zaha/twa_city/probe_results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mini_marie.zaha.twa_city.twa_city_operations import (
    DEFAULT_ENDPOINT_CANDIDATES,
    STACK_INTERNAL_HINTS,
    format_results_as_tsv,
    probe_endpoints,
    run_discovery,
)

DEFAULT_HOSTS = ["68.183.227.15", "127.0.0.1", "localhost"]
DEFAULT_PORTS = [3840, 3841, 3842, 3843, 3844, 3845, 8080, 8081, 8082, 8083]


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe TWA city TWA / stack endpoints")
    parser.add_argument(
        "--endpoint",
        help="Skip port scan; run discovery on this SPARQL endpoint URL",
    )
    parser.add_argument(
        "--hosts",
        nargs="*",
        default=DEFAULT_HOSTS,
        help="Hosts for TCP + HTTP probe (default: remote + localhost)",
    )
    parser.add_argument(
        "--ports",
        nargs="*",
        type=int,
        default=DEFAULT_PORTS,
        help="Ports to scan when --endpoint is not set",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Run schema discovery queries on the working endpoint",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Write full probe report as JSON",
    )
    args = parser.parse_args()

    report: dict = {"stack_internal_hints": STACK_INTERNAL_HINTS, "endpoint_probes": [], "discovery": None}

    if args.endpoint:
        working = args.endpoint
        report["endpoint_probes"] = [{"url": working, "ok": True, "note": "user-supplied"}]
    else:
        report["endpoint_probes"] = probe_endpoints(
            candidates=DEFAULT_ENDPOINT_CANDIDATES,
            hosts=args.hosts,
            ports=args.ports,
        )
        ok_urls = [p["url"] for p in report["endpoint_probes"] if p.get("ok")]
        if not ok_urls:
            print("No working SPARQL endpoints found.", file=sys.stderr)
            print("Stack-internal names (Docker network only):", file=sys.stderr)
            for k, v in STACK_INTERNAL_HINTS.items():
                print(f"  {k}: {v}", file=sys.stderr)
            if args.json_out:
                args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return 1
        working = ok_urls[0]
        if len(ok_urls) > 1:
            print(f"Multiple endpoints OK; using first: {working}", file=sys.stderr)
            report["all_ok_endpoints"] = ok_urls

    report["working_endpoint"] = working
    print(f"Working endpoint: {working}")

    run_schema_discovery = args.discover or bool(args.endpoint)
    if not run_schema_discovery:
        print("Endpoint probe only. Re-run with --discover to introspect schema.")
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Wrote {args.json_out}")
        return 0

    print(f"\nDiscovery on: {working}\n")
    discovery = run_discovery(working)
    report["discovery"] = discovery
    report["discovery_endpoint"] = working

    for name, rows in discovery.items():
        if isinstance(rows, dict) and "error" in rows:
            print(f"=== {name} (failed) ===")
            print(rows["error"])
        else:
            print(f"=== {name} ({len(rows)} rows) ===")
            text = format_results_as_tsv(rows)
            try:
                print(text)
            except UnicodeEncodeError:
                sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        print()

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
