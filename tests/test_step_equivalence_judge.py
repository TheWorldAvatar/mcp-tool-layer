from types import SimpleNamespace

import pytest

import evaluation.scoring_steps as scoring_steps
import evaluation.utils.step_equivalence_judge as judge_module
from evaluation.utils.step_equivalence_judge import (
    StepEquivalenceConfig,
    StepEquivalenceJudge,
)


def test_step_equivalence_is_deterministic_first_and_exclusions_fail_closed(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []

    def fake_invoke(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            data={
                "schema_version": judge_module.SCHEMA_VERSION,
                "equivalent": True,
                "confidence": 0.97,
                "relation": "equivalent",
                "reason": "Exact field-specific paraphrase.",
            }
        )

    monkeypatch.setattr(judge_module, "invoke_json", fake_invoke)
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=True, cache_dir=tmp_path)
    )

    assert judge.equivalent("chemical_name", "addedChemical.names", " CuCl2 ", "cucl2")
    assert not judge.equivalent("qualitative", "comment", "N/A", "not available")
    assert not judge.equivalent("qualitative", "stir", True, "yes")
    assert not judge.equivalent("quantity", "duration", "5 hours", "6 hours")
    assert calls == []

    assert judge.equivalent("atmosphere", "atmosphere", "nitrogen", "N2")
    assert len(calls) == 1


def test_reviewed_alias_registry_avoids_llm_calls(monkeypatch, tmp_path) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("reviewed aliases should be resolved deterministically")

    monkeypatch.setattr(judge_module, "invoke_json", fail_if_called)
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=True, cache_dir=tmp_path)
    )

    assert judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "copper(II) nitrate hexahydrate",
        "Cu(NO3)2·6H2O",
    )
    assert judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "sodium tungstate dihydrate",
        "Na2WO4-2H2O",
    )
    assert judge.equivalent(
        "atmosphere",
        "atmosphere",
        "argon atmosphere",
        "argon",
    )


def test_reviewed_alias_registry_preserves_hydrate_identity(tmp_path) -> None:
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=False, cache_dir=tmp_path)
    )

    assert not judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "copper(II) nitrate pentahydrate",
        "Cu(NO3)2·6H2O",
    )
    assert not judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "Cu(OAc)2",
        "Cu(OAc)2·H2O",
    )


def test_step_equivalence_cache_avoids_reinvocation(monkeypatch, tmp_path) -> None:
    def fake_invoke(*args, **kwargs):
        return SimpleNamespace(
            data={
                "schema_version": judge_module.SCHEMA_VERSION,
                "equivalent": True,
                "confidence": 0.99,
                "relation": "equivalent",
                "reason": "Same device description.",
            }
        )

    monkeypatch.setattr(judge_module, "invoke_json", fake_invoke)
    config = StepEquivalenceConfig(enabled=True, cache_dir=tmp_path)
    assert StepEquivalenceJudge(config).equivalent(
        "device", "usedVesselType", "PTFE-lined autoclave", "Teflon-lined autoclave"
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("valid persistent cache should be reused")

    monkeypatch.setattr(judge_module, "invoke_json", fail_if_called)
    assert StepEquivalenceJudge(config).equivalent(
        "device", "usedVesselType", "PTFE-lined autoclave", "Teflon-lined autoclave"
    )


def test_step_equivalence_failure_is_closed_and_required_can_abort(
    monkeypatch, tmp_path
) -> None:
    def broken_invoke(*args, **kwargs):
        raise TimeoutError("test timeout")

    monkeypatch.setattr(judge_module, "invoke_json", broken_invoke)
    assert not StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=True, cache_dir=tmp_path)
    ).equivalent("qualitative", "duration", "overnight", "through the night")

    with pytest.raises(RuntimeError, match="step field equivalence judge failed"):
        StepEquivalenceJudge(
            StepEquivalenceConfig(
                enabled=True,
                cache_dir=tmp_path,
                required=True,
            )
        ).equivalent("qualitative", "duration", "overnight", "through the night")


class _MappingJudge:
    def equivalent(self, field_kind, field_name, gt_value, prediction_value):
        normalized = (str(gt_value).casefold(), str(prediction_value).casefold())
        accepted = {
            ("dimethylformamide", "dmf"),
            ("5 ml", "5 millilitres"),
            ("nitrogen", "n2"),
        }
        return gt_value == prediction_value or normalized in accepted


