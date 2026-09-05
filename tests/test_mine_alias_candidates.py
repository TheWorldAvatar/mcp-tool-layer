import json
from pathlib import Path

from scripts.mine_alias_candidates import mine_candidates


def _write_step_cache(
    path: Path,
    *,
    model: str,
    field_kind: str,
    left: str,
    right: str,
    equivalent: bool,
    confidence: float,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "step-field-equivalence-judge.v1",
                "policy_version": "test-policy",
                "model": model,
                "field_kind": field_kind,
                "field_name": (
                    "chemicalName" if field_kind == "chemical_name" else "atmosphere"
                ),
                "ground_truth_value": left,
                "prediction_value": right,
                "judgement": {
                    "equivalent": equivalent,
                    "confidence": confidence,
                    "relation": "equivalent" if equivalent else "different",
                    "reason": "test",
                },
            }
        ),
        encoding="utf-8",
    )


def test_miner_groups_cross_model_evidence_and_skips_reviewed_aliases(
    tmp_path: Path,
) -> None:
    _write_step_cache(
        tmp_path / "one.json",
        model="gpt-4o",
        field_kind="chemical_name",
        left="H2BPDC",
        right="4,4'-biphenyldicarboxylic acid",
        equivalent=True,
        confidence=0.96,
    )
    _write_step_cache(
        tmp_path / "two.json",
        model="gpt-5",
        field_kind="chemical_name",
        left="4,4'-biphenyldicarboxylic acid",
        right="H2BPDC",
        equivalent=True,
        confidence=0.99,
    )
    _write_step_cache(
        tmp_path / "reviewed.json",
        model="gpt-5",
        field_kind="atmosphere",
        left="argon atmosphere",
        right="argon",
        equivalent=True,
        confidence=0.99,
    )

    chemical, fields = mine_candidates([tmp_path])

    assert len(chemical) == 1
    assert chemical[0]["evidence"]["level"] == "cross_model_consensus"
    assert chemical[0]["evidence"]["positive_models"] == ["gpt-4o", "gpt-5"]
    assert chemical[0]["review"]["status"] == "pending_review"
    assert fields == []


def test_miner_marks_conflicts_and_ignores_negative_only_pairs(tmp_path: Path) -> None:
    _write_step_cache(
        tmp_path / "positive.json",
        model="gpt-5",
        field_kind="chemical_name",
        left="ligand alpha",
        right="LA",
        equivalent=True,
        confidence=0.95,
    )
    _write_step_cache(
        tmp_path / "conflict.json",
        model="gpt-4o",
        field_kind="chemical_name",
        left="ligand alpha",
        right="LA",
        equivalent=False,
        confidence=0.8,
    )
    _write_step_cache(
        tmp_path / "negative.json",
        model="gpt-4o",
        field_kind="chemical_name",
        left="water",
        right="hexane",
        equivalent=False,
        confidence=0.99,
    )

    chemical, _ = mine_candidates([tmp_path])

    assert len(chemical) == 1
    assert chemical[0]["evidence"]["level"] == "conflicted"
    assert chemical[0]["evidence"]["negative_models"] == ["gpt-4o"]
