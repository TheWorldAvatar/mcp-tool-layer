from pathlib import Path
from types import SimpleNamespace

from src.agents.scripts_and_prompts_generation import semantic_script_review
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _artifact_generation_contract,
    _artifact_generation_guidance,
    _artifact_role_contract,
)


def _context(tmp_path: Path) -> SimpleNamespace:
    scripts_dir = tmp_path / "scripts" / "onto"
    scripts_dir.mkdir(parents=True)
    return SimpleNamespace(
        scripts_dir=scripts_dir,
        output_root=tmp_path,
        ontology=SimpleNamespace(name="onto", role="main"),
        parsed={
            "classes": {
                "Entity": {"iri": "urn:onto:Entity", "parent_classes": []},
                "Step": {"iri": "urn:onto:Step", "parent_classes": []},
                "ConcreteStep": {
                    "iri": "urn:onto:ConcreteStep",
                    "parent_classes": ["Step"],
                },
            }
        },
        contract={
            "ordered_member_profile": {
                "ordered_member_classes": ["Step"],
                "single_valued_ordering_properties": ["hasOrder"]
            },
            "relationship_tool_contracts": {},
            "ontology_publish_contract": {
                "subclass_closure": [
                    {
                        "class_iri": "urn:onto:ConcreteStep",
                        "superclass_iris": [
                            "urn:onto:ConcreteStep",
                            "urn:onto:Step",
                        ],
                    }
                ],
                "datatype_properties": [
                    {
                        "property_iri": "urn:onto:hasName",
                        "domain_iris": ["urn:onto:Entity"],
                        "range_iris": ["http://www.w3.org/2001/XMLSchema#string"],
                    },
                    {
                        "property_iri": "urn:onto:hasOrder",
                        "domain_iris": ["urn:onto:Step"],
                        "range_iris": ["http://www.w3.org/2001/XMLSchema#integer"],
                    },
                ]
            },
        },
    )


def test_entity_generation_contract_projects_datatypes_into_domain_creators(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    target = Path(context.scripts_dir) / "onto_creation_entities.py"

    contract = _artifact_generation_contract(context, target)
    role = _artifact_role_contract(target)
    guidance = _artifact_generation_guidance(target)

    creators = {
        item["class_local"]: item
        for item in contract["owned_entity_tool_contracts"]
    }
    assert creators["Entity"]["datatype_inputs"] == [
        {
            "property_local": "hasName",
            "property_iri": "urn:onto:hasName",
            "range_iri": "http://www.w3.org/2001/XMLSchema#string",
            "python_type": "str",
            "tbox_comment": "",
            "required": False,
        }
    ]
    assert creators["Step"]["datatype_inputs"][0]["property_local"] == "hasOrder"
    assert creators["Step"]["datatype_inputs"][0]["required"]
    assert creators["ConcreteStep"]["datatype_inputs"] == [
        {
            "property_local": "hasOrder",
            "property_iri": "urn:onto:hasOrder",
            "range_iri": "http://www.w3.org/2001/XMLSchema#integer",
            "python_type": "int",
            "tbox_comment": "",
            "required": False,
        }
    ]
    assert "optional keyword parameter" in str(role)
    assert "package_datatype_capabilities()" in guidance
    assert "Do not generate public `set_<property>` tools" in guidance


def test_main_contract_carries_datatype_completeness_to_registry_review(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    scripts = Path(context.scripts_dir)
    (scripts / "onto_creation_entities.py").write_text(
        "__all__ = ['create_Entity']\n"
        "def create_Entity(label: str, *, hasName: str | None = None) -> str:\n"
        "    return label\n",
        encoding="utf-8",
    )
    (scripts / "onto_creation_relationships.py").write_text(
        "__all__ = []\n",
        encoding="utf-8",
    )
    (scripts / "onto_creation_checks.py").write_text(
        "__all__ = ['check_ordered_members']\n"
        "def check_ordered_members() -> str:\n"
        "    return '{}'\n",
        encoding="utf-8",
    )

    contract = _artifact_generation_contract(context, scripts / "main.py")

    assert contract["datatype_property_contracts"][0]["property_iri"] == "urn:onto:hasName"
    assert contract["datatype_completeness_policy"][
        "all_paths_must_be_creator_inputs"
    ]
    assert contract["datatype_completeness_policy"]["separate_datatype_setters_forbidden"]


def test_semantic_reviewer_is_explicitly_tasked_with_datatype_completeness(
    tmp_path: Path, monkeypatch
) -> None:
    context = _context(tmp_path)
    target = Path(context.scripts_dir) / "onto_creation_entities.py"
    target.write_text("__all__ = []\n", encoding="utf-8")
    captured: dict[str, str] = {}

    def fake_invoke_json(_model: str, prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(
            data={
                "decision": "pass",
                "summary": "complete",
                "critical_errors": [],
                "noncritical_observations": [],
                "confidence": 1.0,
            }
        )

    monkeypatch.setattr(semantic_script_review, "invoke_json", fake_invoke_json)
    monkeypatch.setattr(
        semantic_script_review,
        "_entity_behavior_evidence",
        lambda *_args, **_kwargs: {"applicable": False},
    )

    semantic_script_review.review_generated_artifact_semantics_with_llm(
        context=context,
        artifact_path=target,
        model_name="test-model",
    )

    assert "Datatype-property completeness" in captured["prompt"]
    assert "urn:onto:hasName" in captured["prompt"]
    assert "package_datatype_capabilities()" in captured["prompt"]
