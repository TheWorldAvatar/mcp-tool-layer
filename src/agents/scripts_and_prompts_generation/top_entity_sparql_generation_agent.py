#!/usr/bin/env python3
"""
top_entity_sparql_generation_agent.py

LLM-backed agent to create a SPARQL query that extracts "top entities"
from an ontology A-Box, based solely on the T-Box TTL schema.

It generates:
  ai_generated_contents_candidate/sparqls/<ontology_name>/top_entity_parsing.sparql

For compatibility with existing runtime tooling that reads from `ai_generated_contents/`,
this script also mirrors the generated query to:
  ai_generated_contents/sparqls/<ontology_name>/top_entity_parsing.sparql

The query is intended for use by the top_entity_kg_building pipeline step,
and should follow the general shape:

  SELECT DISTINCT ?synthesis ?label WHERE {
      ?synthesis a <TopLevelClassIRI> .
      OPTIONAL { ?synthesis rdfs:label ?label }
  }

But the actual <TopLevelClassIRI> and prefixes are inferred by the LLM
from the provided T-Box TTL; we do not hardcode ontology-specific details here.

Usage (CLI):
  # Generate SPARQL for one or more ontologies (expects data/ontologies/<name>.ttl)
  python -m src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent --ontosynthesis
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List

import httpx
from dotenv import load_dotenv

from src.utils.global_logger import get_logger


LOGGER = get_logger("agent", "TopEntitySPARQLAgent")


def _read_text_file(file_path: Path) -> str:
    """Read a text file in UTF-8, returning empty string if missing."""
    try:
        return file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _compose_prompt(ttl_text: str, ontology_name: str) -> str:
    """
    Build a domain-agnostic prompt that asks the LLM to produce one SPARQL query
    for extracting top-level entities from an A-Box, given only the T-Box.
    """
    header = f"""You are given the T-Box (schema) of an RDF/OWL ontology in Turtle syntax.

Your task is to produce ONE SPARQL SELECT query that lists all instances of the primary
"top entity" class in this ontology. This is the main class representing the thing being
modeled (for example, the main domain entity described by this ontology).

Strict requirements:
- Output MUST be a SPARQL query ONLY (no markdown, no comments, no explanation).
- Use PREFIX declarations that are consistent with the T-Box (reuse existing prefixes,
  especially rdfs).
- The query MUST:
  - Use variable ?entity for the subject instances (the top-level entities),
    regardless of the actual class name.
  - Optionally bind a human-readable label in ?label using rdfs:label if available.
- The general pattern should be logically equivalent to:

  SELECT DISTINCT ?entity ?label WHERE {{
    ?entity a <TopLevelClassIRI> .
    OPTIONAL {{ ?entity rdfs:label ?label }}
  }}

- Replace <TopLevelClassIRI> with the IRI of the primary top-level entity class inferred
  from the T-Box. Do NOT leave angle brackets or placeholders in the final output.
- Do NOT assume any dataset-specific instances; rely only on the schema to infer the
  correct top-level class.

Heuristics for choosing the top-level class:
- Prefer a class whose rdfs:comment or definition clearly indicates it is the main product
  or primary entity (e.g., mentions "Represents one standalone ...", "main entity",
  "product", "core concept", etc.).
- If multiple such classes exist, choose the single most central one.

Return ONLY the SPARQL query text.

Ontology short name (hint): {ontology_name}

