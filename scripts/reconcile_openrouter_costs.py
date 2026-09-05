#!/usr/bin/env python3
"""Reconcile and summarize the append-only OpenRouter actual-cost journal."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.llm_call_telemetry import (  # noqa: E402
    journal_path,
    reconcile_pending_costs,
    summarize_costs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", help="JSONL journal (defaults to runtime journal)")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print the deduplicated aggregate after reconciliation",
    )
    args = parser.parse_args()
    target = args.journal or str(journal_path())
    result = {"journal": target, **reconcile_pending_costs(target)}
    if args.summary:
        result["summary"] = summarize_costs(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
