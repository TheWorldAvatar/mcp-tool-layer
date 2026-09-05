import pytest

from scripts.promote_aliases import promote


def _registry():
    return {
        "schema_version": "chemical-species-aliases.v1",
        "species": [
            {
                "canonical_id": "water",
                "canonical": "water",
                "aliases": ["h2o"],
                "status": "reviewed",
            }
        ],
    }


def test_promotion_requires_clean_explicitly_reviewed_candidate() -> None:
    candidate = {
        "candidate_id": "c1",
        "values": ["aqua", "water"],
        "evidence": {"level": "cross_model_consensus"},
        "review": {
            "decision": "approve",
            "canonical_id": "water",
            "reviewer": "reviewer",
        },
    }
    released, promoted = promote(
        _registry(),
        {"candidates": [candidate]},
        {"clusters": [{"status": "clean", "candidate_ids": ["c1"]}]},
    )
    assert promoted == ["c1"]
    assert "aqua" in released["species"][0]["aliases"]
    assert released["registry_status"] == "frozen"


def test_promotion_rejects_conflicts_and_hydrate_mismatch() -> None:
    candidate = {
        "candidate_id": "c2",
        "values": ["CuCl2-2H2O", "CuCl2-6H2O"],
        "evidence": {"level": "cross_model_consensus"},
        "review": {
            "decision": "approve",
            "canonical_id": "copper_chloride",
            "reviewer": "reviewer",
        },
    }
    with pytest.raises(ValueError, match="hydrate counts"):
        promote(
            _registry(),
            {"candidates": [candidate]},
            {"clusters": [{"status": "clean", "candidate_ids": ["c2"]}]},
        )


def test_promotion_rejects_acid_anion_collapse() -> None:
    candidate = {
        "candidate_id": "c3",
        "values": ["2-aminoterephthalic acid", "2-aminoterephthalate"],
        "evidence": {"level": "cross_model_consensus"},
        "review": {
            "decision": "approve",
            "canonical_id": "amino_terephthalic_acid",
            "reviewer": "reviewer",
        },
    }
    with pytest.raises(ValueError, match="acid and anion"):
        promote(
            _registry(),
            {"candidates": [candidate]},
            {"clusters": [{"status": "clean", "candidate_ids": ["c3"]}]},
        )
