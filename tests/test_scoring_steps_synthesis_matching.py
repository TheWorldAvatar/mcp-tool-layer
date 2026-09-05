from types import SimpleNamespace

import evaluation.scoring_steps as scoring_steps
import evaluation.utils.product_identity_judge as product_module
from evaluation.scoring_steps import (
    _assign_synthesis_indices,
    _find_best_synthesis_match,
    _synthesis_ccdc,
    _synthesis_identity_score,
    _synthesis_route_score,
    _type_counts_for_objs,
)
from evaluation.utils.step_equivalence_judge import (
    StepEquivalenceConfig,
    StepEquivalenceJudge,
)


def _synthesis(name: str, step_types: list[str], ccdc: str = "183513 1") -> dict:
    return {
        "productCCDCNumber": ccdc,
        "productNames": [name],
        "steps": [{step_type: {}} for step_type in step_types],
    }


def _routed_synthesis(
    names: list[str],
    precursor: str,
    step_types: list[str],
    ccdc: str = "1590349",
) -> dict:
    steps = [
        {
            "Add": {
                "addedChemical": [
                    {"chemicalName": [precursor], "chemicalAmount": "N/A"}
                ]
            }
        }
    ]
    steps.extend({step_type: {}} for step_type in step_types)
    return {
        "productCCDCNumber": ccdc,
        "productNames": names,
        "steps": steps,
    }


def test_shared_product_routes_pair_by_procedure_label_before_step_similarity() -> None:
    gt_me2 = _routed_synthesis(
        [
            "VMOP-alpha",
            "VMOP-alpha from [Me2NH2]5[V6O6(OCH3)9(SO4)4] and H2edb at 120 degC",
        ],
        "[Me2NH2]5[V6O6(OCH3)9(SO4)4]",
        ["Add", "Add", "HeatChill", "Filter"],
    )
    gt_voso4 = _routed_synthesis(
        ["VMOP-alpha", "VMOP-alpha from VOSO4-xH2O and H2edb at 120 degC"],
        "VOSO4-xH2O",
        ["Add", "Add", "HeatChill", "Filter"],
    )
    gt_transform = _routed_synthesis(
        ["VMOP-alpha", "Structural transformation from VMOP-beta to VMOP-alpha"],
        "VMOP-beta",
        ["Add", "Add", "HeatChill"],
    )

    # Deliberately make step similarity favour a three-way cyclic mismatch.
    pred_voso4 = _routed_synthesis(
        ["Synthesis of VMOP-alpha from VOSO4-xH2O and H2edb at 120degC", "VMOP-alpha"],
        # Mirrors the observed extraction contamination: route label is VOSO4,
        # while the materialized precursor belongs to the Me2NH2 route.
        "[Me2NH2]5[V6O6(OCH3)9(SO4)4]",
        ["Add", "HeatChill"],
        ccdc="",
    )
    pred_transform = _routed_synthesis(
        ["Structural transformation from VMOP-beta to VMOP-alpha", "VMOP-alpha"],
        "VMOP-beta",
        ["Add", "Add", "Add", "HeatChill", "Filter"],
        ccdc="",
    )
    pred_me2 = _routed_synthesis(
        ["Synthesis of VMOP-alpha from [Me2NH2]5[V6O6(OCH3)9(SO4)4] and H2edb at 120degC"],
        "[Me2NH2]5[V6O6(OCH3)9(SO4)4]",
        ["Add", "Add", "HeatChill"],
        ccdc="",
    )
    gt_routes = [gt_me2, gt_voso4, gt_transform]
    predictions = [pred_voso4, pred_transform, pred_me2]

    assert _synthesis_route_score(gt_voso4, pred_voso4) > _synthesis_route_score(
        gt_voso4, pred_me2
    )

    matched: set[int] = set()
    paired_indices = []
    for gt_route in gt_routes:
        index, _ = _find_best_synthesis_match(
            gt_route,
            predictions,
            matched,
            gt_synths=gt_routes,
        )
        paired_indices.append(index)
        matched.add(index)

    assert paired_indices == [2, 0, 1]


