"""
Fast probe for Zaha / Singapore stack endpoints.

Uses minimal SPARQL (ASK / SELECT 1) and short timeouts so results are quick and clear.

Usage:
  python -m mini_marie.zaha.probe_zaha_stack
  python -m mini_marie.zaha.probe_zaha_stack --timeout 4
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib import error, parse, request

# Minimal queries — avoid scanning triple store
ASK_QUERY = "ASK { }"
SELECT_ONE = "SELECT (1 AS ?x) WHERE {}"

# User-provided stack (3839) + common alternates
ENDPOINTS: List[Tuple[str, str]] = [
    ("ontop_ui", "http://174.138.23.221:3839/ontop/ui/sparql"),
    ("ontop_api", "http://174.138.23.221:3839/ontop/sparql/"),
    ("ontop_internal", "http://sg-ontop:8080/sparql"),
    ("dispersion_kb", "http://174.138.23.221:3839/blazegraph/namespace/kb/sparql"),
    ("plot", "http://174.138.23.221:3839/blazegraph/namespace/plot/sparql"),
    ("company", "http://174.138.23.221:3839/blazegraph/namespace/company/sparql"),
    ("carpark", "http://174.138.23.221:3839/blazegraph/namespace/carpark/sparql"),
    ("carpark_internal", "http://sg-blazegraph:8080/blazegraph/namespace/carpark/sparql"),
]

HTTP_GET_URLS: List[Tuple[str, str]] = [
    ("feature_info_agent", "http://174.138.23.221:3839/feature-info-agent/get"),
    ("pollutant_concentration", "http://174.138.23.221:3839/dispersion-interactor/GetPollutantConcentrations"),
]

TCP_HOSTS_PORTS = [
    ("174.138.23.221", 3838),
    ("174.138.23.221", 3839),
]


def tcp_open(host: str, port: int, timeout: float) -> Dict[str, Any]:
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


def _http(
    url: str,
    *,
    method: str,
    data: bytes | None,
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(256)
            return {
                "ok": True,
                "status": resp.status,
                "ms": round((time.perf_counter() - t0) * 1000),
                "content_type": resp.headers.get("Content-Type"),
                "bytes": len(body),
            }
    except error.HTTPError as exc:
        return {
            "ok": False,
            "http_status": exc.code,
            "ms": round((time.perf_counter() - t0) * 1000),
            "error": str(exc.reason),
        }
    except Exception as exc:
        return {
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def sparql_post_query(url: str, query: str, timeout: float, content_type: str) -> Dict[str, Any]:
    return _http(
        url,
        method="POST",
        data=query.encode("utf-8"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": content_type,
            "User-Agent": "mini_marie-fast-probe",
        },
        timeout=timeout,
    )


def sparql_get_query(url: str, query: str, timeout: float) -> Dict[str, Any]:
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}{parse.urlencode({'query': query})}"
    return _http(
        full,
        method="GET",
        data=None,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "mini_marie-fast-probe",
        },
        timeout=timeout,
    )


def _host_port_from_url(url: str) -> Tuple[str | None, int | None]:
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        return p.hostname, p.port
    except Exception:
        return None, None


def probe_sparql_fast(
    url: str,
    timeout: float,
    *,
    tcp_cache: Dict[str, Dict[str, Any]],
    tcp_timeout: float,
) -> Dict[str, Any]:
    """Minimal ASK/SELECT(1); stop on first success. Skip if TCP already closed."""
    out: Dict[str, Any] = {"url": url, "methods": {}}
    host, port = _host_port_from_url(url)
    if host and port:
        key = f"{host}:{port}"
        if key not in tcp_cache:
            tcp_cache[key] = tcp_open(host, port, tcp_timeout)
        out["tcp"] = tcp_cache[key]
        if not tcp_cache[key].get("open"):
            out["ok"] = False
            err = (tcp_cache[key].get("error") or "").lower()
            if "getaddrinfo" in err or "name or service" in err or "11001" in err:
                out["skipped"] = "dns_unresolved"
            else:
                out["skipped"] = "tcp_closed"
            return out

    attempts = [
        ("post_ask", lambda: sparql_post_query(url, ASK_QUERY, timeout, "application/sparql-query")),
        ("post_form_ask", lambda: sparql_post_query(url, ASK_QUERY, timeout, "application/x-www-form-urlencoded")),
        ("get_ask", lambda: sparql_get_query(url, ASK_QUERY, timeout)),
        ("post_select1", lambda: sparql_post_query(url, SELECT_ONE, timeout, "application/sparql-query")),
    ]
    for name, fn in attempts:
        r = fn()
        out["methods"][name] = r
        if r.get("ok"):
            out["ok"] = True
            out["winning"] = name
            return out
    out["ok"] = False
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast Zaha stack SPARQL probe")
    parser.add_argument("--timeout", type=float, default=4.0, help="Per-request timeout (seconds)")
    parser.add_argument("--tcp-timeout", type=float, default=2.0)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("data/mini_marie_cache/zaha_stack_fast_probe.json"),
    )
    args = parser.parse_args()

    report: Dict[str, Any] = {"timeout_s": args.timeout, "tcp": {}, "sparql": {}, "http_get": {}}

    print(f"TCP probe ({args.tcp_timeout}s):")
    for host, port in TCP_HOSTS_PORTS:
        key = f"{host}:{port}"
        r = tcp_open(host, port, args.tcp_timeout)
        report["tcp"][key] = r
        state = "OPEN" if r["open"] else "CLOSED"
        extra = f" ({r['error']})" if r.get("error") else ""
        print(f"  {key}  {state}  {r['ms']}ms{extra}")

    tcp_cache: Dict[str, Dict[str, Any]] = report["tcp"]

    print(f"\nSPARQL fast probe (ASK then SELECT 1, {args.timeout}s per attempt):")
    for name, url in ENDPOINTS:
        r = probe_sparql_fast(url, args.timeout, tcp_cache=tcp_cache, tcp_timeout=args.tcp_timeout)
        report["sparql"][name] = r
        if r.get("ok"):
            win = r["methods"][r["winning"]]
            print(f"  [OK] {name}  {r['winning']}  {win['ms']}ms")
        elif r.get("skipped"):
            print(f"  [SKIP] {name}  {r['skipped']} (no SPARQL wait)")
        else:
            m = r.get("methods") or {}
            err = next(
                (v.get("error") or v.get("http_status") for v in m.values() if not v.get("ok")),
                "?",
            )
            print(f"  [FAIL] {name}  {err}")

    print(f"\nHTTP GET ({args.timeout}s):")
    for name, url in HTTP_GET_URLS:
        host, port = _host_port_from_url(url)
        if host and port:
            key = f"{host}:{port}"
            if key in tcp_cache and not tcp_cache[key].get("open"):
                report["http_get"][name] = {"ok": False, "skipped": "tcp_closed"}
                print(f"  [SKIP] {name}  tcp_closed")
                continue
        r = _http(url, method="GET", data=None, headers={"Accept": "*/*"}, timeout=args.timeout)
        report["http_get"][name] = r
        if r.get("ok"):
            print(f"  [OK] {name}  status={r['status']}  {r['ms']}ms")
        else:
            print(f"  [FAIL] {name}  {r.get('error') or r.get('http_status')}")

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.json_out}")
    ok_count = sum(1 for r in report["sparql"].values() if r.get("ok"))
    return 0 if ok_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
