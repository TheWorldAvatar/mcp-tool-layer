from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from extraction_hints import HintEntity
from iteration_guides import (
    attach_pipeline_identity,
    format_iteration_surface,
    load_identity_records,
    match_identity_record,
)
from prompt_builder import LAYERED_CONTRACT, entity_human_suffix
from run import _parse_full_hints_entity, _parse_layered_entity


def test_iter3_surface_is_closed_and_excludes_iter4():
    text = format_iteration_surface(3)
    assert "hasAddedChemicalInput" in text
    assert "ChemicalInput" in text
    assert "hasSynthesisStep" in text
    assert "hasYield" not in text
    assert "hasEquipment" not in text


def test_iter4_surface_is_remainder_only():
    text = format_iteration_surface(4)
    assert "hasYield" in text
    assert "hasEquipment" in text
    assert "hasAddedChemicalInput" not in text
    assert "Add" not in text or "Owned classes: (none" in text


def test_iter2_surface_owns_foundation_links():
    text = format_iteration_surface(2)
    assert "retrievedFrom" in text
    assert "ChemicalOutput" in text
    assert "hasSynthesisStep" not in text


def test_layered_human_suffix_includes_uri_dossier_and_surface():
    dossier = {
        "uri": "https://example.org/cs/1",
        "label": "MOP",
        "explicit_iteration_1_facts": [],
    }
    text = entity_human_suffix(
        "ChemicalSynthesis-1",
        "MOP",
        layer=3,
        entity_uri="https://example.org/cs/1",
        identity_dossier=dossier,
        include_iteration_surface=True,
    )
    assert "https://example.org/cs/1" in text
    assert "PIPELINE ENTITY IDENTITY DOSSIER" in text
    assert "ITERATION-OWNED SURFACE (iter3 only)" in text
    assert "hasAddedChemicalInput" in text
    assert "closed iteration-owned surface" in LAYERED_CONTRACT


def test_full_graph_suffix_has_identity_but_no_layer_surface():
    text = entity_human_suffix(
        "ChemicalSynthesis-1",
        "MOP",
        entity_uri="https://example.org/cs/1",
        identity_dossier={"uri": "https://example.org/cs/1", "label": "MOP"},
        include_iteration_surface=False,
    )
    assert "PIPELINE ENTITY URI" in text
    assert "ITERATION-OWNED SURFACE" not in text


def test_match_identity_by_label_and_anchor(tmp_path):
    runtime = tmp_path / "a527729b"
    mcp = runtime / "mcp_run"
    mcp.mkdir(parents=True)
    (mcp / "iter1_top_entities.json").write_text(
        json.dumps(
            [
                {
                    "uri": "https://example.org/cs/1",
                    "label": "Synthesis of capsule (1)",
                    "source_anchor": "ChemicalSynthesis-1 [Synthesis of capsule (1)]",
                    "identity_dossier": {
                        "uri": "https://example.org/cs/1",
                        "label": "Synthesis of capsule (1)",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    records = load_identity_records(runtime)
    by_label = match_identity_record(
        records, key="ChemicalSynthesis-9", label="Synthesis of capsule (1)"
    )
    by_key = match_identity_record(records, key="ChemicalSynthesis-1", label="other")
    assert by_label["uri"] == "https://example.org/cs/1"
    assert by_key["uri"] == "https://example.org/cs/1"

    entity = HintEntity(
        key="ChemicalSynthesis-1",
        label="Synthesis of capsule (1)",
        path=mcp / "iter3_hints_x.txt",
        run="test",
        text="hint",
    )
    attached = attach_pipeline_identity(entity, runtime)
    assert attached.uri == "https://example.org/cs/1"
    assert attached.identity_dossier["uri"] == "https://example.org/cs/1"


def test_layered_and_full_hints_inject_identity_differently(tmp_path):
    hint_dir = tmp_path / "mcp_run"
    hint_dir.mkdir()
    iter2 = hint_dir / "iter2_hints_MOP.txt"
    iter3 = hint_dir / "iter3_hints_MOP.txt"
    iter2.write_text("iter2", encoding="utf-8")
    iter3.write_text("iter3", encoding="utf-8")
    entity = SimpleNamespace(
        key="ChemicalSynthesis-1",
        label="MOP",
        run="test",
        path=iter3,
        uri="https://example.org/cs/1",
        identity_dossier={"uri": "https://example.org/cs/1", "label": "MOP"},
        token_budget=100,
        slug="cs1",
        iter_layers=lambda: [
            (2, iter2, "iter2", None),
            (3, iter3, "iter3", None),
        ],
        full_hints=lambda: SimpleNamespace(
            text="ITER2\nITER3\n",
            layers=(2, 3),
            paths=(iter2, iter3),
        ),
    )

    class FakeOntoLogX:
        def __init__(self):
            self.prompts = []

        def parse(self, text, context, paper_hash, **kwargs):
            self.prompts.append(kwargs["extra_human"])
            usage = SimpleNamespace(
                calls=1,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                call_details=[],
                stop_reason="test",
            )
            from graph_types import GraphDocument, Node

            graph = GraphDocument(
                nodes=[Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={})],
                relationships=[],
                source=None,
            )
            return graph, True, [], usage

    layered = FakeOntoLogX()
    _parse_layered_entity(
        layered,
        entity,
        {"doi": "10.example/test", "hash": "12345678", "title": "Test"},
        SimpleNamespace(from_extraction=False, no_kg_budget=True),
    )
    assert "ITERATION-OWNED SURFACE (iter2 only)" in layered.prompts[0]
    assert "ITERATION-OWNED SURFACE (iter3 only)" in layered.prompts[1]
    assert "https://example.org/cs/1" in layered.prompts[0]
    assert "hasAddedChemicalInput" in layered.prompts[1]

    full = FakeOntoLogX()
    _parse_full_hints_entity(
        full,
        entity,
        {"doi": "10.example/test", "hash": "12345678", "title": "Test"},
        SimpleNamespace(no_kg_budget=True),
        tmp_path,
    )
    assert "https://example.org/cs/1" in full.prompts[0]
    assert "PIPELINE ENTITY IDENTITY DOSSIER" in full.prompts[0]
    assert "ITERATION-OWNED SURFACE" not in full.prompts[0]
