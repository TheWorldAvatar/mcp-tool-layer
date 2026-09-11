from __future__ import annotations

import json
import sys
from pathlib import Path

from src.extraction_runtime.names import entity_artifact_name, entity_scope_name
from src.kg_building.experiment_protocol import ox_summary_fields, resolve_protocol
from src.kg_building.pipeline.main_kg.traces import write_main_kg_token_trace

OX = Path(__file__).resolve().parents[1] / "src" / "kg_building" / "ontologx"
if str(OX) not in sys.path:
    sys.path.insert(0, str(OX))

from kg_token_budget import entity_kg_building_budget  # noqa: E402


def _runtime(tmp_path: Path, *, label: str, uri: str) -> Path:
    runtime = tmp_path / "runtime" / "a014d993"
    (runtime / "mcp_run").mkdir(parents=True)
    (runtime / "mcp_run" / "iter1_top_entities.json").write_text(
        json.dumps([{"label": label, "uri": uri}], indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime


def test_main_kg_trace_is_pipeline_equivalent_budget(tmp_path: Path) -> None:
    label = "UMC-1"
    uri = "http://example.org/umc-1"
    runtime = _runtime(tmp_path, label=label, uri=uri)
    dest = write_main_kg_token_trace(
        doi_folder=runtime,
        entity_label=label,
        entity_uri=uri,
        metadata={
            "aggregated_usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "total_tokens": 1500,
                "calls": 4,
            }
        },
    )
    assert dest.name == f"{entity_scope_name(label, uri)}.trace.json"
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["usage"]["total_tokens"] == 1500
    budget = entity_kg_building_budget("a014d993", label, runtime=runtime)
    assert budget["total_tokens"] == 1500
    assert budget["by_dir"] == {"main_kg_building": 1500}


def test_main_kg_trace_is_not_added_to_official_iter_traces(tmp_path: Path) -> None:
    label = "UMC-1"
    uri = "http://example.org/umc-1"
    runtime = _runtime(tmp_path, label=label, uri=uri)
    scope = entity_scope_name(label, uri)
    official = runtime / "responses" / "iter2_kg_building"
    official.mkdir(parents=True)
    (official / f"{scope}.trace.json").write_text(
        json.dumps({"usage": {"total_tokens": 9000, "calls": 9}}) + "\n",
        encoding="utf-8",
    )
    write_main_kg_token_trace(
        doi_folder=runtime,
        entity_label=label,
        entity_uri=uri,
        metadata={"aggregated_usage": {"total_tokens": 1500, "calls": 4}},
    )
    budget = entity_kg_building_budget("a014d993", label, runtime=runtime)
    assert budget["total_tokens"] == 1500


def test_reuse_published_ttl_requires_hints_not_newer(tmp_path: Path) -> None:
    from src.kg_building.pipeline.main_kg.run import should_reuse_published_ttl

    doi = tmp_path / "paper"
    mcp = doi / "mcp_run"
    out = doi / "ontosynthesis_output"
    mcp.mkdir(parents=True)
    out.mkdir()
    dest = out / "UMC-1.ttl"
    dest.write_text("@prefix : <http://example.org/> .\n:x a :Y .\n", encoding="utf-8")
    hint = mcp / "iter2_hints_UMC-1.txt"
    hint.write_text("SEMANTIC_HINTS_V1\n", encoding="utf-8")
    older = dest.stat().st_mtime - 10
    import os

    os.utime(dest, (older, older))
    assert should_reuse_published_ttl(dest, doi_folder=doi, entity_safe="UMC-1") is False
    newer = hint.stat().st_mtime + 10
    os.utime(dest, (newer, newer))
    assert should_reuse_published_ttl(dest, doi_folder=doi, entity_safe="UMC-1") is True


def test_medical_hints_attach_main_kg_budget(tmp_path: Path) -> None:
    from extraction_hints import set_hint_runs
    from medical_hints import load_medical_entities

    label = "Komplette Thymektomie"
    uri = "http://example.org/case-1"
    hash_id = "23a00605"
    runtime = tmp_path / "runtime" / hash_id
    (runtime / "mcp_run").mkdir(parents=True)
    (runtime / "mcp_run" / "iter1_top_entities.json").write_text(
        json.dumps([{"label": label, "uri": uri}], indent=2) + "\n",
        encoding="utf-8",
    )
    scope = entity_scope_name(label, uri)
    artifact = entity_artifact_name(label)
    (runtime / "mcp_run" / f"iter2_hints_{artifact}.txt").write_text("SEMANTIC_HINTS_V1\n", encoding="utf-8")
    write_main_kg_token_trace(
        doi_folder=runtime,
        entity_label=label,
        entity_uri=uri,
        metadata={"aggregated_usage": {"total_tokens": 17002, "calls": 3}},
    )
    set_hint_runs([str(tmp_path)])
    try:
        entities = load_medical_entities(hash_id)
    finally:
        set_hint_runs(None)
    assert len(entities) == 1
    assert entities[0].token_budget == 17002
    assert entities[0].budget_detail["by_dir"] == {"main_kg_building": 17002}


def test_medical_hints_match_umlaut_artifact_ledgers(tmp_path: Path) -> None:
    from extraction_hints import set_hint_runs
    from medical_hints import load_medical_entities

    label = "Operative Drainage der Brustwand oder Pleurahöhle ohne Rippenresektion"
    uri = "http://example.org/case-umlaut"
    hash_id = "6cc343f0"
    runtime = tmp_path / "runtime" / hash_id
    (runtime / "mcp_run").mkdir(parents=True)
    (runtime / "mcp_run" / "iter1_top_entities.json").write_text(
        json.dumps([{"label": label, "uri": uri}], indent=2) + "\n",
        encoding="utf-8",
    )
    artifact = entity_artifact_name(label)
    scope = entity_scope_name(label, uri)
    assert artifact != scope
    (runtime / "mcp_run" / f"iter2_hints_{artifact}.txt").write_text("SEMANTIC_HINTS_V1\n", encoding="utf-8")
    write_main_kg_token_trace(
        doi_folder=runtime,
        entity_label=label,
        entity_uri=uri,
        metadata={"aggregated_usage": {"total_tokens": 111, "calls": 1}},
    )
    set_hint_runs([str(tmp_path)])
    try:
        entities = load_medical_entities(hash_id)
    finally:
        set_hint_runs(None)
    assert entities[0].path.name == f"iter2_hints_{artifact}.txt"
    assert entities[0].token_budget == 111


def test_ox_summary_records_equivalent_pipeline_budget() -> None:
    fields = ox_summary_fields(resolve_protocol("generic-strict"))
    assert fields["pipeline_token_budget"] == "equivalent"
    assert fields["from_main_run"] is None
    assert fields["official_onepass_guidance"] is False
    full = ox_summary_fields(resolve_protocol("full-prompt"))
    assert full["official_onepass_guidance"] is True
    assert full["pipeline_token_budget"] == "equivalent"
