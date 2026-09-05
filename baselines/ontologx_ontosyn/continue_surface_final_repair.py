"""Continue a layered-surface run with budget-neutral full-SHACL merge repair."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from graph_merge import graph_inventory
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from parser import OntoSynParser, ParseUsage
from shacl_validate import validate_graph
from ttl_export import read_ttl, write_ttl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ontologx_surface_final_repair")


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def _hint_bundle(paper_record: dict) -> str:
    parts: list[str] = []
    seen: set[Path] = set()
    for entity in paper_record.get("entities", []):
        for layer in entity.get("layers", []):
            path = _resolve(layer["hint"])
            if path in seen:
                continue
            seen.add(path)
            parts.append(
                f"=== {entity.get('label', entity.get('key', 'entity'))} "
                f"ITER{layer.get('layer')} ===\n{path.read_text(encoding='utf-8')}"
            )
    return "\n\n".join(parts)


def _usage_record(usage: ParseUsage, budget: int) -> dict:
    return {
        "calls": usage.calls,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "token_budget": budget,
        "stop_reason": usage.stop_reason,
        "call_details": usage.call_details,
    }


def main() -> None:
    load_dotenv(REPO_ROOT / ".env", override=True)
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source-run", type=Path, required=True)
    cli.add_argument("--out-dir", type=Path, required=True)
    cli.add_argument("--hash", action="append", dest="hashes", required=True)
    cli.add_argument("--model", default="gpt-4o")
    args = cli.parse_args()

    source_run = _resolve(args.source_run)
    out_dir = _resolve(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ontology = REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
    shacl = HERE / "resources" / "ontosynthesis_shacl.ttl"

    first_prompt = source_run / args.hashes[0] / "system_prompt.md"
    prompt = first_prompt.read_text(encoding="utf-8")
    (out_dir / "system_prompt.md").write_text(prompt, encoding="utf-8")
    llm = LLMCreator(
        model=args.model,
        remote_model=True,
        model_config=ModelConfig(timeout=600, temperature=0.0),
        structured_output=False,
    ).setup_llm()
    parser = OntoSynParser(
        llm=llm,
        ontology_path=str(ontology),
        shacl_path=str(shacl),
        prompt=prompt,
        input_label="Paper",
    )

    output = {
        "model": args.model,
        "parser": "ontologx-layered-surface-final-full-shacl-repair",
        "source_run": str(source_run),
        "budget_policy": "paper pooled remainder: sum(entity budgets) - sum(surface usage)",
        "papers": [],
    }
    for hash_id in args.hashes:
        source_summary = json.loads(
            (source_run / hash_id / "summary.json").read_text(encoding="utf-8")
        )
        paper = source_summary["papers"][0]
        graph = read_ttl(_resolve(paper["ttl"]), hash_id)
        initial_ok, initial_messages, _ = validate_graph(
            graph, ontology, shacl, hash_id
        )
        total_budget = sum(
            int(entity.get("token_budget") or 0) for entity in paper.get("entities", [])
        )
        surface_spent = sum(
            int(entity.get("token_usage", {}).get("total_tokens") or 0)
            for entity in paper.get("entities", [])
        )
        remaining = max(0, total_budget - surface_spent)
        usage = ParseUsage(token_budget=remaining)
        final_graph = graph
        final_ok = bool(initial_ok)
        final_messages = initial_messages

        logger.info(
            "%s initial_conforms=%s total_budget=%s surface_spent=%s remaining=%s",
            hash_id,
            initial_ok,
            total_budget,
            surface_spent,
            remaining,
        )
        if not initial_ok and remaining > 0:
            violation_text = "\n".join(initial_messages)
            extra = (
                "\n\nFINAL MERGE REPAIR CONTRACT\n"
                "Continue from the existing merged graph. Fix every reported full-SHACL "
                "violation using only facts supported by the supplied ledgers. Emit a "
                "minimal correction delta and preserve unaffected content.\n\n"
                f"{graph_inventory(graph, heading='EXISTING_MERGED_GRAPH')}\n\n"
                f"INITIAL_FULL_SHACL_REPORT\n{violation_text}"
            )
            repaired, final_ok, final_messages, usage = parser.parse(
                _hint_bundle(paper),
                {
                    "doi": paper["doi"],
                    "hash": hash_id,
                    "entity_key": "paper-merge",
                    "source": "final_merge_repair",
                },
                hash_id,
                extra_human=extra,
                token_budget=remaining,
                existing_graph=graph,
                require_shacl=True,
            )
            if repaired is not None:
                final_graph = repaired
        elif not initial_ok:
            usage.stop_reason = "no_remaining_budget"

        paper_dir = out_dir / hash_id
        ttl = write_ttl(final_graph, hash_id, paper_dir / f"{hash_id}.ttl")
        output["papers"].append(
            {
                "hash": hash_id,
                "doi": paper["doi"],
                "initial_conforms": bool(initial_ok),
                "conforms": bool(final_ok),
                "n_nodes": len(final_graph.nodes),
                "n_relationships": len(final_graph.relationships),
                "ttl": str(ttl.relative_to(REPO_ROOT)),
                "total_budget": total_budget,
                "surface_spent": surface_spent,
                "remaining_budget": remaining,
                "repair_usage": _usage_record(usage, remaining),
                "initial_shacl_messages": initial_messages,
                "shacl_messages": final_messages,
            }
        )
    (out_dir / "summary.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