T-Box (schema, in Turtle):
"""
    return header + "\n" + ttl_text


def _generate_sparql_with_llm(ttl_text: str, ontology_name: str, model: str = "gpt-4o") -> str:
    """
    Generate a SPARQL query for the top entity from the T-Box.

    Implementation note:
    - We intentionally avoid depending on optional SDKs (e.g., `openai`, `langchain_openai`).
    - We call an OpenAI-compatible Chat Completions HTTP endpoint via `httpx` using
      environment variables (REMOTE_API_KEY / REMOTE_BASE_URL, with common fallbacks).
    """
    if not ttl_text.strip():
        raise ValueError("Empty TTL text provided to SPARQL generation agent.")

    prompt = _compose_prompt(ttl_text, ontology_name)
    # NOTE: avoid non-ASCII characters in console output (Windows cp1252 terminals).
    print(f"Invoking LLM to create top_entity_parsing.sparql for '{ontology_name}' ...", flush=True)

    load_dotenv(override=True)
    api_key = (
        os.getenv("REMOTE_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = (
        os.getenv("REMOTE_BASE_URL")
        or os.getenv("BASE_URL")
        or "https://api.openai.com/v1"
    )

    if not api_key:
        raise RuntimeError(
            "No API key found for SPARQL generation. Set REMOTE_API_KEY (preferred) or OPENAI_API_KEY."
        )

    bu = str(base_url).rstrip("/")
    if bu.endswith("/v1"):
        url = bu + "/chat/completions"
    else:
        url = bu + "/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return ONLY a SPARQL SELECT query. No markdown, no comments, no explanation."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 2000,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=120) as client:
        r = client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()

    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise RuntimeError(f"Unexpected LLM response shape: {str(data)[:500]}")

    # Strip accidental markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop opening fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Drop closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return text


def _write_sparql(ontology_name: str, sparql_text: str) -> Path:
    """
    Write the SPARQL text to the candidate location, and mirror to the runtime location.

    Candidate output:
      ai_generated_contents_candidate/sparqls/<ontology_name>/top_entity_parsing.sparql

    Runtime mirror:
      ai_generated_contents/sparqls/<ontology_name>/top_entity_parsing.sparql
    """
    # Candidate path (used by generation artifacts)
    cand_dir = Path("ai_generated_contents_candidate") / "sparqls" / ontology_name
    cand_dir.mkdir(parents=True, exist_ok=True)
    cand_path = cand_dir / "top_entity_parsing.sparql"
    cand_path.write_text(sparql_text, encoding="utf-8")

    # Runtime mirror (best-effort)
    try:
        run_dir = Path("ai_generated_contents") / "sparqls" / ontology_name
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / "top_entity_parsing.sparql"
        run_path.write_text(sparql_text, encoding="utf-8")
    except Exception:
        pass

    return cand_path


def generate_top_entity_sparql_for_ontology(ontology_name: str, model: str = "gpt-4o") -> Path:
    """
    High-level helper:
      1) Load T-Box from data/ontologies/<ontology_name>.ttl
      2) Ask LLM to generate SPARQL for top entity extraction
      3) Save to ai_generated_contents_candidate/sparqls/<ontology_name>/top_entity_parsing.sparql
         (and mirror to ai_generated_contents/sparqls/<ontology_name>/top_entity_parsing.sparql)
    """
    ttl_path = Path("data/ontologies") / f"{ontology_name}.ttl"
    ttl_text = _read_text_file(ttl_path)
    if not ttl_text:
        raise FileNotFoundError(f"T-Box TTL not found or empty: {ttl_path}")

    # Fast path: if a SPARQL already exists in the runtime tree, mirror it into candidate.
    # This fixes output-folder mismatch even when LLM tooling isn't configured locally.
    existing_runtime = Path("ai_generated_contents") / "sparqls" / ontology_name / "top_entity_parsing.sparql"
    existing_txt = _read_text_file(existing_runtime)
    if existing_txt.strip():
        out_path = _write_sparql(ontology_name, existing_txt.strip())
        LOGGER.info(f"Mirrored existing SPARQL for '{ontology_name}' to: {out_path}")
        print(f"SPARQL mirrored: {out_path}")
        return out_path

    # Use the provided ontology_name consistently; do not bake in ontology-specific heuristics.
    LOGGER.info(f"Generating top-entity SPARQL for ontology '{ontology_name}'")

    sparql_text = _generate_sparql_with_llm(ttl_text, ontology_name, model=model)
    out_path = _write_sparql(ontology_name, sparql_text)
    LOGGER.info(f"✅ Wrote SPARQL query for '{ontology_name}' to: {out_path}")
    print(f"SPARQL saved: {out_path}")
    return out_path


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate top_entity_parsing.sparql from a T-Box TTL using an LLM.",
        epilog="""
Examples:
  # Generate SPARQL for ontosynthesis (expects data/ontologies/ontosynthesis.ttl)
  python -m src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent --ontosynthesis

  # Generate SPARQL for multiple ontologies with a specific model
  python -m src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent --ontosynthesis --ontomops --model gpt-4.1
        """,
    )

    # Ontology flags (mirroring style of iteration_creation_agent)
    parser.add_argument(
        "--ontosynthesis",
        action="store_true",
        help="Generate SPARQL for the ontosynthesis ontology",
    )
    parser.add_argument(
        "--ontomops",
        action="store_true",
        help="Generate SPARQL for the ontomops ontology",
    )
    parser.add_argument(
        "--ontospecies",
        action="store_true",
        help="Generate SPARQL for the ontospecies ontology",
    )
    parser.add_argument(
        "--ontology",
        type=str,
        action="append",
        dest="ontologies",
        help="Additional ontology short name(s) to generate SPARQL for (uses data/ontologies/<name>.ttl)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="LLM model name to use via LLMCreator (default: gpt-4o)",
    )

    args = parser.parse_args(argv)

    # Collect ontology names from flags
    requested: List[str] = []
    if args.ontosynthesis:
        requested.append("ontosynthesis")
    if args.ontomops:
        requested.append("ontomops")
    if args.ontospecies:
        requested.append("ontospecies")
    if args.ontologies:
        requested.extend(args.ontologies)

    if not requested:
        print("No ontologies specified. Use --ontosynthesis, --ontomops, --ontospecies, or --ontology <name>.")
        return

    for ont in requested:
        try:
            generate_top_entity_sparql_for_ontology(ont, model=args.model)
        except Exception as e:
            LOGGER.error(f"Failed to generate SPARQL for ontology '{ont}': {e}")
            print(f"Failed to generate SPARQL for '{ont}': {e}")


if __name__ == "__main__":
    main()


