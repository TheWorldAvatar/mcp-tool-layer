from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from graph_merge import (
    attach_subgraph,
    canonicalize_reused,
    merge_graphs,
    reusable_inventory,
    reusable_subgraph,
    reused_node_ids,
    scoped_reuse_inventory,
    seed_reusable,
)
from graph_types import GraphDocument, Node, Relationship
from prompt_builder import (
    DEFAULT_ONTOLOGX_PROMPT,
    DEFAULT_TBOX,
    ENTITY_REUSE_CONTRACT,
    FAITHFUL_ONTOLOGX_PROMPT,
    FROM_EXTRACTION_CONTRACT,
    FULL_HINTS_CONTRACT,
    LAYERED_CONTRACT,
    OUTPUT_CONTRACT,
    build_system_prompt,
    load_faithful_ontologx_prompt,
    load_original_ontologx_prompt,
)
from run import (
    _inject_entity_global_context,
    _parse_layered_entity,
    _publish_global_reuse,
    _reuse_seed,
)


def _graph(*nodes, rels=()):
    by_id = {node.id: node for node in nodes}
    relationships = [
        Relationship(source=by_id[src], target=by_id[tgt], type=typ) for src, typ, tgt in rels
    ]
    return GraphDocument(nodes=list(nodes), relationships=relationships, source=None)


def test_entity_prompt_injects_pipeline_global_context(tmp_path):
    paper_dir = tmp_path / "aaf9ce20"
    hint_dir = paper_dir / "mcp_run"
    hint_dir.mkdir(parents=True)
    hint_path = hint_dir / "iter3_hints_VMOC-6.txt"
    hint_path.write_text("hint", encoding="utf-8")
    (paper_dir / "global_procedure_context.json").write_text(
        json.dumps(
            {
                "resolution": {
                    "schema_version": "global-procedure-context.v1",
                    "contexts": [
                        {
                            "context_id": "G001",
                            "context_class_iri": (
                                "https://www.theworldavatar.com/kg/OntoSyn/"
                                "VesselEnvironment"
                            ),
                            "target_property_iri": (
                                "https://www.theworldavatar.com/kg/OntoSyn/"
                                "hasVesselEnvironment"
                            ),
                            "canonical_value": "Argon atmosphere",
                            "source_evidence": "All experiments were performed under argon.",
                            "declared_scope": "Experimental section",
                            "scope_kind": "section",
                            "inheritance_rule": (
                                "propagate_to_all_compatible_operations_in_declared_scope_"
                                "unless_overridden"
                            ),
                            "exceptions": [],
                        }
                    ],
                    "unresolved_references": [],
                    "rationale": "Section-wide argon declaration.",
                }
            }
        ),
        encoding="utf-8",
    )

    prompt = _inject_entity_global_context(
        "entity instructions",
        SimpleNamespace(path=hint_path),
    )

    assert "---- GLOBAL_PROCEDURE_CONTEXT: BEGIN ----" in prompt
    assert '"canonical_value": "Argon atmosphere"' in prompt
    assert prompt.count("---- GLOBAL_PROCEDURE_CONTEXT: BEGIN ----") == 1


def test_paper_merge_reuses_document_not_chemical_synthesis():
    first = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-16"}),
        Node(id="doc1", type="bibo:Document", properties={"rdfs:label": "10.example/doi"}),
        rels=(("cs1", "ontosyn:retrievedFrom", "doc1"),),
    )
    second = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-17"}),
        Node(id="doc1", type="bibo:Document", properties={"rdfs:label": "10.example/doi"}),
        rels=(("cs1", "ontosyn:retrievedFrom", "doc1"),),
    )
    merged = attach_subgraph(first, second, reuse="paper")
    docs = [node for node in merged.nodes if node.type == "bibo:Document"]
    syntheses = [node for node in merged.nodes if node.type == "ontosyn:ChemicalSynthesis"]
    assert len(docs) == 1
    assert docs[0].id == "doc1"
    assert len(syntheses) == 2
    assert {node.properties["rdfs:label"] for node in syntheses} == {"VMOP-16", "VMOP-17"}


