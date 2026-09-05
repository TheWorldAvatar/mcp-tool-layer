from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from types import SimpleNamespace

from graph_merge import (
    attach_subgraph,
    complete_delta,
    graph_inventory,
    layered_graph_inventory,
    remove_prior_relationships,
)
from graph_types import GraphDocument, Node, Relationship
from ontology_graph import load_ontology_graph
from parser import complete_snapshot_instruction
from prompt_builder import LAYERED_CONTRACT
from synthesis_schema import build_dynamic_model


def _graph(*nodes, rels=()):
    by_id = {node.id: node for node in nodes}
    relationships = [
        Relationship(source=by_id[src], target=by_id[tgt], type=typ) for src, typ, tgt in rels
    ]
    return GraphDocument(nodes=list(nodes), relationships=relationships, source=None)


def test_attach_reuses_synthesis_and_adds_steps():
    base = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-17"}),
        Node(id="ci1", type="ontosyn:ChemicalInput", properties={"rdfs:label": "VCl3"}),
        rels=(("cs1", "ontosyn:hasChemicalInput", "ci1"),),
    )
    delta = _graph(
        Node(id="new_cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-17"}),
        Node(id="add1", type="ontosyn:Add", properties={"rdfs:label": "Add", "ontosyn:hasOrder": 1}),
        rels=(("new_cs", "ontosyn:hasSynthesisStep", "add1"),),
    )
    attached = attach_subgraph(base, delta)
    ids = {node.id for node in attached.nodes}
    assert "cs1" in ids
    assert "add1" in ids
    assert "new_cs" not in ids
    step_links = [
        rel for rel in attached.relationships if rel.type == "ontosyn:hasSynthesisStep"
    ]
    assert len(step_links) == 1
    assert step_links[0].source.id == "cs1"


def test_attach_overlays_amount_on_exact_input_id():
    base = _graph(
        Node(id="ci1", type="ontosyn:ChemicalInput", properties={"rdfs:label": "VCl3"}),
    )
    delta = _graph(
        Node(
            id="ci1",
            type="ontosyn:ChemicalInput",
            properties={"rdfs:label": "VCl3", "ontosyn:hasAmount": "0.05 g"},
        ),
    )
    attached = attach_subgraph(base, delta)
    assert len(attached.nodes) == 1
    assert attached.nodes[0].properties["ontosyn:hasAmount"] == "0.05 g"


