"""Summarize request, token, latency, and actual-cost telemetry from a JSONL journal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def summarize(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "completed":
            rows.append(row)
    billable = [row for row in rows if row.get("billable")]
    return {
        "completed_requests": len(rows),
        "billable_requests": len(billable),
        "failed_requests": sum(bool(row.get("error")) for row in rows),
        "input_tokens": sum(
            int((row.get("token_usage") or {}).get("input_tokens") or 0)
            for row in billable
        ),
        "output_tokens": sum(
            int((row.get("token_usage") or {}).get("output_tokens") or 0)
            for row in billable
        ),
        "total_tokens": sum(
            int((row.get("token_usage") or {}).get("total_tokens") or 0)
            for row in billable
        ),
        "actual_cost_usd": round(
            sum(float(row.get("actual_cost_usd") or 0.0) for row in billable),
            8,
        ),
        "summed_provider_latency_seconds": round(
            sum(float(row.get("elapsed") or 0.0) for row in rows),
            3,
        ),
        "models": sorted({str(row.get("model") or "unknown") for row in rows}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.journal)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
