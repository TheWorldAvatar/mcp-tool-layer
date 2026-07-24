"""
Discover option enumerations for full-space chemistry cache warm.

Runs one careful SPARQL query per discovery target (with sleep), writes
warm_option_catalog.discovered.json for merge into full-space warm specs.
"""

from __future__ import annotations

import argparse
import json
import time

from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS
from mini_marie.marie.chemistry.query_builder import _execute, _prefix_block
from mini_marie.marie.chemistry.warm_option_catalog import (
    DISCOVERED_PATH,
    load_discovered_options,
    save_discovered_options,
)


def discover_framework_codes() -> list[str]:
    q = (
        _prefix_block("ontozeolite")
        + """
SELECT DISTINCT ?code WHERE {
  ?fw a oz:ZeoliteFramework .
  ?fw oz:hasFrameworkCode ?code .
}
ORDER BY ?code
"""
    )
    rows = _execute("ontozeolite", q)
    codes: list[str] = []
    for row in rows:
        code = str(row.get("code", "")).strip().upper()
        if code:
            codes.append(code)
    return sorted(set(codes))


def discover_compchem_species_labels() -> list[str]:
    q = (
        _prefix_block("ontocompchem")
        + """
SELECT DISTINCT ?label WHERE {
  ?s a occ:Species .
  ?s rdfs:label ?label .
}
ORDER BY ?label
"""
    )
    rows = _execute("ontocompchem", q)
    labels: list[str] = []
    for row in rows:
        label = str(row.get("label", "")).strip()
        if label:
            labels.append(label)
    return sorted(set(labels))


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover chemistry warm option enumerations")
    parser.add_argument(
        "--target",
        action="append",
        choices=["framework_codes", "compchem_species", "all"],
        help="Discovery target(s); default all when omitted",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=WARM_DELAY_SECONDS,
        help="Seconds between discovery queries",
    )
    args = parser.parse_args()

    targets = set(args.target or ["all"])
    if "all" in targets:
        targets = {"framework_codes", "compchem_species"}

    options = load_discovered_options()
    report: dict[str, object] = {"discovered": {}, "path": str(DISCOVERED_PATH)}

    if "framework_codes" in targets:
        print("Discovering ontozeolite framework codes ...", flush=True)
        codes = discover_framework_codes()
        options["framework_codes"] = codes
        report["discovered"]["framework_codes"] = len(codes)
        print(f"  -> {len(codes)} codes", flush=True)
        if "compchem_species" in targets:
            time.sleep(max(0.0, args.delay))

    if "compchem_species" in targets:
        print("Discovering ontocompchem species labels ...", flush=True)
        labels = discover_compchem_species_labels()
        options["compchem_species"] = labels
        report["discovered"]["compchem_species"] = len(labels)
        print(f"  -> {len(labels)} labels", flush=True)

    path = save_discovered_options(options)
    report["saved"] = str(path)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
