#!/usr/bin/env python3
"""
Regenerate OntoSynthesis candidate MCP scripts using the direct LLM generators.

This is a focused runner that bypasses the full orchestration pipeline and is useful
for debugging generation/validation logic.
"""

import asyncio
import argparse
import json
import traceback
from pathlib import Path

from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    generate_checks_script_direct,
    generate_relationships_script_direct,
    generate_base_script_direct,
    generate_entities_script_direct,
    generate_main_script_direct,
    repair_generated_entity_scripts,
)


ONTOLOGY_NAME = "ontosynthesis"
ONTOLOGY_TTL = "data/ontologies/ontosynthesis.ttl"
OUTPUT_DIR = "ai_generated_contents_candidate/scripts/ontosynthesis"
MODEL = "gpt-5.2"
META_TASK_CONFIG = "configs/meta_task/meta_task_config.json"


def _existing_entity_paths() -> list[str]:
    base = Path(OUTPUT_DIR)
    return [
        str(base / f"{ONTOLOGY_NAME}_creation_entities_1.py"),
        str(base / f"{ONTOLOGY_NAME}_creation_entities_2.py"),
    ]


async def main(repair_existing_only: bool = False) -> None:
    try:
        meta_cfg_path = Path(META_TASK_CONFIG)
        meta_cfg = json.loads(meta_cfg_path.read_text(encoding="utf-8")) if meta_cfg_path.exists() else None

        if repair_existing_only:
            print("== repair existing entities ==", flush=True)
            entity_paths = repair_generated_entity_scripts(
                ONTOLOGY_NAME,
                _existing_entity_paths(),
            )
            print(f"entities repaired: {entity_paths}", flush=True)

            print("== rebuild main ==", flush=True)
            main_path = await generate_main_script_direct(
                ONTOLOGY_TTL,
                ONTOLOGY_NAME,
                str(Path(OUTPUT_DIR) / f"{ONTOLOGY_NAME}_creation_checks.py"),
                str(Path(OUTPUT_DIR) / f"{ONTOLOGY_NAME}_creation_relationships.py"),
                str(Path(OUTPUT_DIR) / f"{ONTOLOGY_NAME}_creation_base.py"),
                entity_paths,
                OUTPUT_DIR,
                model_name=MODEL,
                max_retries=3,
                meta_cfg=meta_cfg,
            )
            print(f"main: {main_path}", flush=True)
            return

        print("== checks ==", flush=True)
        checks_path = await generate_checks_script_direct(
            ONTOLOGY_TTL, ONTOLOGY_NAME, OUTPUT_DIR, model_name=MODEL, max_retries=3
        )
        print(f"checks: {checks_path}", flush=True)

        print("== relationships ==", flush=True)
        rel_path = await generate_relationships_script_direct(
            ONTOLOGY_TTL, ONTOLOGY_NAME, OUTPUT_DIR, model_name=MODEL, max_retries=3
        )
        print(f"relationships: {rel_path}", flush=True)

        print("== base ==", flush=True)
        base_path = await generate_base_script_direct(
            ONTOLOGY_TTL, ONTOLOGY_NAME, OUTPUT_DIR, model_name=MODEL, max_retries=3
        )
        print(f"base: {base_path}", flush=True)

        print("== entities ==", flush=True)
        entity_paths = await generate_entities_script_direct(
            ONTOLOGY_TTL,
            ONTOLOGY_NAME,
            OUTPUT_DIR,
            base_path,
            checks_path,
            rel_path,
            model_name=MODEL,
            max_retries=3,
        )
        print(f"entities: {entity_paths}", flush=True)

        print("== main ==", flush=True)
        main_path = await generate_main_script_direct(
            ONTOLOGY_TTL,
            ONTOLOGY_NAME,
            checks_path,
            rel_path,
            base_path,
            entity_paths,
            OUTPUT_DIR,
            model_name=MODEL,
            max_retries=3,
            meta_cfg=meta_cfg,
        )
        print(f"main: {main_path}", flush=True)

    except Exception as e:
        print("\nERROR:", e, flush=True)
        traceback.print_exc()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repair-existing",
        action="store_true",
        help="Repair existing generated entity scripts with deterministic normalizers and rebuild main.py",
    )
    args = parser.parse_args()
    asyncio.run(main(repair_existing_only=args.repair_existing))


