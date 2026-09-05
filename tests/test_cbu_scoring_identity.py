from evaluation.scoring_cbu import (
    _cbu_formula_identity,
    _extract_procedures,
    _score_procedures_combined,
    _score_species_maps,
)


def test_formula_rearrangement_matches_composition() -> None:
    assert _cbu_formula_identity("[(C6H4)(CO2)2]") == _cbu_formula_identity("[C8H4O4]")
    assert _cbu_formula_identity("[(C6H3Br)(CO2)2]") == _cbu_formula_identity("[C8H3BrO4]")


def test_extra_pubchem_names_are_not_fp() -> None:
    gt = {"950330": ["vanadyl sulfate", "VOSO4-xH2O"]}
    pred = {
        "950330": [
            "VOSO4-xH2O",
            "VANADYL SULFATE",
            "Vanadic sulfate",
            "CI 77940",
        ]
    }
    tp, fp, fn = _score_species_maps(gt, pred)
    assert tp >= 1
    assert fp == 0
    assert fn >= 0


def test_duplicate_ccdc_rows_do_not_dump_synonym_fp() -> None:
    gt = {
        "synthesisProcedures": [
            {
                "mopCCDCNumber": "1590348",
                "cbuFormula1": "[V6O6(OCH3)9(SO4)]",
                "cbuFormula2": "[(C6H4C)2(CO2)2]",
                "cbuSpeciesNames1": ["VOSO4-xH2O"],
                "cbuSpeciesNames2": ["H2edb"],
            }
        ]
    }
    pred = {
        "synthesisProcedures": [
            {
                "mopCCDCNumber": "1590348",
                "cbuFormula1": "[V6O6(OCH3)9(SO4)]",
                "cbuFormula2": "[(C6H4C)2(CO2)2]",
                "cbuSpeciesNames1": ["VOSO4-xH2O", "VANADYL SULFATE", "CI 77940"],
                "cbuSpeciesNames2": ["H2edb"],
            },
            {
                "mopCCDCNumber": "1590348",
                "cbuFormula1": "[V6O6(OCH3)9(SO4)]",
                "cbuFormula2": "[(C6H4C)2(CO2)2]",
                "cbuSpeciesNames1": ["oxovanadium(2+) sulfate", "Vanadic sulfate"],
                "cbuSpeciesNames2": ["H2edb"],
            },
        ]
    }
    tp, fp, fn = _score_procedures_combined(
        _extract_procedures(gt),
        _extract_procedures(pred),
    )
    assert tp > 0
    assert fp == 0
    assert fn == 0