def test_paper_merge_does_not_collapse_chemical_inputs():
    first = _graph(
        Node(
            id="ci1",
            type="ontosyn:ChemicalInput",
            properties={"rdfs:label": "DMF", "ontosyn:hasAmount": "10 mL"},
        )
    )
    second = _graph(
        Node(
            id="ci1",
            type="ontosyn:ChemicalInput",
            properties={"rdfs:label": "DMF", "ontosyn:hasAmount": "15 mL"},
        )
    )
    merged = attach_subgraph(first, second, reuse="paper")
    inputs = [node for node in merged.nodes if node.type == "ontosyn:ChemicalInput"]
    assert len(inputs) == 2
    assert {node.properties["ontosyn:hasAmount"] for node in inputs} == {"10 mL", "15 mL"}


def test_paper_merge_reuses_atmosphere():
    first = _graph(
        Node(id="ve1", type="ontosyn:VesselEnvironment", properties={"rdfs:label": "argon"}),
    )
    second = _graph(
        Node(id="atm", type="ontosyn:VesselEnvironment", properties={"rdfs:label": "argon"}),
    )
    merged = attach_subgraph(first, second, reuse="paper")
    envs = [node for node in merged.nodes if node.type == "ontosyn:VesselEnvironment"]
    assert len(envs) == 1
    assert envs[0].id == "ve1"


def test_reusable_subgraph_drops_occurrence_local_nodes():
    graph = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-17"}),
        Node(id="doc1", type="bibo:Document", properties={"rdfs:label": "10.example/doi"}),
        Node(id="ci1", type="ontosyn:ChemicalInput", properties={"rdfs:label": "DMF"}),
        Node(id="ve1", type="ontosyn:VesselEnvironment", properties={"rdfs:label": "argon"}),
        rels=(
            ("cs1", "ontosyn:retrievedFrom", "doc1"),
            ("cs1", "ontosyn:hasChemicalInput", "ci1"),
        ),
    )
    seed = reusable_subgraph(graph)
    assert {node.id for node in seed.nodes} == {"doc1", "ve1"}
    assert reused_node_ids(seed, graph) == ["doc1", "ve1"]
    text = reusable_inventory(graph)
    assert "id=doc1" in text
    assert "id=cs1" not in text


def test_layer_mode_reuses_exact_chemical_input_id():
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
    attached = attach_subgraph(base, delta, reuse="layer")
    assert len(attached.nodes) == 1
    assert attached.nodes[0].id == "ci1"
    assert attached.nodes[0].properties["ontosyn:hasAmount"] == "0.05 g"


def test_layer_mode_keeps_same_label_chemical_input_occurrences_distinct():
    base = _graph(
        Node(
            id="def-5",
            type="ontosyn:ChemicalInput",
            properties={"rdfs:label": "DEF", "ontosyn:hasAmount": "5 mL"},
        ),
    )
    delta = _graph(
        Node(
            id="def-10",
            type="ontosyn:ChemicalInput",
            properties={"rdfs:label": "DEF", "ontosyn:hasAmount": "10 mL"},
        ),
    )
    attached = attach_subgraph(base, delta, reuse="layer")
    inputs = [node for node in attached.nodes if node.type == "ontosyn:ChemicalInput"]
    assert {node.id for node in inputs} == {"def-5", "def-10"}
    assert {node.properties["ontosyn:hasAmount"] for node in inputs} == {"5 mL", "10 mL"}


def test_merge_graphs_paper_mode_keeps_two_syntheses():
    graphs = [
        _graph(
            Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "A"}),
            Node(id="doc1", type="bibo:Document", properties={"rdfs:label": "doi"}),
        ),
        _graph(
            Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "B"}),
            Node(id="doc1", type="bibo:Document", properties={"rdfs:label": "doi"}),
        ),
    ]
    merged = merge_graphs(graphs)
    assert len([n for n in merged.nodes if n.type == "ontosyn:ChemicalSynthesis"]) == 2
    assert len([n for n in merged.nodes if n.type == "bibo:Document"]) == 1


