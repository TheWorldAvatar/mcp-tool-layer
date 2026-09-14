"""Load ITER2/3/4 (and extension) extraction ledgers for one paper."""

from __future__ import annotations

from pathlib import Path

from src.kg_building.pipeline.main_kg.hints import load_entity_hints

_MAIN_PREFIX = "iter3_hints_"


def resolve_mcp_run(hint_run: str | Path, paper_hash: str) -> Path | None:
    """Locate ``mcp_run`` under a pipeline run, OX hint-run, or explicit folder."""
    candidate = Path(hint_run)
    if candidate.name == "mcp_run" and candidate.is_dir():
        return candidate
    nested = [
        candidate / "runtime" / paper_hash / "mcp_run",
        candidate / paper_hash / "mcp_run",
        candidate / "mcp_run",
        candidate,
    ]
    for path in nested:
        if path.is_dir() and (
            any(path.glob("iter3_hints_*.txt"))
            or any(path.glob("iter2_hints_*.txt"))
            or any(path.glob("*_iter*_hints_*.txt"))
        ):
            return path
    return None


def _entity_safe_from_iter3(path: Path) -> str:
    stem = path.name
    if stem.startswith(_MAIN_PREFIX):
        stem = stem[len(_MAIN_PREFIX) :]
    if stem.endswith(".txt"):
        stem = stem[: -len(".txt")]
    return stem


def load_paper_ledger(
    paper_hash: str,
    hint_runs: list[str | Path],
) -> tuple[str, list[str]]:
    """Return the merged SEMANTIC_HINTS text and the source file paths."""
    sections: list[str] = []
    sources: list[str] = []
    for run in hint_runs:
        mcp_run = resolve_mcp_run(run, paper_hash)
        if mcp_run is None:
            continue
        seen_entities: set[str] = set()
        for iter3 in sorted(mcp_run.glob("iter3_hints_*.txt")):
            if ".pre_size_dedup" in iter3.name:
                continue
            entity = _entity_safe_from_iter3(iter3)
            if entity in seen_entities:
                continue
            seen_entities.add(entity)
            text = load_entity_hints(mcp_run.parent, entity).strip()
            if not text:
                text = iter3.read_text(encoding="utf-8").strip()
            if not text:
                continue
            sources.append(str(iter3))
            sections.append(
                f"=== ENTITY {entity} ===\n{text}"
            )
        if not seen_entities:
            for path in sorted(mcp_run.glob("iter*_hints_*.txt")):
                if ".pre_size_dedup" in path.name:
                    continue
                text = path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                sources.append(str(path))
                sections.append(f"=== FILE {path.name} ===\n{text}")
        for pattern in (
            "ontomops_iter*_hints_*.txt",
            "ontospecies_iter*_hints_*.txt",
        ):
            for path in sorted(mcp_run.glob(pattern)):
                if str(path) in sources:
                    continue
                text = path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                sources.append(str(path))
                sections.append(f"=== FILE {path.name} ===\n{text}")
        if sections:
            break
    return ("\n\n".join(sections) + "\n") if sections else "", sources
