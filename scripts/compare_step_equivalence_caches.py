"""Compare decisions in two step-equivalence cache directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in path.glob("*.json"):
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
            judgement = payload["judgement"]
            key = (
                str(payload["field_kind"]),
                str(payload["field_name"]),
                str(payload["ground_truth_value"]),
                str(payload["prediction_value"]),
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue
        rows[key] = judgement
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()
    left = _load(args.left)
    right = _load(args.right)
    common = set(left) & set(right)
    disagreements = [
        {
            "field_kind": key[0],
            "field_name": key[1],
            "ground_truth_value": key[2],
            "prediction_value": key[3],
            "left_equivalent": bool(left[key].get("equivalent")),
            "right_equivalent": bool(right[key].get("equivalent")),
        }
        for key in sorted(common)
        if bool(left[key].get("equivalent")) != bool(right[key].get("equivalent"))
    ]
    print(
        json.dumps(
            {
                "left_pairs": len(left),
                "right_pairs": len(right),
                "common_pairs": len(common),
                "left_positive": sum(
                    bool(row.get("equivalent")) for row in left.values()
                ),
                "right_positive": sum(
                    bool(row.get("equivalent")) for row in right.values()
                ),
                "decision_disagreements": len(disagreements),
                "disagreements": disagreements,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
