#!/usr/bin/env python3
"""Revise Characterisation GT so it only covers Steps Synthesis chemical outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.pipelines.utils.characterisation_gt_scope import revise_characterisation_gt_tree


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Drop Characterisation GT records that are not chemical outputs "
            "of Steps Synthesis[] entries."
        )
    )
    parser.add_argument(
        "--characterisation-dir",
        default="full_ground_truth/characterisation",
    )
    parser.add_argument("--steps-dir", default="full_ground_truth/steps")
    parser.add_argument(
        "--report",
        default="full_ground_truth/_characterisation_synthesis_scope_revision.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report without writing Characterisation files.",
    )
    args = parser.parse_args()

    characterisation_dir = Path(args.characterisation_dir)
    steps_dir = Path(args.steps_dir)
    if args.dry_run:
        from src.pipelines.utils.characterisation_gt_scope import (
            filter_characterisation_document,
        )

        report = {
            "rule": "Keep Characterisation only when it matches a Steps Synthesis product.",
            "dry_run": True,
            "files": {},
            "kept": 0,
            "removed": 0,
        }
        for char_path in sorted(characterisation_dir.glob("*.json")):
            if char_path.name.startswith("_"):
                continue
            steps_path = steps_dir / char_path.name
            char_obj = json.loads(char_path.read_text(encoding="utf-8"))
            if not steps_path.is_file():
                report["files"][char_path.name] = {"status": "missing_steps"}
                continue
            steps_obj = json.loads(steps_path.read_text(encoding="utf-8"))
            filtered, removed = filter_characterisation_document(char_obj, steps_obj)
            kept = sum(
                len((device or {}).get("Characterisation", []) or [])
                for device in (filtered.get("Devices") or [])
            )
            report["files"][char_path.name] = {
                "status": "would_revise" if removed else "unchanged",
                "kept": kept,
                "removed": len(removed),
                "dropped": removed,
            }
            report["kept"] += kept
            report["removed"] += len(removed)
    else:
        report = revise_characterisation_gt_tree(characterisation_dir, steps_dir)

    report_path = Path(args.report)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"kept={report['kept']} removed={report['removed']} report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
