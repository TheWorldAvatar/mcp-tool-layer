#!/usr/bin/env python3
"""
Agent 1: Generate Script A (sampling-only query interface)

This agent writes a standalone Python script that:
- connects to a SPARQL endpoint
- auto-selects "important" classes to sample (no manual --class required)
- samples predicates for each selected class
- records concise sampling output (predicates + minimal 2-hop literal payload hints)

The TTL schema is the ONLY domain-specific input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from src.agents.grounding._llm_script_agent_utils import LLMGenConfig, generate_python_module_with_repair


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _compose_prompt(*, ttl_text: str, ontology_name: str, endpoint_url: str) -> str:
    return f"""
You are a senior Python engineer.

Generate ONE standalone Python script (Script A) that samples a SPARQL endpoint to discover
which predicates are actually present on instances of important classes.

Hard requirements:
- Output MUST be Python code only (no markdown).
- Must be runnable as a script with: if __name__ == "__main__": main()
- Only use standard library + requests.
- Must accept CLI args:
  --ttl <path>                (TTL schema input)
  --endpoint <url>            (SPARQL endpoint)
  --ontology-name <name>      (string, for metadata)
  --out <path>                (sampling JSON output)
  --top-k <int>               (default 12)  [classes by instance count]
  --max-object-literal-preds <int> (default 15) [for 2-hop payload discovery]
  --sleep <float>             (default 0.1)

Selection logic (automatic):
- Parse OWL classes from the TTL (rdflib is OK if installed; otherwise a lightweight TTL parse is OK).
- Query instance counts per class using a VALUES query and select:
  - top-k classes by instance count (exclude zero)
  - plus any classes that look label-like from the TTL (datatype props containing name/label/id/inchi/smiles/cas/key)

Sampling output (keep concise!):
- Write JSON with shape:
  {{
    "meta": {{...}},
    "classes": [
      {{
        "classLocalName": "...",
        "classIRI": "...",
        "selectedBy": "...",
        "predicates": ["<predIRI>", ...],
        "objectValuePredicates": {{
           "<predIRI>": ["<payloadPredIRI>", ...]
        }},
        "suggestedLookupPredicates": ["<predIRI>", ...]
      }}
    ]
  }}

Sampling details:
- For each selected class, list predicates actually used by instances:
    SELECT ?p (COUNT(?p) AS ?count) (SAMPLE(?o) AS ?example) (SAMPLE(isIRI(?o)) AS ?isIRI)
    GROUP BY ?p ORDER BY DESC(?count)
- Keep only predicate IRIs in the JSON "predicates" list (do not store count/example).
- For lookup-relevant predicates where sampled object is an IRI, do one-hop expansion to find which
  predicates on that object yield literals (payload predicates). Store only predicate IRIs in objectValuePredicates.

Lookup predicate suggestion:
- Suggested lookup predicates should include any predicate whose local name suggests label/name/identifier/cas/inchi/smiles/key/cid/chebi/iupac,
  and include any skos:*Label predicates seen (prefLabel/altLabel) if present.

Also:
- Include PREFIX rdfs/skos in queries if needed; do not assume TTL includes them.
- Print a short progress line per class and a final line with output path.

Ontology short name: {ontology_name}
Endpoint: {endpoint_url}

T-Box TTL:
{ttl_text}
""".strip()


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Agent 1: generate Script A (sampling) from TTL.")
    p.add_argument("--ontology-name", required=True)
    p.add_argument("--ttl", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--out", required=True, help="Output path of Script A (python file)")
    p.add_argument("--model", default="gpt-4.1")
    args = p.parse_args(argv)

    ttl_text = _read_text(Path(args.ttl))
    prompt = _compose_prompt(
        ttl_text=ttl_text,
        ontology_name=str(args.ontology_name).strip(),
        endpoint_url=str(args.endpoint).strip(),
    )

    out_path = Path(args.out)
    generate_python_module_with_repair(
        prompt=prompt,
        out_path=out_path,
        cfg=LLMGenConfig(model=str(args.model).strip()),
        require_substrings=["def main", "if __name__ == \"__main__\""],
    )
    print(json.dumps({"status": "ok", "script": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()


