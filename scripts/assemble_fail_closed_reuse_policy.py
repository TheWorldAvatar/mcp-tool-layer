from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.agents.scripts_and_prompts_generation.config_derivation import (
    build_fail_closed_reuse_policy,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile a no-reuse runtime policy from ten valid trials."
    )
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    policy = build_fail_closed_reuse_policy(summary=summary)
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
                "class_count": len(policy["classes"]),
                "reuse_enabled_count": 0,
                "status": policy["status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
