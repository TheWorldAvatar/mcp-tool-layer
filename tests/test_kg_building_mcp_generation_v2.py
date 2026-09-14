from __future__ import annotations

from pathlib import Path

from src.kg_building_mcp_generation.surface.compile import (
    compile_occurrence_surface as compile_v1,
)
from src.kg_building_mcp_generation_v2.surface.candidates import (
    discover_occurrence_surface_candidates,
)
from src.kg_building_mcp_generation_v2.surface.compile import (
    compile_occurrence_surface,
    keep_as_public_owner,
    public_owner_may_omit_parent,
)
from test_kg_building_mcp_generation import (
    NS,
    XSD,
    _bundle_all,
    _extension_surface_fixture,
    _fixture,
)


def _ambiguous_same_domain_fixture() -> tuple[dict, dict]:
    parsed, contract = _fixture()
    parsed["classes"]["Session"] = {
        "iri": NS + "Session",
        "parent_classes": [],
        "comment": "Non-root owner that records two kinds of observation.",
    }
    parsed["classes"]["Observation"] = {
        "iri": NS + "Observation",
        "parent_classes": [],
        "comment": "Measurement node ranged by two same-domain properties.",
    }
    parsed["properties"]["hasSession"] = {
        "comment": "A container owns one session."
    }
    parsed["properties"]["sessionNote"] = {"comment": "Optional session note."}
    parsed["properties"]["obsValue"] = {"comment": "Optional observation value."}
    parsed["properties"]["hasCalculatedObservation"] = {
        "comment": "Session calculated observation."
    }
    parsed["properties"]["hasExperimentalObservation"] = {
        "comment": "Session experimental observation."
    }
    publish = contract["ontology_publish_contract"]
    publish["subclass_closure"].extend(
        [
            {"class_iri": NS + "Session", "superclass_iris": []},
            {"class_iri": NS + "Observation", "superclass_iris": []},
        ]
    )
    publish["datatype_properties"].extend(
        [
            {
                "property_iri": NS + "sessionNote",
                "domain_iris": [NS + "Session"],
                "range_iris": [XSD + "string"],
            },
            {
                "property_iri": NS + "obsValue",
                "domain_iris": [NS + "Observation"],
                "range_iris": [XSD + "string"],
            },
        ]
    )
    rel = contract["relationship_tool_contracts"]
    rel["hasSession"] = {
        "predicate_local": "hasSession",
        "predicate_iri": NS + "hasSession",
        "domain_iris": [NS + "Container"],
        "range_iris": [NS + "Session"],
    }
    rel["hasCalculatedObservation"] = {
        "predicate_local": "hasCalculatedObservation",
        "predicate_iri": NS + "hasCalculatedObservation",
        "domain_iris": [NS + "Session"],
        "range_iris": [NS + "Observation"],
    }
    rel["hasExperimentalObservation"] = {
        "predicate_local": "hasExperimentalObservation",
        "predicate_iri": NS + "hasExperimentalObservation",
        "domain_iris": [NS + "Session"],
        "range_iris": [NS + "Observation"],
    }
    contract["reuse_policy"]["classes"].extend(
        [
            {
                "class_iri": NS + "Session",
                "class_local": "Session",
                "reusable": False,
                "reuse_scope": "occurrence_local",
            },
            {
                "class_iri": NS + "Observation",
                "class_local": "Observation",
                "reusable": False,
                "reuse_scope": "occurrence_local",
            },
        ]
    )
    return parsed, contract


def _parentless_unnested_fixture() -> tuple[dict, dict]:
    parsed, contract = _fixture()
    parsed["classes"]["IsolatedNote"] = {
        "iri": NS + "IsolatedNote",
        "parent_classes": [],
        "comment": "Parentless heading with no incoming object property.",
    }
    parsed["properties"]["noteText"] = {"comment": "Optional isolated note text."}
    publish = contract["ontology_publish_contract"]
    publish["subclass_closure"].append(
        {"class_iri": NS + "IsolatedNote", "superclass_iris": []}
    )
    publish["datatype_properties"].append(
        {
            "property_iri": NS + "noteText",
            "domain_iris": [NS + "IsolatedNote"],
            "range_iris": [XSD + "string"],
        }
    )
    contract["reuse_policy"]["classes"].append(
        {
            "class_iri": NS + "IsolatedNote",
            "class_local": "IsolatedNote",
            "reusable": False,
            "reuse_scope": "occurrence_local",
        }
    )
    return parsed, contract


