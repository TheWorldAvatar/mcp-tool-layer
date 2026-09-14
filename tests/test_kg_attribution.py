"""Extraction vs KG attribution: ledger-conditioned stage tags and counterfactual F1."""

from __future__ import annotations

import json
from pathlib import Path

from src.kg_building.attribution.atoms import (
    MistakeAtom,
    collect_chemicals_atoms,
    collect_steps_atoms,
)
from src.kg_building.attribution.cli import main as attribution_main
from src.kg_building.attribution.judge import (
    STAGE_DERIVATION,
    STAGE_EXTRACTION,
    STAGE_KG,
    heuristic_ledger_present,
    judge_atoms,
    stage_for,
)
from src.kg_building.attribution.ledger import load_paper_ledger
from src.kg_building.attribution.report import summarize_module
from src.kg_building.attribution.run import attribute_paper

LEDGER = """SEMANTIC_HINTS_V1
Add
hasOrder: 1
hasAddedChemicalInput: Bis(cyclopentadienyl)zirconium dichloride
hasAmount: 17.5 mg, 0.06 mmol
hasChemicalFormula: C10H10Cl2Zr
"""

GT_STEPS = {
    "Synthesis": [
        {
            "productCCDCNumber": "1576897",
            "productNames": ["UMC-1"],
            "steps": [
                {
                    "Add": {
                        "addedChemical": [
                            {
                                "chemicalAmount": "17.5 mg, 0.06 mmol",
                                "chemicalName": [
                                    "Cp2ZrCl2",
                                    "bis(cyclopentadienyl)zirconium dichloride",
                                ],
                            }
                        ]
                    }
                },
                {
                    "Stir": {
                        "duration": "12 h",
                    }
                },
            ],
        }
    ]
}

PRED_STEPS_MISSING_AMOUNT = {
    "Synthesis": [
        {
            "productCCDCNumber": "1576897",
            "productNames": ["UMC-1"],
            "steps": [
                {
                    "Add": {
                        "addedChemical": [
                            {
                                "chemicalName": [
                                    "bis(cyclopentadienyl)zirconium dichloride"
                                ]
                            }
                        ]
                    }
                }
            ],
        }
    ]
}


def _write_run(tmp_path: Path) -> tuple[Path, Path]:
    mcp = tmp_path / "runtime" / "0c57bac8" / "mcp_run"
    mcp.mkdir(parents=True)
    (mcp / "iter3_hints_UMC-1.txt").write_text(LEDGER, encoding="utf-8")
    pred_root = tmp_path / "merged"
    (pred_root / "0c57bac8").mkdir(parents=True)
    (pred_root / "0c57bac8" / "steps.json").write_text(
        json.dumps(PRED_STEPS_MISSING_AMOUNT), encoding="utf-8"
    )
    gt_root = tmp_path / "full_ground_truth"
    (gt_root / "steps").mkdir(parents=True)
    (gt_root / "steps" / "10.1021_acsami.7b18836.json").write_text(
        json.dumps(GT_STEPS), encoding="utf-8"
    )
    return pred_root, gt_root


def test_heuristic_detects_ledger_amount() -> None:
    score = collect_steps_atoms(
        GT_STEPS, PRED_STEPS_MISSING_AMOUNT, paper_hash="0c57bac8"
    )
    amount_fn = next(atom for atom in score.atoms if atom.field == "chemical_amount")
    assert amount_fn.error_type == "fn"
    assert heuristic_ledger_present(amount_fn, LEDGER) is True
    stir_fn = next(
        atom
        for atom in score.atoms
        if atom.field == "step_type" and atom.gt_value == "Stir"
    )
    assert heuristic_ledger_present(stir_fn, LEDGER) is False
    assert stage_for(amount_fn, ledger_present=True) == STAGE_KG
    assert stage_for(stir_fn, ledger_present=False) == STAGE_EXTRACTION


def test_counterfactual_f1_is_at_least_kg_f1() -> None:
    score = collect_steps_atoms(
        GT_STEPS, PRED_STEPS_MISSING_AMOUNT, paper_hash="0c57bac8"
    )
    judged = judge_atoms(score.atoms, LEDGER, heuristic_only=True)
    summary = summarize_module(score, judged)
    assert summary["monotonicity_ok"] is True
    assert summary["extraction_counterfactual"]["f1"] >= summary["official"]["f1"]
    assert summary["attribution"]["kg_fn"] >= 1
    assert summary["attribution"]["extraction_fn"] >= 1


def test_llm_judge_overrides_heuristic() -> None:
    score = collect_steps_atoms(
        GT_STEPS, PRED_STEPS_MISSING_AMOUNT, paper_hash="0c57bac8"
    )
    amount = next(atom for atom in score.atoms if atom.field == "chemical_amount")

    class _Result:
        def __init__(self, data: dict) -> None:
            self.data = data

    def fake_invoke(_model: str, _prompt: str) -> _Result:
        return _Result(
            {
                "judgements": [
                    {
                        "atom_id": atom.atom_id,
                        "ledger_present": atom.atom_id == amount.atom_id,
                        "evidence": "hasAmount: 17.5 mg, 0.06 mmol",
                        "reason": "mocked",
                    }
                    for atom in score.atoms
                ]
            }
        )

    judged = judge_atoms(score.atoms, LEDGER, invoke=fake_invoke)
    tagged = {row["atom_id"]: row for row in judged}
    assert tagged[amount.atom_id]["stage"] == STAGE_KG
    assert tagged[amount.atom_id]["judge_source"] == "llm"


