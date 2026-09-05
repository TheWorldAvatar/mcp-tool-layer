from __future__ import annotations

import argparse
import json

from src.agents.scripts_and_prompts_generation.config_derivation import (
    write_orchestration_config,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip ontology-semantic priors from a domain config."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    summary = write_orchestration_config(
        source_path=args.source,
        output_path=args.output,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
