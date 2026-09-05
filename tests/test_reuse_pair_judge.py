from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.scripts_and_prompts_generation import reuse_pair_judge as judge


def _request(pair_id: str = "p0001") -> dict:
    return {
        "pair_id": pair_id,
        "class_iri": "https://example.test/Entity",
        "class_local": "Entity",
        "class_contract": {
            "comment": "A context-independent real-world entity.",
            "datatype_properties": {"identifier": "string"},
            "object_properties": {},
        },
        "reuse_policy": {
            "reuse_scope": "global",
            "match_basis": "same stable identifier",
        },
        "current_context": {"doi": "new-document", "top_level_entity_name": "top"},
        "proposed_entity": {"label": "Example", "identifier": "ID-1"},
        "candidate_entity": {
            "iri": "https://example.test/entity/1",
            "labels": ["Example"],
            "datatype_values": {"identifier": [{"value": "ID-1"}]},
            "central_provenance": [{"doi": "old-document"}],
        },
    }


def _response(pair_id: str, *, authorized: bool = True) -> dict:
    return {
        "schema_version": judge.SCHEMA_VERSION,
        "judgements": [
            {
                "pair_id": pair_id,
                "reuse_authorized": authorized,
                "same_real_world_entity": authorized,
                "context_independent_identity": authorized,
                "match_basis_satisfied": authorized,
                "confidence": 0.99 if authorized else 0.4,
                "reason": "Stable identifiers establish the same entity."
                if authorized
                else "Identity is not established.",
                "evidence_used": ["identifier ID-1"]
                if authorized
                else ["insufficient identity evidence"],
            }
        ],
    }


def test_reuse_judge_caches_valid_structured_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_invoke(*_args, **_kwargs):
        calls.append(True)
        return SimpleNamespace(data=_response("p0001"))

    monkeypatch.setattr(judge, "invoke_json", fake_invoke)
    config = judge.ReuseJudgeConfig(
        model="test-model",
        cache_dir=tmp_path / "cache",
        audit_dir=tmp_path / "audit",
    )

    first = judge.judge_reuse_pairs([_request()], config)
    second = judge.judge_reuse_pairs([_request()], config)

    assert first == second
    assert first[0]["reuse_authorized"] is True
    assert len(calls) == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert len(list((tmp_path / "audit").rglob("*.json"))) == 2


def test_reuse_judge_retries_semantically_invalid_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = _response("p0001")
    invalid["judgements"][0]["confidence"] = 0.5
    responses = iter([invalid, _response("p0001")])

    monkeypatch.setattr(
        judge,
        "invoke_json",
        lambda *_args, **_kwargs: SimpleNamespace(data=next(responses)),
    )
    result = judge.judge_reuse_pairs(
        [_request()],
        judge.ReuseJudgeConfig(
            model="test-model",
            cache_dir=tmp_path / "cache",
            max_semantic_attempts=2,
        ),
    )

    assert result[0]["reuse_authorized"] is True


def test_reuse_judge_fails_closed_when_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        judge,
        "invoke_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    result = judge.judge_reuse_pairs(
        [_request()],
        judge.ReuseJudgeConfig(
            model="test-model",
            cache_dir=tmp_path / "cache",
            max_semantic_attempts=1,
            required=False,
        ),
    )

    assert result[0]["reuse_authorized"] is False
    assert result[0]["confidence"] == 0.0


def test_validation_rejects_authorization_without_generic_identity_gates() -> None:
    payload = _response("p0001")
    payload["judgements"][0]["context_independent_identity"] = False

    with pytest.raises(ValueError, match="authorization gate"):
        judge._validate_payload(payload, [_request()], 0.95)


def test_reuse_judge_cache_supports_long_windows_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        judge,
        "invoke_json",
        lambda *_args, **_kwargs: SimpleNamespace(data=_response("p0001")),
    )
    long_root = tmp_path / ("a" * 80) / ("b" * 80)
    result = judge.judge_reuse_pairs(
        [_request()],
        judge.ReuseJudgeConfig(
            model="test-model",
            cache_dir=long_root,
            audit_dir=long_root / "audit",
        ),
    )

    assert result[0]["reuse_authorized"] is True
    assert len(list(long_root.glob("*.json"))) == 1
