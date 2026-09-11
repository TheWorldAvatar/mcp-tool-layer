"""Locate and merge per-entity extraction hints for KG building."""

from __future__ import annotations

from pathlib import Path

MAIN_KG_HINT_ITERS = (2, 3, 4)


def find_hints_file(*, mcp_run_dir: str | Path, iter_num: int, entity_safe: str) -> Path | None:
    path = Path(mcp_run_dir) / f"iter{iter_num}_hints_{entity_safe}.txt"
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def load_entity_hints(doi_folder: str | Path, entity_safe: str) -> str:
    """Join iter2 + iter3 + iter4 ledgers in the official whole-graph format."""
    mcp_run = Path(doi_folder) / "mcp_run"
    sections: list[str] = []
    if not mcp_run.is_dir():
        return ""
    for iter_num in MAIN_KG_HINT_ITERS:
        path = find_hints_file(
            mcp_run_dir=mcp_run, iter_num=iter_num, entity_safe=entity_safe
        )
        if path is None:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        sections.append(
            f"=== ITER{iter_num} SEMANTIC_HINTS ===\n"
            f"Source: {path.resolve()}\n"
            f"{text}"
        )
    if not sections:
        return ""
    return (
        "SEMANTIC_HINTS_V1\n"
        "Whole-graph ledger: complementary iteration views of one bound top-level entity.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )


def is_semantic_hint_content(content: str) -> bool:
    return str(content or "").lstrip().startswith("SEMANTIC_HINTS_V1")