def test_label_poor_product_does_not_cross_pair_synthesis_and_transformation() -> None:
    gt_synthesis = _routed_synthesis(
        ["VMOP-beta", "VMOP-beta from precursor and linker at 160 degC"],
        "precursor",
        ["Add", "HeatChill"],
        ccdc="1590348",
    )
    gt_transformation = _routed_synthesis(
        ["VMOP-beta", "Structural transformation from VMOP-alpha to VMOP-beta"],
        "VMOP-alpha",
        ["Add", "HeatChill"],
        ccdc="1590348",
    )
    pred_transformation = _routed_synthesis(
        ["Structural transformation from VMOP-alpha to VMOP-beta"],
        "VMOP-alpha",
        ["Add", "HeatChill"],
        ccdc="",
    )
    pred_generic = _routed_synthesis(
        ["VMOP-beta"],
        "precursor",
        ["Add"],
        ccdc="",
    )

    assignment = _assign_synthesis_indices(
        [gt_synthesis, gt_transformation],
        [pred_transformation, pred_generic],
    )

    assert assignment == {0: 1, 1: 0}


def test_global_assignment_prevents_early_route_from_stealing_specific_match() -> None:
    gt_route_a = _routed_synthesis(
        ["Product-alpha", "Product-alpha from precursor-A"],
        "precursor-A",
        ["Add", "HeatChill"],
        ccdc="",
    )
    gt_route_b = _routed_synthesis(
        ["Product-alpha", "Product-alpha from precursor-B"],
        "precursor-B",
        ["Add", "HeatChill"],
        ccdc="",
    )
    pred_route_b = _routed_synthesis(
        ["Product-alpha", "Synthesis of Product-alpha from precursor-B"],
        "precursor-B",
        ["Add", "HeatChill"],
        ccdc="",
    )
    pred_generic = _routed_synthesis(
        ["Product-alpha"],
        "precursor-A",
        ["Add"],
        ccdc="",
    )

    assignment = _assign_synthesis_indices(
        [gt_route_a, gt_route_b],
        [pred_route_b, pred_generic],
    )

    assert assignment == {0: 1, 1: 0}


def test_same_ccdc_prefers_product_and_route_identity_over_step_similarity() -> None:
    gt_mech = _synthesis(
        "Cu24(tBu-amide-bdc)24 via mechanochemical method",
        ["Add", "Add", "Stir", "Add", "Transfer", "Separate"],
    )
    gt_solv = _synthesis(
        "Cu24(tBu-amide-bdc)24 via solvothermal method",
        ["Add", "Add", "Add", "Evaporate"],
    )
    pred_mech = _synthesis(
        "Synthesis of Cu24(tBu-amide-bdc)24 by mechanochemical milling",
        ["Add", "Add", "Add", "Transfer", "Separate"],
    )
    pred_solv = _synthesis(
        "Synthesis of Cu24(tBu-amide-bdc)24 by solvothermal crystallization",
        ["Add", "Add", "Add", "HeatChill"],
    )

    assert _synthesis_identity_score(gt_mech, pred_mech) > _synthesis_identity_score(
        gt_mech, pred_solv
    )
    index, matched = _find_best_synthesis_match(
        gt_mech,
        [pred_solv, pred_mech],
        set(),
    )

    assert index == 1
    assert matched is pred_mech


def test_type_counts_use_identity_first_matching_with_shared_ccdc() -> None:
    gt = {
        "Synthesis": [
            _synthesis(
                "Cu24(tBu-amide-bdc)24 via mechanochemical method",
                ["Add", "Add", "Stir", "Add", "Transfer", "Separate"],
            ),
            _synthesis(
                "Cu24(tBu-amide-bdc)24 via solvothermal method",
                ["Add", "Add", "Add", "Evaporate"],
            ),
            _synthesis(
                "Cu24(H-bdc)24 cage",
                ["Add", "Add", "Stir", "Add", "Transfer", "Separate"],
            ),
        ]
    }
    pred = {
        "Synthesis": [
            _synthesis(
                "Synthesis of Cu24(H-bdc)24 cage by mechanochemical milling",
                ["Add", "Add", "HeatChill", "Add", "Transfer", "Separate"],
            ),
            _synthesis(
                "Synthesis of Cu24(tBu-amide-bdc)24 by mechanochemical milling",
                ["Add", "Add", "Add", "Transfer", "Separate"],
            ),
            _synthesis(
                "Synthesis of Cu24(tBu-amide-bdc)24 by solvothermal crystallization",
                ["Add", "Add", "Add", "HeatChill"],
            ),
        ]
    }

    assert _type_counts_for_objs(gt, pred, skip_order=True) == (13, 2, 3)