def test_schema_is_not_a_graph_validator():
    Model = build_dynamic_model(load_ontology_graph(REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"))
    parsed = Model.model_validate(
        {
            "nodes": [
                {
                    "id": "Add-1",
                    "type": "ontosyn:Add",
                    "properties": [{"type": "rdfs:label", "value": "Add"}],
                }
            ],
            "relationships": [
                {
                    "source_id": "Add-1",
                    "target_id": "ChemicalInput-DMF",
                    "type": "ontosyn:hasAddedChemicalInput",
                }
            ],
        }
    )
    assert parsed.nodes[0].id == "Add-1"


def test_schema_accepts_attach_delta_without_repeating_prior_nodes():
    Model = build_dynamic_model(load_ontology_graph(REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"))
    parsed = Model.model_validate(
        {
            "nodes": [
                {
                    "id": "add-1",
                    "type": "ontosyn:Add",
                    "properties": [
                        {"type": "rdfs:label", "value": "Add"},
                        {"type": "ontosyn:hasOrder", "value": 1},
                    ],
                }
            ],
            "relationships": [
                {"source_id": "cs-1", "target_id": "add-1", "type": "ontosyn:hasSynthesisStep"},
                {"source_id": "add-1", "target_id": "ci-vos04", "type": "ontosyn:hasAddedChemicalInput"},
            ],
        }
    )
    assert [node.id for node in parsed.nodes] == ["add-1"]
    existing = _graph(
        Node(id="cs-1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "X"}),
        Node(id="ci-vos04", type="ontosyn:ChemicalInput", properties={"rdfs:label": "VOSO4"}),
    )
    completed = complete_delta(parsed, existing, "event", {})
    assert {node.id for node in completed.nodes} >= {"cs-1", "add-1", "ci-vos04"}


def test_layered_corrections_require_complete_current_layer_snapshot():
    assert "COMPLETE" in LAYERED_CONTRACT
    assert "It is not a patch" in LAYERED_CONTRACT
    assert "nodes and relationships" in LAYERED_CONTRACT
    assert "remove_relationships" in LAYERED_CONTRACT

    instruction = complete_snapshot_instruction({"layer": 3})
    assert "iter3" in instruction
    assert "not a patch" in instruction
    assert "Repeat every current-layer node and relationship" in instruction
    assert "remove_relationships" in instruction

    Model = build_dynamic_model(
        load_ontology_graph(REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl")
    )
    with pytest.raises(ValidationError):
        Model.model_validate({"relationships": []})


def test_layered_output_can_remove_an_exact_prior_relationship():
    Model = build_dynamic_model(
        load_ontology_graph(REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl")
    )
    parsed = Model.model_validate(
        {
            "nodes": [],
            "relationships": [],
            "remove_relationships": [
                {
                    "source_id": "cs1",
                    "target_id": "wrong-step",
                    "type": "ontosyn:hasSynthesisStep",
                }
            ],
        }
    )
    base = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={}),
        Node(id="wrong-step", type="ontosyn:Add", properties={}),
        Node(id="right-step", type="ontosyn:Add", properties={}),
        rels=(
            ("cs1", "ontosyn:hasSynthesisStep", "wrong-step"),
            ("cs1", "ontosyn:hasSynthesisStep", "right-step"),
        ),
    )
    edited = remove_prior_relationships(base, parsed.remove_relationships)
    assert [
        (rel.source.id, rel.type, rel.target.id) for rel in edited.relationships
    ] == [("cs1", "ontosyn:hasSynthesisStep", "right-step")]


def test_complete_delta_keeps_edges_to_prior_ids():
    existing = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-17"}),
    )

    class _Delta:
        relationships = [
            SimpleNamespace(source_id="cs1", target_id="add1", type="ontosyn:hasSynthesisStep")
        ]

        def graph(self, event, context):
            return _graph(
                Node(id="add1", type="ontosyn:Add", properties={"ontosyn:hasOrder": 1}),
            )

    completed = complete_delta(_Delta(), existing, "event", {})
    assert {node.id for node in completed.nodes} == {"cs1", "add1"}
    assert completed.relationships[0].source.id == "cs1"


def test_inventory_lists_ids():
    graph = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-17"}),
    )
    text = graph_inventory(graph)
    assert "id=cs1" in text
    assert "VMOP-17" in text


def test_inventory_serializes_all_prior_node_properties():
    graph = _graph(
        Node(
            id="heat1",
            type="ontosyn:HeatChill",
            properties={
                "rdfs:label": "Heat",
                "ontosyn:hasAmount": "5 mL",
                "ontosyn:hasDuration": "24 h",
                "ontosyn:hasTargetTemperature": "120 °C",
            },
        ),
    )
    text = graph_inventory(graph)
    assert '"ontosyn:hasAmount":"5 mL"' in text
    assert '"ontosyn:hasDuration":"24 h"' in text
    assert '"ontosyn:hasTargetTemperature":"120 °C"' in text


def test_later_layer_inventory_keeps_unused_seed_candidates_visible():
    existing = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "MOP"}),
        Node(
            id="argon-1",
            type="ontosyn:VesselEnvironment",
            properties={"rdfs:label": "argon"},
        ),
    )
    seed = _graph(
        Node(
            id="argon-1",
            type="ontosyn:VesselEnvironment",
            properties={"rdfs:label": "argon"},
        ),
        Node(
            id="supplier-1",
            type="ontosyn:Supplier",
            properties={"rdfs:label": "Sigma-Aldrich"},
        ),
    )
    text = layered_graph_inventory(existing, seed)
    existing_text, unused_text = text.split("UNUSED_REUSABLE_ENTITIES", maxsplit=1)
    assert "id=argon-1" in existing_text
    assert "id=argon-1" not in unused_text
    assert "id=supplier-1" in unused_text
    assert '"rdfs:label":"Sigma-Aldrich"' in unused_text


def test_iter_layers_finds_sibling_hint_files(tmp_path: Path):
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "baselines" / "ontologx_ontosyn"))
    from extraction_hints import HintEntity

    for layer in (2, 3, 4):
        (tmp_path / f"iter{layer}_hints_VMOP-17.txt").write_text(f"layer{layer}", encoding="utf-8")
    entity = HintEntity(
        key="ChemicalSynthesis-1",
        label="VMOP-17",
        path=tmp_path / "iter3_hints_VMOP-17.txt",
        run="test",
        text="layer3",
        budget_detail={
            "by_dir": {
                "iter2_kg_building": 10,
                "iter3_kg_building": 20,
                "iter4_kg_building": 30,
            }
        },
    )
    layers = entity.iter_layers()
    assert [item[0] for item in layers] == [2, 3, 4]
    assert layers[0][2] == "layer2"
    assert layers[1][3] == 20


def test_full_hints_combines_layers_in_order(tmp_path: Path):
    from extraction_hints import HintEntity

    for layer in (4, 2, 3):
        (tmp_path / f"iter{layer}_hints_VMOP-17.txt").write_text(
            f"ledger-{layer}", encoding="utf-8"
        )
    entity = HintEntity(
        key="ChemicalSynthesis-1",
        label="VMOP-17",
        path=tmp_path / "iter3_hints_VMOP-17.txt",
        run="test",
        text="ledger-3",
    )
    combined = entity.full_hints()
    assert combined.layers == (2, 3, 4)
    assert combined.text.index("ITER2") < combined.text.index("ITER3") < combined.text.index("ITER4")
    assert [path.name for path in combined.paths] == [
        "iter2_hints_VMOP-17.txt",
        "iter3_hints_VMOP-17.txt",
        "iter4_hints_VMOP-17.txt",
    ]


def test_full_hints_falls_back_to_available_layer(tmp_path: Path):
    from extraction_hints import HintEntity

    path = tmp_path / "iter3_hints_VMOP-17.txt"
    path.write_text("only-iter3", encoding="utf-8")
    entity = HintEntity(
        key="ChemicalSynthesis-1",
        label="VMOP-17",
        path=path,
        run="test",
        text="only-iter3",
    )
    combined = entity.full_hints()
    assert combined.layers == (3,)
    assert "only-iter3" in combined.text
