"""Compare two 30-paper Steps `_overall.md` reports."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean


def load_rows(path: Path) -> dict[str, dict[str, float | int]]:
    rows: dict[str, dict[str, float | int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 8 or len(cells[1]) != 8:
            continue
        try:
            rows[cells[1]] = {
                "tp": int(cells[2]),
                "fp": int(cells[3]),
                "fn": int(cells[4]),
                "precision": float(cells[5]),
                "recall": float(cells[6]),
                "f1": float(cells[7]),
            }
        except ValueError:
            continue
    return rows


def aggregate(rows: dict[str, dict[str, float | int]]) -> dict[str, float | int]:
    tp = sum(int(row["tp"]) for row in rows.values())
    fp = sum(int(row["fp"]) for row in rows.values())
    fn = sum(int(row["fn"]) for row in rows.values())
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall),
        "macro_f1": mean(float(row["f1"]) for row in rows.values()),
    }


def permutation_p(differences: list[float], samples: int = 200000) -> float:
    observed = abs(mean(differences))
    rng = random.Random(20260826)
    extreme = 0
    for _ in range(samples):
        value = abs(
            sum(diff if rng.random() < 0.5 else -diff for diff in differences)
            / len(differences)
        )
        extreme += value >= observed
    return (extreme + 1) / (samples + 1)


def bootstrap_ci(differences: list[float], samples: int = 20000) -> list[float]:
    rng = random.Random(20260826)
    estimates = sorted(
        mean(rng.choice(differences) for _ in differences) for _ in range(samples)
    )
    return [
        estimates[int(0.025 * samples)],
        estimates[int(0.975 * samples)],
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", type=Path)
    parser.add_argument("ontologx", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pipeline = load_rows(args.pipeline)
    ontologx = load_rows(args.ontologx)
    hashes = sorted(set(pipeline) & set(ontologx))
    if len(hashes) != 30:
        raise ValueError(f"Expected 30 common papers, found {len(hashes)}")
    per_paper = []
    differences = []
    for hash_value in hashes:
        difference = float(ontologx[hash_value]["f1"]) - float(
            pipeline[hash_value]["f1"]
        )
        differences.append(difference)
        per_paper.append(
            {
                "hash": hash_value,
                "pipeline_f1": pipeline[hash_value]["f1"],
                "ontologx_f1": ontologx[hash_value]["f1"],
                "difference": round(difference, 6),
            }
        )
    result = {
        "schema_version": "pipeline-ontologx-comparison.v1",
        "pipeline": aggregate(pipeline),
        "ontologx": aggregate(ontologx),
        "ontologx_minus_pipeline_micro_f1": aggregate(ontologx)["f1"]
        - aggregate(pipeline)["f1"],
        "ontologx_minus_pipeline_macro_f1": mean(differences),
        "wins": sum(diff > 0 for diff in differences),
        "ties": sum(diff == 0 for diff in differences),
        "losses": sum(diff < 0 for diff in differences),
        "paired_permutation_p_two_sided": permutation_p(differences),
        "bootstrap_95_ci_macro_difference": bootstrap_ci(differences),
        "per_paper": per_paper,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in result.items() if key != "per_paper"}, indent=2))


if __name__ == "__main__":
    main()
