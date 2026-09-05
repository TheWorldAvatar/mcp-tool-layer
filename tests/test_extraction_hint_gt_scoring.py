from scripts.score_extraction_hints_against_gt import parse_semantic_steps


def test_semantic_hint_parser_handles_numbered_and_unnumbered_steps() -> None:
    content = """SEMANTIC_HINTS_V1

1. Add step (order 1): Introduces Cu(NO3)2.6H2O as a step-local ChemicalInput with amount "0.1 mmol (24.1 mg)".
2. Add step (order 2): DMF (4 mL) was added to the vessel.

HeatChill step (order 3): The vessel was heated at "90 degC" for "24 h".
"""

    synthesis, errors = parse_semantic_steps(content, "Example")

    assert errors == []
    assert [next(iter(step)) for step in synthesis["steps"]] == [
        "Add",
        "Add",
        "HeatChill",
    ]
    assert synthesis["steps"][0]["Add"]["addedChemical"][0] == {
        "chemicalName": ["Cu(NO3)2.6H2O"],
        "chemicalAmount": "0.1 mmol (24.1 mg)",
    }
    assert synthesis["steps"][1]["Add"]["addedChemical"][0] == {
        "chemicalName": ["DMF"],
        "chemicalAmount": "4 mL",
    }
    assert synthesis["steps"][2]["HeatChill"]["targetTemperature"] == "90 degC"
    assert synthesis["steps"][2]["HeatChill"]["duration"] == "24 h"


def test_semantic_hint_parser_handles_quoted_step_local_input() -> None:
    content = """SEMANTIC_HINTS_V1

Add step (order 1): Introduces step-local ChemicalInput "distilled water (6 drops)" via hasAddedChemicalInput.
"""

    synthesis, errors = parse_semantic_steps(content, "Example")

    assert errors == []
    chemical = synthesis["steps"][0]["Add"]["addedChemical"][0]
    assert chemical["chemicalName"] == ["distilled water"]
    assert chemical["chemicalAmount"] == "6 drops"
