"""Overlay generator: generic templates plus T-Box-derived quantity tables."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.kg_building_mcp_generation.emit.main import emit_occurrence_main
from src.kg_building_mcp_generation.emit.operations import emit_occurrence_operations
from src.kg_building_mcp_generation.overlay.instruction import (
    compile_overlay_instruction,
)
from src.kg_building_mcp_generation.overlay.policy import (
    forbidden_hits,
    handwritten_template_blobs,
)
from src.kg_building_mcp_generation.overlay.quantity_surface import (
    compile_quantity_surface,
    load_om2_graph,
)

REPO = Path(__file__).resolve().parents[1]
OM2_TTL = REPO / "data" / "ontologies" / "om2.ttl"
DURATION = "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"
TEMPERATURE = "http://www.ontology-of-units-of-measure.org/resource/om-2/Temperature"
HAS_STEP = "https://www.theworldavatar.com/kg/OntoSyn/hasStepDuration"
HAS_TEMP = "https://www.theworldavatar.com/kg/OntoSyn/hasTargetTemperature"
HAS_YIELD = "https://www.theworldavatar.com/kg/OntoSyn/hasYield"


def _units() -> dict:
    return {
        "public_tools": [
            {
                "name": "create_HeatChill",
                "parent_parameter": "parent_iri",
                "parent_binds_to_session_root": True,
                "ordering_property_local": "hasOrder",
                "quantities": [
                    {
                        "parameter": "hasStepDuration",
                        "predicate_local": "hasStepDuration",
                        "predicate_iri": HAS_STEP,
                        "range_iri": DURATION,
                    },
                    {
                        "parameter": "hasTargetTemperature",
                        "predicate_local": "hasTargetTemperature",
                        "predicate_iri": HAS_TEMP,
                        "range_iri": TEMPERATURE,
                    },
                ],
            },
            {
                "name": "create_ChemicalOutput",
                "parent_parameter": "parent_iri",
                "parent_binds_to_session_root": True,
                "parent_quantities": [
                    {
                        "parameter": "hasYield",
                        "predicate_local": "hasYield",
                        "predicate_iri": HAS_YIELD,
                        "range_iri": "http://www.ontology-of-units-of-measure.org/resource/om-2/AmountOfSubstanceFraction",
                    }
                ],
            },
        ],
        "public_linkers": [],
        "reusable_classes": [],
    }


def _context(units: dict):
    return SimpleNamespace(
        ontology=SimpleNamespace(name="ontosynthesis"),
        contract={"occurrence_surface_units": units},
    )


@pytest.mark.skipif(not OM2_TTL.is_file(), reason="om2.ttl is missing")
def test_templates_are_generic() -> None:
    for name, blob in handwritten_template_blobs().items():
        hits = forbidden_hits(blob)
        assert not hits, f"{name} contains domain tokens: {hits}"


@pytest.mark.skipif(not OM2_TTL.is_file(), reason="om2.ttl is missing")
def test_quantity_surface_derives_facets_and_unit_classes() -> None:
    graph = load_om2_graph()
    surface = compile_quantity_surface(_units(), om2_graph=graph)
    facets = surface["facets"]
    assert facets["hasStepDuration"]["range_iri"] == DURATION
    assert facets["hasStepDuration"]["attach_to"] == "owner"
    assert facets["hasYield"]["attach_to"] == "bound_root"
    assert surface["unit_local_to_class"]["degreeCelsius"] == TEMPERATURE
    assert surface["unit_local_to_class"]["hour"] == DURATION
    duration = surface["classes"][DURATION]
    assert "h" in duration["allowed_unit_aliases"]
    assert duration["example_labels"][0].startswith("1 ")
    assert not duration["example_labels"][0].startswith("overnight")
    assert "overnight" in duration["qualitative_labels"]
    assert "create_HeatChill" in surface["example_calls"]["hasStepDuration"]
    assert surface["compact_example_call"].startswith("create_HeatChill(")
    assert "hasStepDuration=" in surface["compact_example_call"]
    assert "overnight" not in surface["compact_example_call"]


@pytest.mark.skipif(not OM2_TTL.is_file(), reason="om2.ttl is missing")
def test_instruction_scopes_compact_to_compiled_facets() -> None:
    surface = compile_quantity_surface(_units(), om2_graph=load_om2_graph())
    text = compile_overlay_instruction(_units(), surface)
    assert "Only the compiled quantity arguments listed below" in text
    assert "Measured values that are not in that compiled list" in text
    assert "hasStepDuration -> Duration" in text
    assert "hasTargetTemperature -> Temperature" in text
    assert "hasYield -> AmountOfSubstanceFraction" in text
    assert "Bound-root quantity facets: `hasYield`" in text
    assert "skippable false" in text
    assert "A failed optional quantity facet is omitted" not in text
    assert "Source-backed measured values" not in text
    assert "Preferred path is compact text on create_*, not this mint." not in text
    assert "hasStepDuration='8 h'" not in text
    assert "never Time" not in text
    assert "aliases:" not in text
    assert text.count("already_committed receipt with graph_changed false is not progress") == 1
    assert "create_HeatChill(..., hasStepDuration=" in text
    assert "Derived example:" in text
    quantity_block = text.split("The pipeline has already called init_memory", 1)[0]
    assert "1 " in quantity_block
    assert "'overnight'" not in surface["compact_example_call"]


@pytest.mark.skipif(not OM2_TTL.is_file(), reason="om2.ttl is missing")
def test_emitted_operations_hard_fail_and_parse() -> None:
    units = _units()
    context = _context(units)
    source = emit_occurrence_operations(context, units)
    ast.parse(source)
    assert "skippable = False" in source or 'payload["skippable"] = False' in source
    assert "QUANTITY_UNIT_CLASS_MISMATCH" in source
    assert "def link_om2_quantity(" in source
    assert "60 degC" not in source
    assert "8 h" not in source
    assert "quantities={'hasStepDuration': hasStepDuration" in source
    assert (
        "create_*(..., <compiled_quantity_argument>" in source
        or "create_HeatChill(..., hasStepDuration=" in source
    )


@pytest.mark.skipif(not OM2_TTL.is_file(), reason="om2.ttl is missing")
def test_emitted_main_instruction_and_om2_wrap() -> None:
    units = _units()
    context = _context(units)
    emit_occurrence_operations(context, units)
    source = emit_occurrence_main(context, units)
    ast.parse(source)
    assert "def create_om2_quantity(" in source
    assert "Never reuse this IRI" not in source
    assert "mcp.tool(name='create_HeatChill')" in source or 'mcp.tool(name="create_HeatChill")' in source
    assert "link_om2_quantity" in source
    assert "Source-backed measured values" not in source
    assert "Preferred path is compact text on create_*, not this mint." not in source
    assert "Preferred path: pass compact labels on create_* rather than this IRI" not in source
    assert source.count("already_committed receipt with graph_changed false is not progress") == 1
    assert "A failed optional quantity facet is omitted" not in source