def test_mop_reused_only_with_matching_ccdc():
    first = _graph(
        Node(
            id="mop1",
            type="ontomops:MetalOrganicPolyhedron",
            properties={"rdfs:label": "VMOP-17", "ontomops:hasCCDCNumber": "1234567"},
        )
    )
    same = _graph(
        Node(
            id="mop2",
            type="ontomops:MetalOrganicPolyhedron",
            properties={"rdfs:label": "other-name", "ontomops:hasCCDCNumber": "1234567"},
        )
    )
    other = _graph(
        Node(
            id="mop3",
            type="ontomops:MetalOrganicPolyhedron",
            properties={"rdfs:label": "VMOP-17"},
        )
    )
    assert len(attach_subgraph(first, same, reuse="paper").nodes) == 1
    assert len(attach_subgraph(first, other, reuse="paper").nodes) == 2


def test_reuse_contract_is_in_system_prompt():
    prompt = build_system_prompt(
        entity_reuse=True,
        per_entity=True,
        from_extraction=True,
    )
    assert "REUSABLE_ENTITIES" in ENTITY_REUSE_CONTRACT
    assert "check_existing_" in prompt
    assert "Do NOT reuse across syntheses" in prompt
    assert "Map hasAmount" in prompt
    assert "0.045 g, 0.276 mmol" in prompt
    assert "ChemicalInput is occurrence-local" in prompt


def test_original_ontologx_prompt_profile_is_verbatim():
    prompt = load_original_ontologx_prompt()

    assert prompt == DEFAULT_ONTOLOGX_PROMPT.read_text(encoding="utf-8")
    assert "top-tier cybersecurity expert" in prompt
    assert OUTPUT_CONTRACT not in prompt
    assert "Authoritative OntoSynthesis T-Box" not in prompt


def test_faithful_ontologx_prompt_is_short_domain_analog():
    prompt = load_faithful_ontologx_prompt()
    tbox = DEFAULT_TBOX.read_text(encoding="utf-8")

    assert prompt == FAITHFUL_ONTOLOGX_PROMPT.read_text(encoding="utf-8")
    assert "# Overview" in prompt
    assert "# Rules" in prompt
    assert "# Strict Compliance" in prompt
    assert "ontosyn:ChemicalSynthesis" in prompt
    assert "OntoSynthesis" in prompt
    assert "top-tier cybersecurity expert" not in prompt
    assert "olx" not in prompt
    assert "Event" not in prompt
    assert OUTPUT_CONTRACT not in prompt
    assert FROM_EXTRACTION_CONTRACT not in prompt
    assert LAYERED_CONTRACT not in prompt
    assert FULL_HINTS_CONTRACT not in prompt
    assert ENTITY_REUSE_CONTRACT not in prompt
    assert "Authoritative OntoSynthesis T-Box" not in prompt
    assert "Ontology Schema - Structured Property Mapping" not in prompt
    assert "Core atomicity — mandatory" not in prompt
    assert tbox not in prompt
    assert len(prompt) < 2000


def test_ontosynthesis_profile_still_embeds_tbox():
    prompt = build_system_prompt(
        entity_reuse=True,
        per_entity=True,
        from_extraction=True,
        layered=True,
    )
    assert OUTPUT_CONTRACT in prompt
    assert "Authoritative OntoSynthesis T-Box" in prompt
    assert "Ontology Schema - Structured Property Mapping" in prompt


def test_full_hints_contract_requires_complete_replacement():
    prompt = build_system_prompt(
        entity_reuse=True,
        per_entity=True,
        from_extraction=True,
        full_hints=True,
    )
    assert FULL_HINTS_CONTRACT in prompt
    assert "replacement whole-graph candidate" in prompt
    assert "same node" in prompt


def test_cross_document_seed_excludes_documents():
    prior_paper = _graph(
        Node(id="old-doc", type="bibo:Document", properties={"rdfs:label": "doi-old"}),
        Node(id="supplier-1", type="ontosyn:Supplier", properties={"rdfs:label": "Sigma"}),
    )
    current_paper = _graph(
        Node(id="new-doc", type="bibo:Document", properties={"rdfs:label": "doi-new"}),
    )
    seed = seed_reusable(current_paper, prior_paper)
    assert {node.id for node in seed.nodes} == {"new-doc", "supplier-1"}
    inventory = scoped_reuse_inventory(current_paper, prior_paper)
    assert "SAME_PAPER_REUSABLE_ENTITIES" in inventory
    assert "CROSS_DOCUMENT_REUSABLE_ENTITIES" in inventory
    assert "old-doc" not in inventory


