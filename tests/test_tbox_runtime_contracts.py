from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.tbox_runtime_contracts import (
    derive_ordered_member_contracts,
    derive_required_link_bindings,
)


ONTOSYN_TOP = {
    "class_local": "ChemicalSynthesis",
    "class_iri": "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis",
}
ONTOSYN_DOCUMENT_BINDING = [
    {
        "identity_slot": "{doi}",
        "target_class_locals": ["Document", "BibliographicResource"],
        "materialization_iteration": 1,
    }
]


ROOT = Path(__file__).resolve().parents[1]


def test_ontosynthesis_tbox_derives_previous_domain_runtime_contracts() -> None:
    tbox = ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
    assert derive_ordered_member_contracts(tbox) == [
        {
            "collection_property_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/hasSynthesisStep"
            ),
            "member_class_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/SynthesisStep"
            ),
            "order_property_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/hasOrder"
            ),
        }
    ]
    assert derive_required_link_bindings(
        tbox_path=tbox,
        top_entity=ONTOSYN_TOP,
    ) == []
    assert derive_required_link_bindings(
        tbox_path=tbox,
        top_entity=ONTOSYN_TOP,
        external_identity_bindings=ONTOSYN_DOCUMENT_BINDING,
    ) == [
        {
            "predicate_iri": "https://www.theworldavatar.com/kg/OntoSyn/retrievedFrom",
            "identity_slot": "{doi}",
            "materialization_iteration": 1,
        }
    ]


def test_ontomock_tbox_derives_ordered_members_without_document_binding() -> None:
    tbox = ROOT / "tests" / "fixtures" / "tbox" / "ontomock.ttl"
    assert derive_ordered_member_contracts(tbox) == [
        {
            "collection_property_iri": "https://example.test/ontomock/hasAction",
            "member_class_iri": "https://example.test/ontomock/ActionBase",
            "order_property_iri": "https://example.test/ontomock/hasOrder",
        }
    ]
    assert (
        derive_required_link_bindings(
            tbox_path=tbox,
            top_entity={
                "class_local": "ProcessRun",
                "class_iri": "https://example.test/ontomock/ProcessRun",
            },
            external_identity_bindings=[
                {
                    "identity_slot": "{doi}",
                    "target_class_locals": ["SourceDoc"],
                    "materialization_iteration": 1,
                }
            ],
        )
        == []
    )
