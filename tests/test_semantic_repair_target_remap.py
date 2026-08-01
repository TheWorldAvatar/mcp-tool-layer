from __future__ import annotations

from pathlib import Path


def test_semantic_diagnosis_targets_can_be_remapped_between_iterations(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "iter_0"
    candidate_root = tmp_path / "iter_1"
    relative = Path("scripts") / "neutral" / "main.py"
    source = source_root / relative
    candidate = candidate_root / relative
    source.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    source.write_text("OLD = True\n", encoding="utf-8")
    candidate.write_text("OLD = True\n", encoding="utf-8")

    mapped = candidate_root / source.resolve().relative_to(source_root.resolve())

    assert mapped.resolve() == candidate.resolve()
