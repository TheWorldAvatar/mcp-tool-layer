from pathlib import Path
from types import SimpleNamespace

import evaluation.scoring_chemicals as scoring
import evaluation.utils.chemical_synonym_judge as judge
from evaluation.utils.chemical_synonym_judge import (
    SynonymJudgeConfig,
    SynonymJudgement,
)


def _valid_response(pair_id: str, *, equivalent: bool = True) -> dict:
    return {
        "schema_version": judge.SCHEMA_VERSION,
        "judgements": [
            {
                "pair_id": pair_id,
                "equivalent": equivalent,
                "confidence": 0.99,
                "relation": "abbreviation" if equivalent else "unrelated",
                "reason": "Both names denote the same exact species."
                if equivalent
                else "The names denote different species.",
                "ground_truth_interpretation": "same species" if equivalent else "",
                "prediction_interpretation": "same species" if equivalent else "",
            }
        ],
    }


def test_judge_pairs_validates_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_invoke(model, prompt, **kwargs):
        calls.append((model, prompt, kwargs))
        return SimpleNamespace(data=_valid_response("p0001"))

    monkeypatch.setattr(judge, "invoke_json", fake_invoke)
    config = SynonymJudgeConfig(
        enabled=True,
        model="test-model",
        cache_dir=tmp_path,
    )
    pairs = [("dimethylformamide", "DMF", "dimethylformamide", "dmf")]

    first = judge.judge_pairs(pairs, config)
    second = judge.judge_pairs(pairs, config)

    assert first[0].equivalent is True
    assert first[0].source == "llm"
    assert second[0].equivalent is True
    assert second[0].source == "cache"
    assert len(calls) == 1


def test_judge_pairs_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        judge,
        "invoke_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    result = judge.judge_pairs(
        [("compound a", "compound b", "compound a", "compound b")],
        SynonymJudgeConfig(enabled=True, cache_dir=tmp_path),
    )

    assert result[0].equivalent is False
    assert result[0].status == "error"


def test_name_scoring_adds_only_one_to_one_validated_synonyms(monkeypatch) -> None:
    def fake_judge_pairs(pairs, config):
        rows = []
        for gt_name, pred_name, gt_fp, pred_fp in pairs:
            rows.append(
                SynonymJudgement(
                    ground_truth_name=gt_name,
                    prediction_name=pred_name,
                    ground_truth_fingerprint=gt_fp,
                    prediction_fingerprint=pred_fp,
                    equivalent=gt_fp == "dimethylformamide" and pred_fp == "dmf",
                    confidence=0.99,
                    relation="abbreviation",
                    reason="exact abbreviation",
                    source="llm",
                    status="ok",
                )
            )
        return rows

    monkeypatch.setattr(scoring, "judge_pairs", fake_judge_pairs)
    counts = scoring._score_name_lists(
        ["dimethylformamide", "unmatched reagent"],
        ["DMF"],
        SynonymJudgeConfig(enabled=True),
    )

    assert counts[:3] == (1, 0, 1)
    # The shared reviewed registry resolves DMF deterministically.
    assert counts[4] == 0


def test_invalid_positive_confidence_is_rejected() -> None:
    payload = _valid_response("p0001")
    payload["judgements"][0]["confidence"] = 0.5

    try:
        judge._validate_payload(payload, [{"pair_id": "p0001"}])
    except ValueError as exc:
        assert "sufficiently certain" in str(exc)
    else:
        raise AssertionError("low-confidence positive judgement must be rejected")
