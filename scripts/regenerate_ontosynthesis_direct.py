#!/usr/bin/env python3
"""
Regenerate OntoSynthesis candidate MCP scripts using the direct LLM generators.

This is a focused runner that bypasses the full orchestration pipeline and is useful
for debugging generation/validation logic.
"""

import asyncio
import traceback

from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    generate_checks_script_direct,
    generate_relationships_script_direct,
    generate_base_script_direct,
    generate_entities_script_direct,
    generate_main_script_direct,
)


ONTOLOGY_NAME = "ontosynthesis"
ONTOLOGY_TTL = "data/ontologies/ontosynthesis.ttl"
OUTPUT_DIR = "ai_generated_contents_candidate/scripts/ontosynthesis"
MODEL = "gpt-5"


async def main() -> None:
    try:
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
        )
        print(f"main: {main_path}", flush=True)

    except Exception as e:
        print("\nERROR:", e, flush=True)
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())


