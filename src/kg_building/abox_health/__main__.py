"""CLI: python -m src.kg_building.abox_health --ttl graph.ttl --ontology ontosynthesis"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import (
    check_ttl_files,
    discover_run_ttl,
    resolve_ontology_tboxes,
    tboxes_without_om2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check published Pipeline or OntoLogX A-Box Turtle for orphans "
            "and T-Box/SHACL/OWL health. No LLM."
        )
    )
    parser.add_argument("--ttl", action="append", dest="ttl_paths", type=Path)
    parser.add_argument("--run-dir", type=Path, help="Scan ontosynthesis_output (and extension) TTL.")
    parser.add_argument("--tbox", action="append", dest="tbox_paths", type=Path)
    parser.add_argument(
        "--ontology",
        help="Load T-Box paths (and default SHACL) from configs/domains/<name>.json.",
    )
    parser.add_argument("--shacl", type=Path)
    parser.add_argument("--root", action="append", dest="roots")
    parser.add_argument("--skip-shacl", action="store_true")
    parser.add_argument("--skip-owlrl", action="store_true")
    parser.add_argument("--skip-owl-dl", action="store_true")
    parser.add_argument(
        "--no-om2-tbox",
        action="store_true",
        help="Drop om2.ttl from T-Box paths (use with HermiT / --skip-shacl).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ttl_paths = [Path(item) for item in (args.ttl_paths or [])]
    if args.run_dir is not None:
        ttl_paths.extend(discover_run_ttl(args.run_dir))
    if not ttl_paths:
        print("No Turtle files given. Pass --ttl or --run-dir.", file=sys.stderr)
        return 2

    tbox_paths = [Path(item) for item in (args.tbox_paths or [])]
    shacl_path = Path(args.shacl) if args.shacl else None
    if args.ontology:
        ontology_tboxes, ontology_shacl = resolve_ontology_tboxes(args.ontology)
        if not tbox_paths:
            tbox_paths = ontology_tboxes
        if shacl_path is None:
            shacl_path = ontology_shacl
    if not tbox_paths:
        print("Pass --tbox or --ontology so the checker can load a T-Box.", file=sys.stderr)
        return 2
    if args.no_om2_tbox:
        tbox_paths = tboxes_without_om2(tbox_paths)

    reports = check_ttl_files(
        ttl_paths,
        tbox_paths=tbox_paths,
        shacl_path=shacl_path,
        roots=args.roots,
        run_shacl=not args.skip_shacl,
        run_owlrl=not args.skip_owlrl,
        run_owl_dl=not args.skip_owl_dl,
    )
    payload = [item.to_dict() for item in reports]
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for report in reports:
            status = "OK" if report.ok else "FAIL"
            print(f"[{status}] {report.source or '(graph)'} instances={report.instance_count} roots={len(report.roots)}")
            for finding in report.findings:
                iris = f"  {', '.join(finding.iris)}" if finding.iris else ""
                print(f"  - {finding.severity} {finding.code}: {finding.message}{iris}")
    return 0 if all(item.ok for item in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
