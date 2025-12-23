#!/usr/bin/env python3
"""
Agent 2: Generate Script B (label collection / batch downloader)

Inputs:
- TTL schema (T-Box)
- sampling JSON from Script A (predicates + objectValuePredicates)

Output:
- a standalone Python script that:
  - counts instances per class
  - downloads labels in non-overlapping subject batches (keyset pagination)
  - writes JSONL + resume files
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

Generate ONE standalone Python script (Script B) for label harvesting from a SPARQL endpoint.

Hard requirements:
- Output MUST be Python code only (no markdown).
- Must be runnable: if __name__ == "__main__": main()
- Only use standard library + requests.
- Must accept CLI args:
  --ttl <path>              (TTL schema input)
  --sampling <path>         (sampling JSON produced by Script A)
  --endpoint <url>          (SPARQL endpoint)
  --out-dir <dir>           (where to write per-class JSONL label files)
  --resume-dir <dir>        (where to write per-class resume JSON)
  --class <localName>       (repeatable; if omitted, harvest all classes in sampling that have suggestedLookupPredicates)
  --subject-batch-size 2000 (default 2000)
  --values-chunk-size 500   (default 500)
  --max-batches 0           (0 means no cap)
  --count-only              (optional; if set, only count DISTINCT subjects per class and exit)
  --test                    (optional; convenience: sets --max-batches=2)
  --output-mode sharded-jsonl|jsonl|sqlite   (default sharded-jsonl)
  --max-rows-per-shard 200000                (only for sharded-jsonl; rotate after N rows)
  --sqlite-path <path>                       (required if output-mode=sqlite)

Core logic:
1) For each class:
   - Determine class IRI from TTL (classLocalName -> iri).
   - COUNT DISTINCT subjects (this is the ground-truth progress metric):
       COUNT(DISTINCT ?s) WHERE {{ ?s a <classIRI> . }}
     IMPORTANT: Do NOT treat JSONL row count as coverage; one subject can emit multiple rows (multiple labels).
   - Download subjects by KEYSET pagination:
       SELECT ?s WHERE {{ ?s a <classIRI> . FILTER(STR(?s) > last_s) }} ORDER BY STR(?s) LIMIT subject_batch_size
     Persist last_s in resume file so runs can resume.
   - Track progress by UNIQUE subjects processed:
     - Each subject batch advances by the number of subjects returned (<= subject_batch_size).
     - Maintain and persist resume fields:
         last_s, subjects_done, subjects_total, batches_done, rows_written
     - Print progress lines like:
         class=Species subjects_done=4000/38000 rows_written=12345 last_s=...
2) For each subject batch, download labels for those subjects:
   - Use VALUES ?s {{ <iri1> <iri2> ... }} with chunking (values-chunk-size).
   - Use sampling JSON to decide label extraction patterns:
     - For each predicate in suggestedLookupPredicates:
       - If sampling has objectValuePredicates[predicate], treat as 2-hop payload:
           ?s <pred> ?n . ?n <payloadPred> ?label
       - Else treat as direct literal:
           ?s <pred> ?label
   - Emit JSONL rows with: classLocalName, s, label, source (predicate or predicate/predicate|payloadPred).
     NOTE: Repeated "s" across multiple rows is expected and correct.

Output / performance requirements:
- Do NOT append forever to a single huge JSONL file by default.
- For output-mode=sharded-jsonl (default):
  - Write to rotating shards under out-dir, e.g.:
      out-dir/<classLocalName>/part-000001.jsonl
      out-dir/<classLocalName>/part-000002.jsonl
    Rotate when current shard exceeds --max-rows-per-shard (count rows written).
  - Flush frequently, and close file handles between batches (avoid long-lived handles).
- For output-mode=jsonl:
  - Write one file per class (out-dir/<classLocalName>.jsonl) (legacy, can be slower over time).
- For output-mode=sqlite:
  - Use sqlite3 from stdlib.
  - Create a DB at --sqlite-path with a table:
      labels(classLocalName TEXT, s TEXT, label TEXT, source TEXT)
  - Create helpful indices:
      idx_labels_class_s on (classLocalName, s)
      idx_labels_class_label on (classLocalName, label)
  - Insert using executemany within a transaction per subject batch for speed.

Batching:
- Must be non-overlapping.
- Must compute and print estimated total batches: ceil(count/subject_batch_size).
- Must ensure completeness when max-batches=0:
  - Stop only when a subject page returns 0 subjects.
  - If subjects_done < subjects_total at termination, print a WARNING (this indicates endpoint instability / query bug).

Safety:
- Handle HTTP errors gracefully (retry small number of times is OK).
- Keep SPARQL queries as normal strings + .format (do not use python f-strings with SPARQL braces).
  IMPORTANT: When using Python .format(), SPARQL group braces MUST be doubled:
    - Use `WHERE {{ ... }}` not `WHERE { ... }`
    - Use `VALUES ?s {{ ... }}` not `VALUES ?s { ... }`

Ontology: {ontology_name}
Endpoint: {endpoint_url}

Sampling JSON (concise):
{sampling_text}

TTL schema:
{ttl_text}
""".strip()


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Agent 2: generate Script B (label batch download).")
    p.add_argument("--ontology-name", required=True)
    p.add_argument("--ttl", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--sampling", required=True)
    p.add_argument("--out", required=True, help="Output path of Script B (python file)")
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
    generate_python_module_with_repair(
        prompt=prompt,
        out_path=out_path,
        cfg=LLMGenConfig(model=str(args.model).strip()),
        require_substrings=["def main", "if __name__ == \"__main__\""],
    )
    print(json.dumps({"status": "ok", "script": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()


