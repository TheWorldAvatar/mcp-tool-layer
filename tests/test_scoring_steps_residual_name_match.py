from evaluation.scoring_steps import (
    _find_best_add_match,
    _match_equivalent_values,
    _prefetch_score_equivalence,
    chemical_name_fuzzy_score,
    iter_llm_match_candidates,
)
from evaluation.utils.fast_field_match_judge import deterministic_species_match


def test_quantity_in_name_locks_as_tp_without_llm(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def record(field_name: str, gt_value: str, pr_value: str) -> bool:
        calls.append((gt_value, pr_value))
        return False

    monkeypatch.setattr("evaluation.scoring_steps._values_equivalent", record)
    unmatched_gt, unmatched_pr, matched = _match_equivalent_values(
        {"DEF", "VOSO4"},
        {"5 mL DEF", "hexane"},
        "addedChemical.names",
    )
    assert matched == 1
    assert "DEF" not in unmatched_gt
    assert "5 mL DEF" not in unmatched_pr
    assert calls == [("VOSO4", "hexane")]


def test_large_residual_asks_fuzzy_top_k_only(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def record(field_name: str, gt_value: str, pr_value: str) -> bool:
        calls.append((gt_value, pr_value))
        return False

    monkeypatch.setattr("evaluation.scoring_steps._values_equivalent", record)
    gt = {f"Ligand-{index:02d}" for index in range(10)}
    pr = {f"Solvent-{index:02d}" for index in range(10)}
    _match_equivalent_values(gt, pr, "addedChemical.names")
    assert len(calls) == 30
    asked_for_first = {pred for gt_name, pred in calls if gt_name == "Ligand-00"}
    assert len(asked_for_first) == 3


def test_iter_candidates_omits_locked_tps() -> None:
    pairs = iter_llm_match_candidates(
        {"DEF", "water"},
        {"5 mL DEF", "methanol"},
        "addedChemical.names",
    )
    assert pairs == [("water", "methanol")]


def test_iter_candidates_omits_reviewed_registry_aliases() -> None:
    pairs = iter_llm_match_candidates(
        {"copper(II) nitrate hexahydrate", "water"},
        {"Cu(NO3)2·6H2O", "methanol"},
        "addedChemical.names",
    )
    assert pairs == [("water", "methanol")]


def test_fuzzy_score_prefers_quantity_variant() -> None:
    assert chemical_name_fuzzy_score("DEF", "5 mL DEF") == 1.0
    assert chemical_name_fuzzy_score("DEF", "5 mL DEF") > chemical_name_fuzzy_score(
        "DEF", "vanadyl sulfate"
    )


def test_definite_hydrate_alias_survives_broken_middle_dot() -> None:
    assert deterministic_species_match(
        "CuCl2\ufffd2H2O",
        "copper chloride (CuCl2) dihydrate",
    )


def test_definite_hydrate_does_not_collapse_to_anhydrous_species() -> None:
    assert not deterministic_species_match("CuCl2\ufffd2H2O", "CuCl2")


def test_add_matching_uses_equivalent_definite_hydrate_alias() -> None:
    gt = {
        "addedChemical": [
            {
                "chemicalName": ["[Cu2]", "CuCl2\ufffd2H2O"],
                "chemicalAmount": "0.30 mmol, 0.05 g",
            }
        ]
    }
    prediction = {
        "addedChemical": [
            {
                "chemicalName": [
                    "CuCl2",
                    "copper chloride (CuCl2) dihydrate",
                ],
                "chemicalAmount": "0.30 mmol, 0.05 g",
            }
        ]
    }

    best_index, overlap = _find_best_add_match(gt, [(1, prediction)])

    assert best_index == 0
    assert overlap == 1


def test_score_prefetch_collects_residual_pairs_without_invoking_llm(
    monkeypatch,
) -> None:
    class StubJudge:
        config = type("Config", (), {"enabled": True, "fast_match_enabled": False})()

        def __init__(self) -> None:
            self.prefetched = []

        def prefetch(self, pairs) -> int:
            self.prefetched = list(pairs)
            return 0

        def prefetch_products(self, _pairs) -> None:
            return None

        def cached_equivalent(self, *args):
            return None

        def same_product(self, ground_truth_names, prediction_names) -> bool:
            return False

    judge = StubJudge()
    monkeypatch.setattr("evaluation.scoring_steps._ACTIVE_STEP_EQUIVALENCE", judge)
    gt = {
        "Synthesis": [
            {
                "productCCDCNumber": "123",
                "steps": [
                    {
                        "Add": {
                            "addedChemical": [
                                {
                                    "chemicalName": ["water"],
                                    "chemicalAmount": "1 mL",
                                }
                            ]
                        }
                    }
                ],
            }
        ]
    }
    prediction = {
        "Synthesis": [
            {
                "productCCDCNumber": "123",
                "steps": [
                    {
                        "Add": {
                            "addedChemical": [
                                {
                                    "chemicalName": ["methanol"],
                                    "chemicalAmount": "1 mL",
                                }
                            ]
                        }
                    }
                ],
            }
        ]
    }

    _prefetch_score_equivalence(
        gt,
        prediction,
        ignore_vessel=False,
        skip_order=False,
    )

    assert (
        "chemical_name",
        "addedChemical.names",
        "water",
        "methanol",
    ) in judge.prefetched