def _compile(parsed: dict, contract: dict, *, v2: bool = True) -> dict:
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract = {
        **contract,
        "occurrence_surface_candidates": candidates,
        "occurrence_surface_decisions": _bundle_all(candidates),
    }
    compiler = compile_occurrence_surface if v2 else compile_v1
    if not v2:
        from src.kg_building_mcp_generation.surface.candidates import (
            discover_occurrence_surface_candidates as discover_v1,
        )

        candidates = discover_v1(parsed=parsed, contract=contract)
        contract["occurrence_surface_candidates"] = candidates
        contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    return compiler(parsed=parsed, contract=contract)


def test_v2_keeps_unique_parent_and_ordered_member() -> None:
    parsed, contract = _fixture()
    compiled = _compile(parsed, contract)
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert "create_Member" in tools
    assert "create_Dependent" in tools
    assert tools["create_Member"]["parent_parameter"] == "parent_iri"
    assert tools["create_Dependent"]["parent_parameter"] == "parent_iri"
    assert compiled["errors"] == []


def test_v2_keeps_adopted_focus_parentless() -> None:
    parsed, contract = _extension_surface_fixture(focus_reusable=True)
    compiled = _compile(parsed, contract)
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert "create_Focus" in tools
    assert not tools["create_Focus"]["parent_parameter"]
    assert tools["create_Focus"]["adopted_focus"] is True
    assert public_owner_may_omit_parent(tools["create_Focus"]) is True
    assert tools["create_Part"]["parent_parameter"] == "parent_iri"


def test_v2_omits_parentless_class_with_two_same_domain_parents() -> None:
    parsed, contract = _ambiguous_same_domain_fixture()
    v1 = _compile(parsed, contract, v2=False)
    v2 = _compile(parsed, contract, v2=True)
    v1_tools = {item["name"]: item for item in v1["public_tools"]}
    v2_tools = {item["name"]: item for item in v2["public_tools"]}
    assert "create_Observation" in v1_tools
    assert not v1_tools["create_Observation"]["parent_parameter"]
    assert "create_Observation" not in v2_tools
    assert "create_Session" in v2_tools
    session = v2_tools["create_Session"]
    nested = {item["predicate_local"] for item in session["fresh_dependents"]}
    assert nested == {"hasCalculatedObservation", "hasExperimentalObservation"}
    assert "create_Member" in v2_tools
    assert "create_Dependent" in v2_tools
    assert v2["errors"] == []


def test_v2_keeps_parentless_class_that_is_not_nested() -> None:
    parsed, contract = _parentless_unnested_fixture()
    v1 = _compile(parsed, contract, v2=False)
    v2 = _compile(parsed, contract, v2=True)
    v1_tools = {item["name"]: item for item in v1["public_tools"]}
    v2_tools = {item["name"]: item for item in v2["public_tools"]}
    assert "create_IsolatedNote" in v1_tools
    assert "create_IsolatedNote" in v2_tools
    note = v2_tools["create_IsolatedNote"]
    assert not note["parent_parameter"]
    assert public_owner_may_omit_parent(note) is False
    nested = {
        str(item.get("target_class_iri") or "")
        for tool in v2["public_tools"]
        for item in tool.get("fresh_dependents") or []
    }
    assert keep_as_public_owner(note, nested) is True
    assert "create_Member" in v2_tools
    assert v2["errors"] == []


def test_v2_generator_modules_contain_no_ontology_literals() -> None:
    root = Path("src/kg_building_mcp_generation_v2")
    forbidden = (
        "WeightPercentage",
        "ElementalAnalysisData",
        "hasWeightPercentageCalculated",
        "OntoSpecies",
        "ontospecies:",
        "ChemicalInput",
        "ChemicalOutput",
        "HeatChill",
        "hasVessel",
        "OntoSyn",
        "ontosyn:",
    )
    for name in (
        "surface/helpers.py",
        "surface/candidates.py",
        "surface/compile.py",
        "surface/infer.py",
        "surface/judge.py",
        "emit/sidecars.py",
        "emit/operations.py",
        "emit/main.py",
        "emit/scripts.py",
        "overlay/om2_runtime_emit.py",
        "overlay/om2_compact.py",
        "cli.py",
        "compile_hook.py",
    ):
        text = (root / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{name} contains {token}"
