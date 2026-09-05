from pathlib import Path
from types import SimpleNamespace

import evaluation.scoring_characterisation as scoring
import evaluation.utils.characterisation_field_judge as judge
from evaluation.utils.characterisation_field_judge import (
    FieldJudgeConfig,
    FieldJudgement,
)


def _valid_response(pair_id: str, *, equivalent: bool = True, abbreviated: bool = False) -> dict:
    return {
        "schema_version": judge.SCHEMA_VERSION,
        "judgements": [
            {
                "pair_id": pair_id,
                "equivalent": equivalent,
                "confidence": 0.99,
                "relation": "deuterated_solvent" if equivalent else "unrelated",
                "reason": "Same NMR solvent." if equivalent else "Different solvents.",
                "gt_abbreviated": abbreviated,
                "ground_truth_interpretation": "DMSO" if equivalent else "",
                "prediction_interpretation": "DMSO-d6" if equivalent else "",
            }
        ],
    }


def test_normalize_shifts_matches_abbreviated_and_assigned_lists() -> None:
    abbreviated = "δ = 13.22, 9.61, 8.54, 8.15, 1.23 ppm"
    assigned = (
        "= 13.22 (s, 2H, OH), 9.61 (s, 1H, NH), 8.54 (s, 2H, CH arom), "
        "8.15 (s, 1H, CH arom), 1.23 (s, 6H, CH3)"
    )
    assert scoring._normalize_shifts(abbreviated) == scoring._normalize_shifts(assigned)


def test_judge_field_pairs_validates_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_invoke(model, prompt, **kwargs):
        calls.append((model, prompt, kwargs))
        return SimpleNamespace(data=_valid_response("p0001", abbreviated=True))

    monkeypatch.setattr(judge, "invoke_json", fake_invoke)
    config = FieldJudgeConfig(enabled=True, model="test-model", cache_dir=tmp_path)
    pairs = [("HNMR.solvent", "DMSO-d6", "DMSO", "dmso-d6", "dmso")]

    first = judge.judge_field_pairs(pairs, config)
    second = judge.judge_field_pairs(pairs, config)

    assert first[0].equivalent is True
    assert first[0].gt_abbreviated is True
    assert first[0].source == "llm"
    assert second[0].source == "cache"
    assert len(calls) == 1


def test_score_rescues_solvent_style_with_llm(monkeypatch) -> None:
    def fake_judge(pairs, config):
        return [
            FieldJudgement(
                field_kind="HNMR.solvent",
                ground_truth_value="DMSO-d6",
                prediction_value="DMSO",
                ground_truth_fingerprint="dmso-d6",
                prediction_fingerprint="dmso",
                equivalent=True,
                confidence=0.99,
                relation="deuterated_solvent",
                reason="same NMR solvent",
                gt_abbreviated=True,
                source="llm",
                status="ok",
            )
        ]

    monkeypatch.setattr(scoring, "judge_field_pairs", fake_judge)
    gt = {
        "Devices": [
            {
                "Characterisation": [
                    {
                        "HNMR": {
                            "shifts": "N/A",
                            "solvent": "DMSO-d6",
                            "temperature": "N/A",
                        },
                        "InfraredSpectroscopy": {"bands": "N/A", "material": "N/A"},
                        "productCCDCNumber": "1835131",
                        "productNames": ["Cu24(tBu-amide-bdc)24"],
                    }
                ]
            }
        ]
    }
    pred = {
        "Devices": [
            {
                "Characterisation": [
                    {
                        "HNMR": {
                            "shifts": "N/A",
                            "solvent": "DMSO",
                            "temperature": "N/A",
                        },
                        "InfraredSpectroscopy": {"bands": "N/A", "material": "N/A"},
                        "productCCDCNumber": "1835131",
                        "productNames": ["Cu24(tBu-amide-bdc)24"],
                    }
                ]
            }
        ]
    }
    judgements: list[FieldJudgement] = []
    without_llm = scoring.score_characterisation_fine_grained(gt, pred)
    with_llm = scoring.score_characterisation_fine_grained(
        gt,
        pred,
        field_config=FieldJudgeConfig(enabled=True),
        judgements_out=judgements,
    )

    assert without_llm[1] == 1 and without_llm[2] == 1
    assert with_llm[1] == 0 and with_llm[2] == 0
    assert judgements[0].equivalent is True


def test_revised_chemmater_gt_follows_paper_nmr() -> None:
    path = Path("full_ground_truth/characterisation/10.1021_acs.chemmater.8b01667.json")
    text = path.read_text(encoding="utf-8")
    assert "13.22 (s, 2H, OH)" in text
    assert '"solvent": "DMSO"' in text
    assert "DMSO-d6" not in text
