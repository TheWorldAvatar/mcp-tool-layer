"""Copy RDF runtime, OM-2 runtime, reuse judge, and relationship contract."""

from __future__ import annotations

from pathlib import Path

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
    runtime_publish_contract,
)
from src.extraction_prompt_generation.pipeline.runtime_support import (
    generate_runtime_support_slice,
)


def write_runtime_support(context: AgenticGenerationContext) -> list[str]:
    """Write flattened RDF, pair judge, relationship contract, SPARQL, iterations."""
    written = list(generate_runtime_support_slice(context))
    scripts_dir = Path(context.scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    judge_src = (
        Path(__file__).resolve().parents[1].parent
        / "extraction_prompt_generation"
        / "compile"
        / "reuse_pair_judge.py"
    )
    judge_dest = scripts_dir / "_reuse_pair_judge.py"
    judge_dest.write_text(judge_src.read_text(encoding="utf-8"), encoding="utf-8")
    written.append(str(judge_dest))
    contract_path = scripts_dir / "_relationship_contract.json"
    import json

    contract_path.write_text(
        json.dumps(
            runtime_publish_contract(context.contract),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(str(contract_path))
    return written
