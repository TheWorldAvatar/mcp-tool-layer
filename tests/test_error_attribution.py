from evaluation.error_attribution import (
    attribute_event,
    cluster_incidents,
    describe_pred_add_with_amount,
    excerpt_paper,
    merge_error_events,
    sanitize_hints,
)


def test_merge_pairs_field_substitution() -> None:
    atoms = [
        {
            "atom_id": "a-fn",
            "hash": "0c57bac8",
            "synth_key": "NAME:umc-1",
            "step_idx": 1,
            "step_type": "Add",
            "field": "addedChemical.amounts",
            "gt_value": "17.5 mg",
            "pred_value": "n/a",
            "status": "fn",
            "product_names": ["umc-1"],
        },
        {
            "atom_id": "a-fp",
            "hash": "0c57bac8",
            "synth_key": "NAME:umc-1",
            "step_idx": 1,
            "step_type": "Add",
            "field": "addedChemical.amounts",
            "gt_value": "17.5 mg",
            "pred_value": "18 mg",
            "status": "fp",
            "product_names": ["umc-1"],
        },
        {
            "atom_id": "b-tp",
            "hash": "0c57bac8",
            "synth_key": "NAME:umc-1",
            "step_idx": 1,
            "step_type": "Add",
            "field": "duration",
            "gt_value": "n/a",
            "pred_value": "n/a",
            "status": "tp",
            "product_names": ["umc-1"],
        },
    ]
    events = merge_error_events(atoms)
    substitutions = [event for event in events if event["e2e"] == "substitution"]
    tps = [event for event in events if event["e2e"] == "tp"]
    assert len(substitutions) == 1
    assert len(tps) == 1
    assert substitutions[0]["atom_ids"] == ["a-fn", "a-fp"]


def test_attribution_tree() -> None:
    fn = {"e2e": "fn", "pairing": "matched"}
    assert attribute_event(fn, "yes", "n/a") == "kg_drop"
    assert attribute_event(fn, "no", "n/a") == "extraction_miss"
    assert attribute_event(fn, "partial", "n/a") == "extraction_wrong"
    fp = {"e2e": "fp", "pairing": "matched"}
    assert attribute_event(fp, "n/a", "yes") == "extraction_hallucination"
    assert attribute_event(fp, "n/a", "no") == "kg_invent"
    sub = {"e2e": "substitution", "pairing": "matched"}
    assert attribute_event(sub, "yes", "no") == "kg_corrupt"
    assert attribute_event(sub, "no", "yes") == "extraction_wrong"
    assert attribute_event(sub, "no", "no") == "both"
    assert attribute_event(sub, "yes", "yes") == "extraction_ambiguous"


def test_sanitize_collapses_alias_wall() -> None:
    text = (
        'The chemical input is DMF, with alternative names including DMF; '
        + "; ".join(f"alias{i}" for i in range(40))
        + ". The chemical formula is C3H7NO. The amount used is 1 mL."
    )
    cleaned = sanitize_hints(text)
    assert "alias6" in cleaned
    assert "alias20" not in cleaned
    assert "aliases omitted" in cleaned
    assert "1 mL" in cleaned


def _event(**kwargs):
    base = {
        "hash": "0c57bac8",
        "synth_key": "157689 7",
        "product_names": ["umc-1"],
        "step_type": "Add",
        "pairing": "matched",
        "matched_step": False,
        "informative": True,
        "gt_in_hints": "yes",
        "pred_in_hints": "yes",
        "attribution": "kg_drop",
    }
    base.update(kwargs)
    base.setdefault("event_id", f"ev:{base['step_idx']}:{base['field']}:{base['e2e']}")
    return base


def test_cluster_merges_unmatched_add_with_same_amount() -> None:
    events = [
        _event(
            step_idx=2,
            field="addedChemical.amounts",
            e2e="fn",
            gt_value="0.03 mmol, 9.20 mg",
            pred_value=[],
            attribution="kg_drop",
        ),
        _event(
            step_idx=2,
            field="atmosphere",
            e2e="fn",
            gt_value="n/a",
            pred_value=None,
            informative=False,
            attribution="kg_drop",
        ),
        _event(
            step_idx=8,
            field="addedChemical.amounts",
            e2e="fp",
            gt_value=[],
            pred_value="0.03 mmol, 9.20 mg",
            attribution="kg_invent",
        ),
        _event(
            step_idx=8,
            field="stir",
            e2e="fp",
            gt_value=None,
            pred_value=False,
            informative=False,
            attribution="kg_invent",
        ),
    ]
    incidents = cluster_incidents(events)
    assert len(incidents) == 1
    assert incidents[0]["stage"] == "scorer_pairing"
    assert incidents[0]["n_atoms"] == 4


def test_excerpt_paper_finds_synthesis_line() -> None:
    paper = (
        "Synthesis of UMC-1. Cp2ZrCl2 (17.5 mg, 0.06 mmol) and H2SDB "
        "(9.2 mg, 0.03 mmol) were dissolved in DMF (1 mL)."
    )
    snippet = excerpt_paper(paper, ["H2SDB", "9.2 mg"])
    assert "H2SDB" in snippet
    assert "9.2 mg" in snippet


def test_cluster_does_not_pair_generic_volumes() -> None:
    events = [
        _event(
            step_idx=3,
            field="addedChemical.amounts",
            e2e="fn",
            gt_value="1 ml",
            pred_value=[],
        ),
        _event(
            step_idx=9,
            field="addedChemical.amounts",
            e2e="fp",
            gt_value=[],
            pred_value="1 ml",
            attribution="kg_invent",
        ),
    ]
    incidents = cluster_incidents(events)
    assert all(item.get("stage") != "scorer_pairing" for item in incidents)


def test_cluster_merges_missing_workup_steps() -> None:
    events = [
        _event(
            step_idx=5,
            step_type="Stir",
            field="duration",
            e2e="fn",
            gt_value="20 min",
            pred_value=None,
            attribution="extraction_miss",
        ),
        _event(
            step_idx=8,
            step_type="Filter",
            field="numberOfFiltrations",
            e2e="fn",
            gt_value=1,
            pred_value=None,
            attribution="extraction_miss",
        ),
        _event(
            step_idx=9,
            step_type="Evaporate",
            field="duration",
            e2e="fn",
            gt_value="1176 h",
            pred_value=None,
            attribution="extraction_miss",
        ),
        _event(
            step_idx=10,
            step_type="Filter",
            field="washingSolvent.names",
            e2e="fn",
            gt_value="ethanol",
            pred_value=[],
            attribution="kg_drop",
        ),
    ]
    incidents = cluster_incidents(events)
    assert len(incidents) == 1
    assert incidents[0]["title"] == "workup unmatched"
    assert incidents[0]["stage"] == "extraction"


def test_describe_pred_add_uses_abox_names() -> None:
    pred = {
        "Synthesis": [
            {
                "steps": [
                    {
                        "Add": {
                            "addedChemical": [
                                {
                                    "chemicalName": ["4,4'-sulfonyldibenzoic acid"],
                                    "chemicalAmount": "9.2 mg, 0.03 mmol",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    text = describe_pred_add_with_amount(pred, "0.03 mmol, 9.20 mg")
    assert "4,4'-sulfonyldibenzoic acid" in text
    assert "9.2 mg" in text