def test_layered_run_publishes_and_seeds_cross_document_reuse():
    first_paper = _graph(
        Node(id="old-doc", type="bibo:Document", properties={"rdfs:label": "doi-old"}),
        Node(id="supplier-1", type="ontosyn:Supplier", properties={"rdfs:label": "Sigma"}),
        Node(
            id="argon-1",
            type="ontosyn:VesselEnvironment",
            properties={"rdfs:label": "argon"},
        ),
    )
    central = _publish_global_reuse(None, first_paper)
    assert {node.id for node in central.nodes} == {"supplier-1", "argon-1"}

    second_paper = _graph(
        Node(id="new-doc", type="bibo:Document", properties={"rdfs:label": "doi-new"}),
    )
    seed = _reuse_seed(second_paper, central, enabled=True)
    assert {node.id for node in seed.nodes} == {
        "new-doc",
        "supplier-1",
        "argon-1",
    }
    assert _reuse_seed(second_paper, central, enabled=False) is None


def test_canonicalize_reused_keeps_only_used_inventory_nodes():
    inventory = _graph(
        Node(id="supplier-1", type="ontosyn:Supplier", properties={"rdfs:label": "Sigma"}),
        Node(id="argon-1", type="ontosyn:VesselEnvironment", properties={"rdfs:label": "argon"}),
    )
    graph = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "A"}),
        Node(id="supplier-new", type="ontosyn:Supplier", properties={"rdfs:label": "Sigma"}),
        rels=(("cs1", "ontosyn:hasSupplier", "supplier-new"),),
    )
    canonical = canonicalize_reused(graph, inventory)
    assert {node.id for node in canonical.nodes} == {"cs1", "supplier-1"}
    assert canonical.relationships[0].target.id == "supplier-1"
    assert "argon-1" not in {node.id for node in canonical.nodes}


def test_iter3_can_reuse_seed_candidate_unused_by_iter2(tmp_path):
    hint_dir = tmp_path / "mcp_run"
    hint_dir.mkdir()
    iter2_path = hint_dir / "iter2_hints_MOP.txt"
    iter3_path = hint_dir / "iter3_hints_MOP.txt"
    iter2_path.write_text("iter2", encoding="utf-8")
    iter3_path.write_text("iter3", encoding="utf-8")
    entity = SimpleNamespace(
        key="ChemicalSynthesis-1",
        label="MOP",
        run="test",
        path=iter3_path,
        iter_layers=lambda: [
            (2, iter2_path, "iter2", None),
            (3, iter3_path, "iter3", None),
        ],
    )
    seed = _graph(
        Node(
            id="supplier-1",
            type="ontosyn:Supplier",
            properties={"rdfs:label": "Sigma-Aldrich"},
        ),
    )

    class FakeOntoLogX:
        def __init__(self):
            self.prompts = []

        def parse(self, text, context, paper_hash, **kwargs):
            self.prompts.append(kwargs["extra_human"])
            graph = (
                _graph(
                    Node(
                        id="cs1",
                        type="ontosyn:ChemicalSynthesis",
                        properties={"rdfs:label": "MOP"},
                    ),
                )
                if context["layer"] == 2
                else _graph(
                    Node(
                        id="cs1",
                        type="ontosyn:ChemicalSynthesis",
                        properties={"rdfs:label": "MOP"},
                    ),
                    Node(
                        id="new-supplier",
                        type="ontosyn:Supplier",
                        properties={"rdfs:label": "Sigma-Aldrich"},
                    ),
                    rels=(("cs1", "ontosyn:hasSupplier", "new-supplier"),),
                )
            )
            usage = SimpleNamespace(
                calls=1,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                call_details=[],
                stop_reason="test",
            )
            return graph, True, [], usage

    ontologx = FakeOntoLogX()
    graph, *_ = _parse_layered_entity(
        ontologx,
        entity,
        {"doi": "10.example/test", "hash": "12345678", "title": "Test"},
        SimpleNamespace(from_extraction=False, no_kg_budget=True),
        seed_graph=seed,
    )

    assert "id=supplier-1" in ontologx.prompts[1]
    assert {node.id for node in graph.nodes} == {"cs1", "supplier-1"}
    assert graph.relationships[0].target.id == "supplier-1"