def _indexed(name: str) -> dict:
    return {
        "productCCDCNumber": "",
        "productNames": [name],
        "steps": [{"Add": {}}, {"HeatChill": {}}, {"Filter": {}}],
    }


def test_hyphen_suffix_alias_pairs_short_code_without_ccdc() -> None:
    gt = [
        _indexed("TMA-VMOT-2"),
        _indexed("TMA-VMOC-P-2"),
        _indexed("TMA-VMOT-3"),
    ]
    preds = [
        _indexed("TMA-VMOC-P-2"),
        _indexed("VMOT-2"),
        _indexed("TMA-VMOT-3"),
    ]
    index, matched = _find_best_synthesis_match(gt[0], preds, set(), gt_synths=gt)
    assert index == 1
    assert matched is preds[1]


def test_unique_compound_index_pairs_empty_ccdc_without_llm() -> None:
    scoring_steps._ACTIVE_STEP_EQUIVALENCE = StepEquivalenceJudge(
        StepEquivalenceConfig(product_match_enabled=False)
    )
    try:
        gt = [
            {
                "productCCDCNumber": "950330",
                "productNames": ["[Zr3O(OH)3(C5H5)3]4[(C6H4)(CO2)2]6", "ZrT-1"],
                "steps": [{"Add": {}}, {"HeatChill": {}}],
            },
            {
                "productCCDCNumber": "950333",
                "productNames": ["[Zr3O(OH)3(C5H5)3]4[(C6H3)(C6H4)3(CO2)3]4", "ZrT-4"],
                "steps": [{"Add": {}}, {"HeatChill": {}}, {"Filter": {}}],
            },
        ]
        preds = [
            {
                "productCCDCNumber": "",
                "productNames": ["Synthesis of 1", "1"],
                "steps": [{"Add": {}}, {"HeatChill": {}}],
            },
            {
                "productCCDCNumber": "",
                "productNames": ["Synthesis of 4", "{[Cp3Zr3mu3-O(mu2-OH)3]4(BTB)4}4+"],
                "steps": [{"Add": {}}, {"Filter": {}}],
            },
        ]
        index, matched = _find_best_synthesis_match(gt[1], preds, set(), gt_synths=gt)
        assert index == 1
        assert matched is preds[1]
    finally:
        scoring_steps._ACTIVE_STEP_EQUIVALENCE = StepEquivalenceJudge()


def test_ccdc_product_alias_pairs_when_prediction_field_is_empty() -> None:
    gt = _synthesis(
        "cubic polyoxometalate-organic molecular cage",
        ["Add", "HeatChill", "Filter"],
        ccdc="759738",
    )
    pred = _synthesis(
        "CCDC-759738",
        ["Add", "HeatChill"],
        ccdc="",
    )

    assert _synthesis_ccdc(pred) == _synthesis_ccdc(gt)
    assert _assign_synthesis_indices([gt], [pred]) == {0: 0}


def test_ccdc_alias_fallback_rejects_ambiguous_product_names() -> None:
    pred = {
        "productCCDCNumber": "",
        "productNames": ["CCDC 759738", "possible alternative CCDC-759739"],
        "steps": [],
    }

    assert _synthesis_ccdc(pred) == ""


def test_shared_trailing_digit_does_not_cross_pair_vmot_and_vmoc() -> None:
    gt = _indexed("TMA-VMOT-2")
    pred_vmoc = _indexed("TMA-VMOC-P-2")
    pred_vmot = _indexed("VMOT-2")
    index, matched = _find_best_synthesis_match(gt, [pred_vmoc, pred_vmot], set())
    assert index == 1
    assert matched is pred_vmot


def test_conflicting_product_index_overrides_false_positive_llm(
    monkeypatch,
) -> None:
    class AlwaysEquivalent:
        def same_product(self, *_args) -> bool:
            return True

    monkeypatch.setattr(
        scoring_steps,
        "_ACTIVE_STEP_EQUIVALENCE",
        AlwaysEquivalent(),
    )
    gt = _indexed("MOP-1")
    pred_mop_2 = _indexed("MOP-2")
    pred_mop_4 = _indexed("MOP-4")

    index, matched = _find_best_synthesis_match(
        gt,
        [pred_mop_2, pred_mop_4],
        set(),
        gt_synths=[gt],
    )

    assert index == -1
    assert matched is None


