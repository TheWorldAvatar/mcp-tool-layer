from __future__ import annotations

import argparse
import json

from src.agents.scripts_and_prompts_generation.config_derivation import (
    promote_reviewed_reuse_policy,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote an approved reuse candidate to runtime policy."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            promote_reviewed_reuse_policy(
                source_path=args.source,
                output_path=args.output,
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
