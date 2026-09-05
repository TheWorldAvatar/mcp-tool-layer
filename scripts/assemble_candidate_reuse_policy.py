from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.agents.scripts_and_prompts_generation.config_derivation import (
    build_candidate_reuse_policy,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble a review-gated reuse policy from stable trials."
    )
    parser.add_argument("--summary", required=True)
    parser.add_argument("--representative-trial", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    trial = json.loads(Path(args.representative_trial).read_text(encoding="utf-8"))
    policy = build_candidate_reuse_policy(
        summary=summary,
        representative_trial=trial,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(policy, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "status": policy["status"],
                "class_count": len(policy["classes"]),
                "pending_match_basis_reviews": sum(
                    item["review"] == "pending_match_basis_review"
                    for item in policy["classes"]
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