def test_primary_add_matching_and_error_report_share_equivalence(monkeypatch) -> None:
    monkeypatch.setattr(
        scoring_steps, "_ACTIVE_STEP_EQUIVALENCE", _MappingJudge()
    )
    gt_add = {
        "addedChemical": [
            {
                "chemicalName": ["dimethylformamide"],
                "chemicalAmount": "5 mL",
            }
        ],
        "atmosphere": "nitrogen",
    }
    wrong_add = {
        "addedChemical": [
            {"chemicalName": ["water"], "chemicalAmount": "5 millilitres"}
        ],
        "atmosphere": "N2",
    }
    equivalent_add = {
        "addedChemical": [
            {"chemicalName": ["DMF"], "chemicalAmount": "5 millilitres"}
        ],
        "atmosphere": "N2",
    }

    best_index, overlap = scoring_steps._find_best_add_match(
        gt_add, [(0, wrong_add), (1, equivalent_add)]
    )
    assert (best_index, overlap) == (1, 1)
    assert scoring_steps._compare_step_fields(
        gt_add, equivalent_add, "Add"
    ) == (3, 0, 0)

    gt = {
        "Synthesis": [
            {
                "productCCDCNumber": "123",
                "productNames": ["product"],
                "steps": [{"Add": gt_add}],
            }
        ]
    }
    pred = {
        "Synthesis": [
            {
                "productCCDCNumber": "123",
                "productNames": ["product"],
                "steps": [{"Add": equivalent_add}],
            }
        ]
    }
    assert scoring_steps.score_steps_fine_grained(gt, pred)[:3] == (3, 0, 0)
    field_errors, detailed = scoring_steps._analyze_errors_by_field(
        gt, pred, collect_details=True
    )
    assert field_errors == {}
    assert detailed == {}


def test_boolean_never_matches_numeric_value() -> None:
    assert scoring_steps._compare_step_fields(
        {"sealedVessel": True},
        {"sealedVessel": 1},
        "HeatChill",
    ) == (0, 1, 1)


@pytest.mark.parametrize(
    ("field_name", "ground_truth", "prediction"),
    [
        ("duration", "three days", "3 d"),
        ("duration", "one hundred and twenty minutes", "2 h"),
        ("targetTemperature", "75°C", "75 degree celsius"),
        ("targetTemperature", "32 degree fahrenheit", "0 degree celsius"),
        ("targetTemperature", "273.15 K", "0°C"),
        ("duration", "60 min", "1 h"),
        ("chemicalAmount", "1000 mg", "1 g"),
        ("chemicalAmount", "1 L", "1000 mL"),
        ("chemicalAmount", "1000 umol", "1 mmol"),
        ("chemicalAmount", "1000 mg (1 mmol)", "1 g; 0.001 mol"),
        ("chemicalAmount", "130 µL", "130 muL"),
        ("chemicalAmount", "50 µL", "50 ul"),
        ("chemicalAmount", "0.023 g, 0.108 mmol", "0.023, 0.108 mmol"),
        ("heatingCoolingRate", "60 degC/h", "1 degree celsius per minute"),
        ("heatingCoolingRate", "1.8 degF/min", "1 degree celsius per minute"),
    ],
)
def test_quantity_units_are_deterministically_equivalent(
    monkeypatch, field_name, ground_truth, prediction
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("deterministic quantity equivalence must not call LLM")

    monkeypatch.setattr(judge_module, "invoke_json", fail_if_called)
    judge = StepEquivalenceJudge(StepEquivalenceConfig(enabled=True))
    assert judge.equivalent("quantity", field_name, ground_truth, prediction)


@pytest.mark.parametrize(
    ("field_name", "ground_truth", "prediction"),
    [
        ("duration", "two days", "3 d"),
        ("targetTemperature", "75°C", "76 degree celsius"),
        ("chemicalAmount", "1 g", "1001 mg"),
        ("heatingCoolingRate", "60 degC/h", "2 degree celsius per minute"),
        ("duration", "1 month", "30 d"),
        ("duration", "1 month", "4 wk"),
        (
            "chemicalAmount",
            "5 mg in 10 mL total volume",
            "6 mg; total volume 10 mL",
        ),
        ("chemicalAmount", "1 g; 2 g", "3 g"),
        ("chemicalAmount", "0.023 g, 0.108 mmol", "0.023 g"),
    ],
)
def test_different_explicit_quantities_fail_closed_without_llm(
    monkeypatch, field_name, ground_truth, prediction
) -> None:
    calls = []

    def unexpected_invoke(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("different explicit quantities must not call LLM")

    monkeypatch.setattr(judge_module, "invoke_json", unexpected_invoke)
    judge = StepEquivalenceJudge(StepEquivalenceConfig(enabled=True))
    assert not judge.equivalent("quantity", field_name, ground_truth, prediction)
    assert calls == []


def test_unsafe_mixed_quantity_keeps_strict_llm_fallback(
    monkeypatch, tmp_path
) -> None:
    calls = []

    def fake_invoke(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            data={
                "schema_version": judge_module.SCHEMA_VERSION,
                "equivalent": True,
                "confidence": 0.95,
                "relation": "equivalent",
                "reason": "The same two quantities are stated in a different layout.",
            }
        )

    monkeypatch.setattr(judge_module, "invoke_json", fake_invoke)
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=True, cache_dir=tmp_path)
    )
    assert judge.equivalent(
        "quantity",
        "chemicalAmount",
        "5 mg in 10 mL total volume",
        "5 mg; total volume 10 mL",
    )
    assert len(calls) == 1


