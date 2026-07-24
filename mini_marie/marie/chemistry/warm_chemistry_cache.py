"""Pre-cache full-tier chemistry competency atomics (fragile endpoint safe)."""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from mini_marie.marie.chemistry.atomic_warm_manifest import (
    full_space_warm_specs,
    workflow_driven_warm_specs,
)
from mini_marie.marie.chemistry.chemistry_cache import ChemistryCache, warm_full_calls, warm_probe_calls
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS, WARM_NAMESPACES
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.warm_option_catalog import (
    coverage_report,
    list_dimensions,
    slice_specs,
)
from mini_marie.warm_manifest import specs_missing_full_tier

UA = "curl/8.0"
CTX = ssl.create_default_context()


def namespace_health_ok(namespace: str, timeout: int = 45) -> Dict[str, Any]:
    """Lightweight ASK before warming a namespace batch."""
    url = endpoint(namespace) + "?" + urllib.parse.urlencode({"query": "ASK { ?s ?p ?o }"})
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"}
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = '"boolean": true' in body.lower() or "true" in body.lower()
            return {
                "namespace": namespace,
                "ok": ok,
                "ms": round((time.perf_counter() - t0) * 1000),
            }
    except Exception as exc:
        return {
            "namespace": namespace,
            "ok": False,
            "error": str(exc)[:200],
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def filter_specs_by_namespace(
    specs: List[Dict[str, Any]], namespaces: List[str] | None
) -> List[Dict[str, Any]]:
    if not namespaces:
        return specs
    allowed = set(namespaces)
    return [s for s in specs if s.get("args", {}).get("namespace") in allowed]


def filter_specs_by_tool(specs: List[Dict[str, Any]], tool: str | None) -> List[Dict[str, Any]]:
    if not tool:
        return specs
    return [s for s in specs if s.get("tool") == tool]


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm chemistry competency SQLite cache")
    parser.add_argument(
        "--comprehensive",
        action="store_true",
        help="Warm MQ/probe-driven specs (~23 entries)",
    )
    parser.add_argument(
        "--full-space",
        action="store_true",
        help="Warm every catalog option variant (see warm_option_catalog.json)",
    )
    parser.add_argument(
        "--dimension",
        help="Limit full-space warm to one catalog dimension id",
    )
    parser.add_argument("--list-dimensions", action="store_true", help="List catalog dimensions")
    parser.add_argument("--coverage", action="store_true", help="Report full-space cache coverage")
    parser.add_argument("--probe-only", action="store_true", help="Online probe tier only (LIMIT 5)")
    parser.add_argument("--namespace", action="append", help="Limit to namespace(s)")
    parser.add_argument("--tool", help="Single tool name")
    parser.add_argument("--args-json", default="{}", help='JSON args (must include "namespace")')
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Skip specs already in full-tier cache (warm only B,C,D when A exists)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=0,
        help="Warm at most N specs this run (0 = all missing)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N specs after missing-only filter (resume batches)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=WARM_DELAY_SECONDS,
        help="Seconds between warm calls (default from limits.py)",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip ASK health check before warming each namespace",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bar",
    )
    args = parser.parse_args()

    if args.list_dimensions:
        print(json.dumps(list_dimensions(), indent=2))
        return

    if args.coverage:
        cache = ChemistryCache()
        try:
            report = coverage_report(
                has_full=cache.has_full,
                namespace=(args.namespace or [None])[0] if args.namespace else None,
                tool=args.tool,
                dimension_id=args.dimension,
            )
            print(json.dumps(report, indent=2))
        finally:
            cache.close()
        return

    if args.full_space:
        ns_filter = args.namespace[0] if args.namespace and len(args.namespace) == 1 else None
        specs = full_space_warm_specs(
            namespace=ns_filter,
            tool=args.tool,
            dimension_id=args.dimension,
        )
        label = "full_space"
        if args.dimension:
            label = f"{label}:{args.dimension}"
    elif args.comprehensive:
        specs = workflow_driven_warm_specs()
        label = "workflow_driven"
    elif args.tool:
        tool_args = json.loads(args.args_json)
        if "namespace" not in tool_args:
            raise SystemExit('--args-json must include "namespace"')
        specs = [{"tool": args.tool, "args": tool_args}]
        label = "single"
    else:
        raise SystemExit(
            "Provide --full-space, --comprehensive, --coverage, --list-dimensions, "
            "or --tool with --args-json"
        )

    specs = filter_specs_by_namespace(specs, args.namespace)
    specs = filter_specs_by_tool(specs, args.tool if not args.full_space else None)

    if args.missing_only and not args.force:
        cache = ChemistryCache()
        try:
            before = len(specs)
            specs = specs_missing_full_tier(specs, has_full=cache.has_full)
            label = f"{label}_missing({len(specs)}/{before})"
        finally:
            cache.close()

    if args.batch or args.offset:
        total_before_slice = len(specs)
        specs = slice_specs(specs, offset=args.offset, batch=args.batch or None)
        if args.batch:
            label = f"{label}_batch({len(specs)}/{total_before_slice}@offset{args.offset})"

    if not args.skip_health_check and not args.probe_only and specs:
        ns_seen: set[str] = set()
        for spec in specs:
            ns = str(spec.get("args", {}).get("namespace", ""))
            if not ns or ns in ns_seen or ns not in WARM_NAMESPACES:
                continue
            ns_seen.add(ns)
            health = namespace_health_ok(ns)
            print(f"health {ns}: {health}", flush=True)
            if not health.get("ok"):
                raise SystemExit(f"Namespace {ns} failed health check; aborting warm")

    print(f"{'Probing' if args.probe_only else 'Warming'} {len(specs)} spec(s) [{label}]...", flush=True)
    if args.probe_only:
        summary = warm_probe_calls(specs, force=args.force)
    else:
        summary = warm_full_calls(
            specs,
            force=args.force,
            delay_seconds=args.delay,
            show_progress=not args.no_progress,
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
