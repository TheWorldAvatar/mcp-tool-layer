from pathlib import Path

from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    _optional_python_type_for_datatype_ranges,
)


ROOT = Path(__file__).resolve().parents[1]


def test_datatype_type_inference_uses_declared_ranges() -> None:
    xsd = "http://www.w3.org/2001/XMLSchema#"

    assert _optional_python_type_for_datatype_ranges([xsd + "boolean"]) == "Optional[bool]"
    assert _optional_python_type_for_datatype_ranges([xsd + "integer"]) == "Optional[int]"
    assert _optional_python_type_for_datatype_ranges([xsd + "double"]) == "Optional[float]"
    assert _optional_python_type_for_datatype_ranges([xsd + "string"]) == "Optional[str]"


def test_generic_runtime_sources_have_no_ontosynthesis_local_rules() -> None:
    source_paths = [
        ROOT / "src" / "pipelines" / "main_ontology_extractions" / "extract.py",
        ROOT
        / "src"
        / "agents"
        / "scripts_and_prompts_generation"
        / "direct_script_generation.py",
    ]
    forbidden_literals = {
        "OntoSynthesis",
        "ChemicalInput",
        "SynthesisStepList",
        "ontosyn:",
        "create_Add",
        "HeatChill",
        "hasAddedChemicalInput",
    }

    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        assert all(literal not in source for literal in forbidden_literals)
