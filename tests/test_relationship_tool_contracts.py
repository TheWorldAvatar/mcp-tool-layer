from __future__ import annotations

import ast
import importlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

from rdflib import URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _relationships_script,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _expected_tool_surface_report,
    _relationship_param_description_report,
    _runtime_graph_hygiene_report,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_relationship_tool_contracts_from_tbox,
)
from src.agents.scripts_and_prompts_generation.repair_skill_catalog import (
    repair_skill_catalog,
)
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    __file__ as fixed_rdf_runtime_path,
)


def _write_synthetic_tbox(path: Path) -> None:
    path.write_text(
        """
@prefix ex: <https://example.test/schema/> .
@prefix ext: <https://external.test/types/> .
@prefix om: <http://www.ontology-of-units-of-measure.org/resource/om-2/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Source a owl:Class .
ex:InternalTarget a owl:Class .

ex:hasInternal a owl:ObjectProperty ;
    rdfs:domain ex:Source ;
    rdfs:range ex:InternalTarget .

ex:hasExternal a owl:ObjectProperty ;
    rdfs:domain ex:Source ;
    rdfs:range ext:ExternalTarget .

ex:hasQuantity a owl:ObjectProperty ;
    rdfs:domain ex:Source ;
    rdfs:range om:NeutralQuantity .

ex:hasUnknown a owl:ObjectProperty ;
    rdfs:domain ex:Source .
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_external_creator_is_derived_from_restriction_only_target(
    tmp_path: Path,
) -> None:
    tbox = tmp_path / "restriction-only.ttl"
    tbox.write_text(
        """
@prefix ex: <https://example.test/schema/> .
@prefix ext: <https://external.test/types/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Source a owl:Class ;
  rdfs:subClassOf [
    a owl:Restriction ;
    owl:onProperty ex:hasExternal ;
    owl:minQualifiedCardinality 1 ;
    owl:onClass ext:ExternalTarget
  ] .
ex:hasExternal a owl:ObjectProperty ; rdfs:domain ex:Source .
""".strip(),
        encoding="utf-8",
    )
    from src.agents.scripts_and_prompts_generation.generation_contracts import (
        _external_creator_specs,
        build_ontology_publish_contract_from_tbox,
    )

    contract = build_ontology_publish_contract_from_tbox(
        tbox,
        ontology_name="example",
    )
    assert contract["required_links"][0]["target_class_iri"] == (
        "https://external.test/types/ExternalTarget"
    )
    specs = _external_creator_specs(
        {contract["required_links"][0]["target_class_iri"]},
        internal_class_locals={"Source"},
    )
    assert specs[0]["tool_name"] == "create_ExternalTarget"


def test_relationship_tool_contracts_are_compiled_only_from_tbox(
    tmp_path: Path,
) -> None:
    tbox = tmp_path / "synthetic.ttl"
    _write_synthetic_tbox(tbox)

    contracts = build_relationship_tool_contracts_from_tbox(tbox)

    assert contracts["hasInternal"]["creator_tools"] == ["create_InternalTarget"]
    assert contracts["hasInternal"]["target_handling"] == "generated_creator"
    assert contracts["hasExternal"]["creator_tools"] == ["create_ExternalTarget"]
    assert contracts["hasExternal"]["external_targets"] == ["ExternalTarget"]
    assert contracts["hasExternal"]["target_handling"] == "generated_external_creator"
    assert contracts["hasExternal"]["external_creator_specs"] == [
        {
            "class_iri": "https://external.test/types/ExternalTarget",
            "class_local": "ExternalTarget",
            "tool_name": "create_ExternalTarget",
            "check_tool_name": "check_existing_ExternalTarget",
            "source": "object_property_external_range",
        }
    ]
    assert contracts["hasQuantity"]["creator_tools"] == ["create_om2_quantity"]
    assert contracts["hasQuantity"]["target_handling"] == "fixed_runtime_creator"
    assert contracts["hasQuantity"]["fixed_runtime_range_iris"] == [
        "http://www.ontology-of-units-of-measure.org/resource/om-2/NeutralQuantity"
    ]
    assert contracts["hasUnknown"]["range_locals"] == []
    assert contracts["hasUnknown"]["target_handling"] == "untyped_existing_iri"


def test_agentic_relationship_generation_consumes_compiled_contract(
    tmp_path: Path,
) -> None:
    tbox = tmp_path / "synthetic.ttl"
    _write_synthetic_tbox(tbox)
    contracts = build_relationship_tool_contracts_from_tbox(tbox)
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        parsed={
            "properties": {
                name: {"kind": "object"} for name in contracts
            }
        },
        contract={
            "relationship_tool_contracts": contracts,
            "relationship_domain_contracts": {},
        },
    )

    code = _relationships_script(context)
    tree = ast.parse(code)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    internal = ast.get_source_segment(code, functions["add_hasInternal"]) or ""
    external = ast.get_source_segment(code, functions["add_hasExternal"]) or ""
    assert "absolute IRI" in internal
    assert "never a label/name/literal/plain text" in internal
    assert "InternalTarget" in internal
    assert "create_InternalTarget" in internal
    assert "ExternalTarget" in external
    assert "create_ExternalTarget" in external
    assert "package_relationship_capabilities" in code
    assert "add_object_property" not in code
    assert "add_object_triple" not in code
    assert ".add((" not in code


def test_agentic_relationship_validator_consumes_same_compiled_contract(
    tmp_path: Path,
) -> None:
    tbox = tmp_path / "synthetic.ttl"
    _write_synthetic_tbox(tbox)
    contracts = build_relationship_tool_contracts_from_tbox(tbox)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        scripts_dir=scripts_dir,
        parsed={
            "properties": {
                name: {"kind": "object"} for name in contracts
            }
        },
        contract={
            "relationship_tool_contracts": contracts,
            "relationship_domain_contracts": {},
        },
    )
    (scripts_dir / "synthetic_creation_relationships.py").write_text(
        _relationships_script(context),
        encoding="utf-8",
    )

    failures, warnings = _relationship_param_description_report(context)

    assert failures == []
    assert warnings == []


def test_relationship_metadata_repair_skill_is_domain_agnostic() -> None:
    skill = next(
        item
        for item in repair_skill_catalog()
        if item["skill_id"] == "relationship-parameter-metadata"
    )
    text = repr(skill)

    assert "relationship_tool_contracts" in text
    assert "creator_tools" in text
    assert "absolute IRI" in text
    assert "example.test" not in text


def test_runtime_hygiene_warns_on_internal_generic_relationship_mutation_api(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "main.py").write_text(
        "def materialize_hints():\n    return None\n",
        encoding="utf-8",
    )
    (scripts_dir / "synthetic_creation_relationships.py").write_text(
        "def add_hasInternal(subject_iri, object_iri):\n"
        "    return add_object_property(subject_iri, 'urn:p', object_iri)\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        scripts_dir=scripts_dir,
        contract={},
        parsed={},
    )

    failures, warnings, _ = _runtime_graph_hygiene_report(context)

    assert not any("generic RDF mutation" in failure for failure in failures)
    assert any("internal generic RDF mutation" in warning for warning in warnings)


def test_runtime_hygiene_warns_on_renamed_direct_graph_mutation(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "main.py").write_text(
        "def materialize_hints():\n    return None\n",
        encoding="utf-8",
    )
    (scripts_dir / "synthetic_creation_entities.py").write_text(
        "def create_Neutral(g, triple):\n"
        "    g.add(triple)\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        scripts_dir=scripts_dir,
        contract={},
        parsed={},
    )

    failures, warnings, _ = _runtime_graph_hygiene_report(context)

    assert not any("direct graph mutation" in failure for failure in failures)
    assert any("direct graph mutation" in warning for warning in warnings)


def test_hard_tool_surface_does_not_classify_semantics_by_tool_name(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "main.py").write_text(
        "def add_object_property(subject_iri, predicate_iri, object_iri):\n"
        "    return None\n"
        "class Registry:\n"
        "    tools = {'add_object_property': add_object_property}\n"
        "mcp = Registry()\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic", role="main"),
        scripts_dir=scripts_dir,
        parsed={"classes": {}, "properties": {}},
        contract={},
    )

    failures, _, _ = _expected_tool_surface_report(context)

    assert not any("generic" in failure.casefold() for failure in failures)


def test_public_relationship_tool_rejects_wrong_range_before_mutation(
    tmp_path: Path,
) -> None:
    package = tmp_path / "neutral_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(fixed_rdf_runtime_path, package / "_fixed_rdf_runtime.py")
    ontology_contract = {
        "classes": [
            {"class_iri": "https://example.test/schema/Source"},
            {"class_iri": "https://example.test/schema/InternalTarget"},
            {"class_iri": "https://example.test/schema/WrongTarget"},
        ],
        "subclass_closure": [],
        "object_properties": [
            {
                "property_iri": "https://example.test/schema/hasInternal",
                "domain_iris": ["https://example.test/schema/Source"],
                "range_iris": ["https://example.test/schema/InternalTarget"],
            }
        ],
    }
    (package / "_relationship_contract.json").write_text(
        json.dumps(ontology_contract),
        encoding="utf-8",
    )
    (package / "synthetic_creation_base.py").write_text(
        "import json\n"
        "def _format_error_json(code, message):\n"
        "    return json.dumps({'status': 'rejected', 'code': code, 'message': message})\n"
        "def _format_success_json(iri, message, **metadata):\n"
        "    return json.dumps({'status': 'ok', 'iri': str(iri), **metadata})\n",
        encoding="utf-8",
    )
    contracts = {
        "hasInternal": {
            "predicate_iri": "https://example.test/schema/hasInternal",
            "predicate_local": "hasInternal",
            "domain_iris": ["https://example.test/schema/Source"],
            "range_iris": ["https://example.test/schema/InternalTarget"],
            "range_locals": ["InternalTarget"],
            "creator_tools": ["create_InternalTarget"],
            "external_range_iris": [],
        }
    }
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        parsed={"properties": {"hasInternal": {"kind": "object"}}},
        contract={
            "relationship_tool_contracts": contracts,
            "relationship_domain_contracts": {},
        },
    )
    (package / "synthetic_creation_relationships.py").write_text(
        _relationships_script(context),
        encoding="utf-8",
    )
    source = tmp_path / "typed.ttl"
    source.write_text(
        "<https://example.test/source> a <https://example.test/schema/Source> .\n"
        "<https://example.test/wrong> a <https://example.test/schema/WrongTarget> .\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        runtime = importlib.import_module("neutral_pkg._fixed_rdf_runtime")
        relationships = importlib.import_module(
            "neutral_pkg.synthetic_creation_relationships"
        )
        runtime.reset_retained_graph()
        runtime.load_from_turtle_file(str(source))

        result = json.loads(
            relationships.add_hasInternal(
                "https://example.test/source",
                "https://example.test/wrong",
            )
        )

        assert result["status"] == "rejected"
        assert result["code"] == "RELATIONSHIP_CONTRACT_REJECTED"
        assert (
            URIRef("https://example.test/source"),
            URIRef("https://example.test/schema/hasInternal"),
            URIRef("https://example.test/wrong"),
        ) not in runtime.retained_graph()
    finally:
        sys.path.remove(str(tmp_path))
        for module_name in list(sys.modules):
            if module_name == "neutral_pkg" or module_name.startswith("neutral_pkg."):
                sys.modules.pop(module_name, None)
