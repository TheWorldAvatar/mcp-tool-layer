from __future__ import annotations

from src.agents.scripts_and_prompts_generation.repair_skill_catalog import (
    repair_skill_catalog,
    repair_skill_ids,
)


def test_rdf_export_skill_requires_fixed_runtime_and_real_triples() -> None:
    skills = {
        skill["skill_id"]: skill
        for skill in repair_skill_catalog()
    }
    rdf_skill = skills["rdf-runtime-export"]

    assert "rdf-runtime-export" in repair_skill_ids()
    assert any("._fixed_rdf_runtime" in repair for repair in rdf_skill["standard_repairs"])
    assert any("real domain triples" in repair for repair in rdf_skill["standard_repairs"])
    assert "serializing an empty graph" in rdf_skill["anti_patterns"]


def test_small_diff_skill_requires_verbatim_old_side() -> None:
    skills = {
        skill["skill_id"]: skill
        for skill in repair_skill_catalog()
    }
    diff_skill = skills["small-unified-diff"]

    assert any(
        "sole source of truth" in repair for repair in diff_skill["standard_repairs"]
    )
    assert "context reconstructed from memory" in diff_skill["anti_patterns"]