def test_quantity_in_chemical_name_fails_closed_by_default(monkeypatch) -> None:
    calls = []

    def unexpected_invoke(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("quantity-in-name must fail closed unless fast match is on")

    monkeypatch.setattr(judge_module, "invoke_json", unexpected_invoke)
    judge = StepEquivalenceJudge(StepEquivalenceConfig(enabled=True))
    assert not judge.equivalent("chemical_name", "addedChemical.names", "DEF", "5 mL DEF")
    assert not judge.equivalent(
        "chemical_name", "addedChemical.names", "H2TEI", "H2TEI (100 mg)"
    )
    assert calls == []


def test_fast_match_accepts_quantity_annotation_without_llm(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("stripped quantity annotations must not call LLM")

    monkeypatch.setattr(judge_module, "invoke_json", fail_if_called)
    import evaluation.utils.fast_field_match_judge as fast_module

    monkeypatch.setattr(fast_module, "invoke_json", fail_if_called)
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=False, fast_match_enabled=True)
    )
    assert judge.equivalent("chemical_name", "addedChemical.names", "DEF", "5 mL DEF")
    assert judge.equivalent(
        "chemical_name", "addedChemical.names", "H2TEI", "H2TEI (100 mg)"
    )
    assert judge.equivalent(
        "chemical_name", "addedChemical.names", "DMF", "DMF (5.0 mL)"
    )
    assert judge.equivalent(
        "chemical_name", "addedChemical.names", "VOSO4xxH2O", "VOSO4-xH2O"
    )


def test_fast_match_llm_rejects_hydrate_and_accepts_leftover_same_species(
    monkeypatch, tmp_path
) -> None:
    calls = []

    def fake_invoke(model, prompt, *args, **kwargs):
        calls.append(model)
        if "CuCl2" in prompt or "CH3OH" in prompt:
            equivalent = False
            relation = "different"
            reason = "Identity changed by hydrate or mixed solvents."
        else:
            equivalent = True
            relation = "equivalent"
            reason = "The extra tokens are only a quantity annotation."
        return SimpleNamespace(
            data={
                "schema_version": "fast-field-match-judge.v1",
                "equivalent": equivalent,
                "confidence": 0.96,
                "relation": relation,
                "reason": reason,
            }
        )

    import evaluation.utils.fast_field_match_judge as fast_module

    monkeypatch.setattr(fast_module, "invoke_json", fake_invoke)
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(
            enabled=True,
            cache_dir=tmp_path / "synonym",
            fast_match_enabled=True,
            fast_match_cache_dir=tmp_path / "fast",
        )
    )
    assert not judge.equivalent(
        "chemical_name", "addedChemical.names", "CuCl2", "CuCl2·2H2O"
    )
    assert not judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "DMF",
        "2.5mL mixture of DMF/CH3OH (v/v: 1:2)",
    )
    assert judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "zirconocene dichloride",
        "Cp2ZrCl2, 15 mg",
    )
    # The reviewed zirconocene alias is deterministic; only the two rejected
    # identity-changing pairs reach the fast LLM judge.
    assert calls == ["gpt-4o", "gpt-4o"]


def test_step_equivalence_known_aliases_are_deterministic(monkeypatch, tmp_path) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("known aliases must not call the LLM judge")

    monkeypatch.setattr(judge_module, "invoke_json", fail_if_called)
    judge = StepEquivalenceJudge(
        StepEquivalenceConfig(enabled=True, cache_dir=tmp_path)
    )
    assert judge.equivalent("chemical_name", "addedChemical.names", "triethylamine", "TEA")
    assert judge.equivalent("chemical_name", "addedChemical.names", "Et2O", "diethyl ether")
    assert judge.equivalent(
        "chemical_name", "addedChemical.names", "H2BDC", "terephthalic acid"
    )
    assert judge.equivalent(
        "chemical_name", "addedChemical.names", "cu(oac)2·h2o", "Cu(OAc)2-H2O"
    )
    assert judge.equivalent(
        "chemical_name",
        "addedChemical.names",
        "H2BTB",
        "1,3,5-tris(4-carboxyphenyl)-benzene (H2BTB)",
    )
    assert any(
        "h2bdc" in line.casefold() and "terephthalic" in line.casefold()
        for line in judge_module._POLICY
    )