def test_fuzzy_match_cannot_steal_another_gt_exact_prediction(
    monkeypatch,
) -> None:
    class AlwaysEquivalent:
        def same_product(self, *_args) -> bool:
            return True

    monkeypatch.setattr(
        scoring_steps,
        "_ACTIVE_STEP_EQUIVALENCE",
        AlwaysEquivalent(),
    )
    gt_missing = _indexed("Unreported target")
    gt_present = _indexed("MOP-2")
    pred_present = _indexed("MOP-2")

    index, matched = _find_best_synthesis_match(
        gt_missing,
        [pred_present],
        set(),
        gt_synths=[gt_missing, gt_present],
    )

    assert index == -1
    assert matched is None


def test_indexed_products_match_parenthetical_alias_without_llm() -> None:
    gt = _indexed("TMA-VMOC-P-2")
    pred_p3 = _indexed(
        "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(ADBDC)3} (TMA-VMOC-P-3)"
    )
    pred_p2 = _indexed(
        "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(NDBDC)3} (TMA-VMOC-P-2)"
    )
    index, matched = _find_best_synthesis_match(gt, [pred_p3, pred_p2], set())
    assert index == 1
    assert matched is pred_p2


def test_short_gt_name_matches_titled_prediction_not_chemical_extension() -> None:
    gt = _indexed("zr-bpydc")
    pred_cucl2 = _indexed("Zr-bpydc-CuCl2 (metalloligand approach)")
    pred_host = _indexed(
        "Zr-bpydc (tetrahedral coordination cage, guest-free host)"
    )
    index, matched = _find_best_synthesis_match(gt, [pred_cucl2, pred_host], set())
    assert index == 1
    assert matched is pred_host


def test_exact_ccdc_outranks_higher_name_similarity() -> None:
    gt = _synthesis("zr-bpydc", ["Add", "HeatChill"], ccdc="1469173")
    pred_cucl2 = _synthesis(
        "Zr-bpydc-CuCl2 (metalloligand approach)",
        ["Add", "Add", "HeatChill"],
        ccdc="1469174",
    )
    pred_host = _synthesis(
        "Zr-bpydc (tetrahedral coordination cage, guest-free host)",
        ["Add", "HeatChill"],
        ccdc="1469173",
    )
    index, matched = _find_best_synthesis_match(gt, [pred_cucl2, pred_host], set())
    assert index == 1
    assert matched is pred_host


def test_llm_product_match_uncrosses_indexed_products(monkeypatch) -> None:
    def fake_invoke(model, prompt, *args, **kwargs):
        payload_text = prompt.split("\n\n", 1)[1]
        import json

        payload = json.loads(payload_text)
        gt_text = " ".join(payload["ground_truth_names"]).casefold()
        pred_text = " ".join(payload["prediction_names"]).casefold()
        same = "p-2" in gt_text and "p-2" in pred_text and "p-3" not in pred_text
        return SimpleNamespace(
            data={
                "schema_version": product_module.SCHEMA_VERSION,
                "equivalent": same,
                "confidence": 0.97,
                "relation": "equivalent" if same else "different",
                "reason": "Product labels match." if same else "Product labels differ.",
            }
        )

    monkeypatch.setattr(product_module, "invoke_json", fake_invoke)
    scoring_steps._ACTIVE_STEP_EQUIVALENCE = StepEquivalenceJudge(
        StepEquivalenceConfig(fast_match_enabled=True)
    )
    try:
        gt = _indexed("TMA-VMOC-P-2")
        pred_p3 = _indexed(
            "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(ADBDC)3} (TMA-VMOC-P-3)"
        )
        pred_p2 = _indexed(
            "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(NDBDC)3} (TMA-VMOC-P-2)"
        )
        index, matched = _find_best_synthesis_match(gt, [pred_p3, pred_p2], set())
        assert index == 1
        assert matched is pred_p2
    finally:
        scoring_steps._ACTIVE_STEP_EQUIVALENCE = StepEquivalenceJudge()