def test_one_llm_call_packs_all_fns() -> None:
    calls: list[str] = []

    class _Result:
        def __init__(self, data: dict) -> None:
            self.data = data

    atoms = [
        MistakeAtom(
            atom_id=f"steps:h:{idx}",
            module="steps",
            error_type="fn" if idx < 40 else "fp",
            field="step_type",
            gt_value="Add" if idx < 40 else None,
            pred_value="Wash" if idx >= 40 else None,
        )
        for idx in range(50)
    ]

    def fake_invoke(_model: str, prompt: str) -> _Result:
        calls.append(prompt)
        return _Result(
            {
                "judgements": [
                    {"id": atom.atom_id, "in_ledger": True}
                    for atom in atoms
                    if atom.error_type == "fn"
                ]
            }
        )

    judged = judge_atoms(atoms, LEDGER, invoke=fake_invoke)
    assert len(calls) == 1
    assert "Wash" not in calls[0]
    by_id = {row["atom_id"]: row for row in judged}
    assert by_id["steps:h:0"]["judge_source"] == "llm"
    assert by_id["steps:h:40"]["judge_source"] == "heuristic_fp"


def test_attribute_paper_heuristic(tmp_path: Path) -> None:
    pred_root, gt_root = _write_run(tmp_path)
    payload = attribute_paper(
        "0c57bac8",
        pred_root=pred_root,
        hint_runs=[tmp_path],
        gt_root=gt_root,
        modules=("steps",),
        heuristic_only=True,
        hash_to_doi={"0c57bac8": "10.1021/acsami.7b18836"},
    )
    steps = payload["modules"][0]
    assert payload["monotonicity_ok"] is True
    assert steps["attribution"]["kg_fn"] >= 1
    ledger, sources = load_paper_ledger("0c57bac8", [tmp_path])
    assert "hasAmount: 17.5 mg, 0.06 mmol" in ledger
    assert sources


def test_cbu_absent_formula_can_be_derivation() -> None:
    atom = MistakeAtom(
        atom_id="cbu:x:1",
        module="cbu",
        error_type="fn",
        field="cbu_formula",
        gt_value="[Zr3O(OH)3(C5H5)3]",
        pred_value=None,
    )
    assert stage_for(atom, ledger_present=False, cbu_derivation=True) == STAGE_DERIVATION
    assert stage_for(atom, ledger_present=False, cbu_derivation=False) == STAGE_EXTRACTION


def test_chemicals_name_fn_without_ledger() -> None:
    gt = {
        "synthesisProcedures": [
            {
                "steps": [
                    {
                        "inputChemicals": [
                            {
                                "chemical": [
                                    {
                                        "chemicalName": ["H2SDB"],
                                        "chemicalAmount": "9.2 mg, 0.03 mmol",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
    pred = {"synthesisProcedures": [{"steps": [{"inputChemicals": []}]}]}
    score = collect_chemicals_atoms(gt, pred, paper_hash="h")
    assert score.fn >= 1
    judged = judge_atoms(score.atoms, "SEMANTIC_HINTS_V1\nAdd\n", heuristic_only=True)
    assert any(row["stage"] == STAGE_EXTRACTION for row in judged)


def test_official_overall_parser_uses_first_table_only() -> None:
    from src.kg_building.attribution.official import parse_overall_counts

    md = """# Steps Scoring - Overall

| # | ID | TP | FP | FN | Precision | Recall | F1 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0c57bac8 | 86 | 1 | 1 | 0.989 | 0.989 | 0.989 |
| - | **Overall** | **3999** | **497** | **992** | **0.889** | **0.801** | **0.843** |

## Step type-only — Overall (Per-Entity)

| # | Hash | DOI | Entity | GT | Pred | TP | FP | FN | Precision | Recall | F1 |
| ---:|------|-----|--------|---:|-----:|---:|---:|---:|----------:|-------:|----:|
| 1 | 0c57bac8 | 10.1021_x | umc-1 | 7 | 7 | 7 | 0 | 0 | 1.000 | 1.000 | 1.000 |
"""
    rows = parse_overall_counts(md)
    assert rows["0c57bac8"]["tp"] == 86
    assert rows["0c57bac8"]["fn"] == 1
    assert rows["0c57bac8"]["f1"] == 0.989


def test_cli_heuristic_only(tmp_path: Path) -> None:
    pred_root, gt_root = _write_run(tmp_path)
    out = tmp_path / "attr"
    rc = attribution_main(
        [
            "--pred-root",
            str(pred_root),
            "--hint-runs",
            str(tmp_path),
            "--hash",
            "0c57bac8",
            "--out-dir",
            str(out),
            "--gt-root",
            str(gt_root),
            "--module",
            "steps",
            "--heuristic-only",
        ]
    )
    assert rc == 0
    overall = json.loads((out / "_overall.json").read_text(encoding="utf-8"))
    assert overall["monotonicity_ok"] is True
    paper = json.loads((out / "0c57bac8.json").read_text(encoding="utf-8"))
    assert paper["doi"] == "10.1021/acsami.7b18836"
