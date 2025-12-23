#!/usr/bin/env python3
"""
Agent 3: Generate Script C (final query interface + lookup functions).

Inputs:
- TTL schema (T-Box)
- sampling JSON (A-Box predicate evidence)

Output:
- a Python module (Script C) that provides:
  - atomic query functions (list/get/lookup) using SPARQL
  - local fuzzy lookup functions that load label files produced by Script B

Script C should NOT download labels; it only consumes local artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from src.agents.grounding._llm_script_agent_utils import LLMGenConfig, generate_python_module_with_repair


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _compose_prompt(*, ttl_text: str, sampling_text: str, ontology_name: str, endpoint_url: str) -> str:
    return f"""
You are a senior Python engineer.

Generate ONE Python module (Script C) that is the final query interface for an ontology SPARQL endpoint.
This module must include:
- SPARQL execution helper (POST + JSON bindings)
- atomic query functions similar to a kg_operations module:
  - list_<class>(limit, order)
  - lookup_<class>_iri_by_label(...)
  - get_<class>_<property>(subject_iri, ...)
- local fuzzy lookup functions built on local label files produced by Script B.

Hard requirements:
- Output MUST be Python code only (no markdown).
- Must be importable (no syntax errors).
- Only use standard library + requests.
- Define ENDPOINT_URL = "{endpoint_url}"
- Do NOT download labels from SPARQL in this module. Only load local label files.

Local label files contract (from Script B):
- Label files are JSONL. Each line is a JSON object with keys at least:
    classLocalName, s, label, source
- Script C should accept (module-level constant or function arg):
    LABELS_DIR (default: data/grounding_cache/<ontology_name>/labels)
- Label output may be sharded: multiple JSONL files per class and/or nested subdirectories.
  Script C should load all `*.jsonl` files under LABELS_DIR recursively.
- Implement:
    load_label_index(force=False) -> dict mapping classLocalName -> {{normalized_label: set(iri)}}
  and:
    fuzzy_lookup_<class>(query, limit=10, cutoff=0.6) -> list of {{label, iri, score}}

Use sampling JSON + TTL to decide which classes should have fuzzy lookup helpers (only those with suggestedLookupPredicates).

Also:
- Avoid python f-strings that include SPARQL curly braces; use normal strings + .format.

Ontology: {ontology_name}
Endpoint: {endpoint_url}

Sampling JSON (concise):
{sampling_text}

TTL schema:
{ttl_text}
""".strip()


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Agent 3: generate Script C (final query interface module).")
    p.add_argument("--ontology-name", required=True)
    p.add_argument("--ttl", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--sampling", required=True)
    p.add_argument("--out", required=True, help="Output path of Script C (python module)")
    p.add_argument("--model", default="gpt-4.1")
    args = p.parse_args(argv)

    ttl_text = _read_text(Path(args.ttl))
    sampling_text = _read_text(Path(args.sampling))
    prompt = _compose_prompt(
        ttl_text=ttl_text,
        sampling_text=sampling_text,
        ontology_name=str(args.ontology_name).strip(),
        endpoint_url=str(args.endpoint).strip(),
    )

    out_path = Path(args.out)
    # Require ENDPOINT_URL presence and importability
    generate_python_module_with_repair(
        prompt=prompt,
        out_path=out_path,
        cfg=LLMGenConfig(model=str(args.model).strip()),
        require_substrings=["ENDPOINT_URL", "def execute_sparql"],
    )
    print(json.dumps({"status": "ok", "module": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()


