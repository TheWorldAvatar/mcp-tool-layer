from pathlib import Path

from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl


ROOT = Path(__file__).resolve().parents[1]


def test_ontosynthesis_semantics_are_available_as_structured_tbox_data() -> None:
    parsed = parse_ontology_ttl(str(ROOT / "data/ontologies/ontosynthesis.ttl"))

    assert parsed["classes"]["ChemicalSynthesis"]["comment"]
    assert parsed["classes"]["ChemicalInput"]["comment"]
    assert parsed["classes"]["Stir"]["comment"]
    assert parsed["classes"]["HeatChill"]["comment"]
    assert parsed["properties"]["hasChemicalInput"]["comment"]
    assert parsed["properties"]["hasWashingSolvent"]["comment"]
    assert parsed["properties"]["hasStepDuration"]["comment"]


def test_ontosynthesis_input_layers_are_complete_and_non_conflicting() -> None:
    parsed = parse_ontology_ttl(str(ROOT / "data/ontologies/ontosynthesis.ttl"))
    chemical_comment = parsed["classes"]["ChemicalInput"]["comment"]
    synthesis_link = parsed["properties"]["hasChemicalInput"]
    step_links = {
        "hasAddedChemicalInput": ("Add", parsed["properties"]["hasAddedChemicalInput"]),
        "hasWashingSolvent": ("Filter", parsed["properties"]["hasWashingSolvent"]),
        "hasSeparationSolvent": (
            "Separate",
            parsed["properties"]["hasSeparationSolvent"],
        ),
    }

    assert "two distinct ownership layers" in chemical_comment
    assert "synthesis-level layer" in chemical_comment
    assert "step-local layer" in chemical_comment
    assert "Never merge or reuse an occurrence across these layers" in chemical_comment
    assert synthesis_link["domains"] == ["ChemicalSynthesis"]
    assert synthesis_link["range"] == "ChemicalInput"
    assert "excludes a pure solvent" in chemical_comment
    assert "pure solvent used only as process medium" in synthesis_link["comment"]

    for property_local, (domain_local, property_spec) in step_links.items():
        assert property_spec["domains"] == [domain_local], property_local
        assert property_spec["range"] == "ChemicalInput", property_local
        assert "fresh step-local ChemicalInput occurrence" in property_spec["comment"]


def test_mixture_guidance_is_generic_not_fixture_specific() -> None:
    tbox = (ROOT / "data/ontologies/ontosynthesis.ttl").read_text(encoding="utf-8")

    assert "V*ri/sum(r)" in tbox
    for fixture_value in ("2.5 mL", "0.83 mL", "1.67 mL"):
        assert fixture_value not in tbox
