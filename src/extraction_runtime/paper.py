"""Shared paper-markdown loader used by every extraction and KG step."""

from __future__ import annotations

from pathlib import Path


def _usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def slim_markdown_path(doi_hash: str, data_dir: str) -> Path:
    return Path(data_dir) / doi_hash / f"{doi_hash}_slim.md"


def load_paper_content_with_sources(
    doi_hash: str, data_dir: str
) -> tuple[str, list[str]]:
    """Load the best-available paper body and append SI when present.

    Vision transcripts (`_vision.md`) are the OntoMed body. Chemistry uses
    `{hash}_slim.md` as the complete extraction body (SI already inside).
    Otherwise main-text priority: `_vision.md` → `.md` → `_text.md`, then
    append SI (`_si_text.md` → `_si_vision.md` → `_si.md` → `_si_tables.md`).
    """
    doi_dir = Path(data_dir) / doi_hash
    vision_path = doi_dir / f"{doi_hash}_vision.md"
    if _usable(vision_path):
        return vision_path.read_text(encoding="utf-8"), [str(vision_path)]
    slim_path = slim_markdown_path(doi_hash, data_dir)
    if _usable(slim_path):
        return slim_path.read_text(encoding="utf-8"), [str(slim_path)]
    main_candidates = (
        doi_dir / f"{doi_hash}.md",
        doi_dir / f"{doi_hash}_text.md",
    )
    main_text = ""
    source_paths: list[str] = []
    for path in main_candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if text.strip():
            main_text = text
            source_paths.append(str(path))
            break
    if not main_text:
        return "", []

    parts = [main_text]
    for si_name in (
        f"{doi_hash}_si_text.md",
        f"{doi_hash}_si_vision.md",
        f"{doi_hash}_si.md",
        f"{doi_hash}_si_tables.md",
    ):
        si_path = doi_dir / si_name
        if not si_path.is_file():
            continue
        si_text = si_path.read_text(encoding="utf-8")
        if si_text.strip():
            parts.append(f"\n\n# Supporting Information: {si_name}\n\n{si_text}")
            source_paths.append(str(si_path))
    return "".join(parts), source_paths


def load_paper_content(doi_hash: str, data_dir: str) -> str:
    content, _ = load_paper_content_with_sources(doi_hash, data_dir)
    return content
