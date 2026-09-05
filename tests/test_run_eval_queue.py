from __future__ import annotations

import json
from pathlib import Path

from scripts.run_eval_queue import (
    KNOWN_STEPS,
    collect_paper_index,
    is_stalled,
    latest_log_mtime,
    order_hashes,
    paper_weight,
    parse_int_list,
    should_spawn_fillin,
    split_extract_rest_steps,
    summarize_costs,
    truncate_steps,
)


def test_truncate_steps_keeps_prefix_through_until() -> None:
    assert truncate_steps(list(KNOWN_STEPS), "top_entity_kg_building") == [
        "pdf_conversion",
        "section_classification",
        "stitching",
        "top_entity_extraction",
        "top_entity_kg_building",
    ]


def test_truncate_steps_preserves_configured_subset() -> None:
    steps = ["stitching", "main_ontology_extractions"]
    assert truncate_steps(steps, "stitching") == ["stitching"]


def test_parse_int_list() -> None:
    assert parse_int_list("2,3") == [2, 3]
    assert parse_int_list("") == []


def test_longest_first_uses_prior_elapsed_then_entity_count(tmp_path: Path) -> None:
    prior = tmp_path / "prior"
    current = tmp_path / "current"
    (prior / "aaaaaa").mkdir(parents=True)
    (prior / "bbbbbb" / "pre_extraction").mkdir(parents=True)
    (prior / "bbbbbb" / "pre_extraction" / "entity_text_one.txt").write_text("x")
    (prior / "bbbbbb" / "pre_extraction" / "entity_text_two.txt").write_text("x")
    (current / "cccccc").mkdir(parents=True)

    ranked = order_hashes(
        ["cccccc", "bbbbbb", "aaaaaa"],
        mode="longest-first",
        prior_elapsed={"aaaaaa": 90.0},
        prior_runtime=prior,
        current_runtime=current,
    )

    assert [row["hash"] for row in ranked] == ["aaaaaa", "bbbbbb", "cccccc"]
    assert ranked[0]["weight_source"] == "prior_elapsed"
    assert ranked[1]["weight_source"] == "entity_count"


def test_source_order_keeps_input_sequence(tmp_path: Path) -> None:
    ranked = order_hashes(
        ["cccccc", "aaaaaa"],
        mode="source",
        prior_elapsed={"aaaaaa": 99.0},
        prior_runtime=tmp_path,
        current_runtime=tmp_path,
    )
    assert [row["hash"] for row in ranked] == ["cccccc", "aaaaaa"]


def test_paper_weight_falls_back_when_no_signal(tmp_path: Path) -> None:
    weight, source = paper_weight(
        "deadbeef",
        prior_elapsed={},
        prior_runtime=tmp_path,
        current_runtime=tmp_path,
    )
    assert weight == 0.0
    assert source == "fallback"


def test_summarize_costs_and_paper_index(tmp_path: Path) -> None:
    cost_log = tmp_path / "openrouter_costs.jsonl"
    cost_log.write_text(
        json.dumps(
            {
                "event": "completed",
                "actual_cost_usd": 1.25,
                "model": "openai/gpt-4.1",
            }
        )
        + "\n"
        + json.dumps({"event": "error", "actual_cost_usd": 9.0})
        + "\n",
        encoding="utf-8",
    )
    paper = tmp_path / "abc123" / "mcp_run"
    paper.mkdir(parents=True)
    (paper / "iter3_hints_one.txt").write_text("hint")
    (tmp_path / "abc123" / ".main_ontology_extractions_done").write_text("")

    cost = summarize_costs(cost_log)
    index = collect_paper_index(tmp_path, ["abc123"])

    assert cost["calls"] == 1
    assert cost["actual_cost_usd"] == 1.25
    assert index["abc123"]["iter3_hints"] == 1
    assert index["abc123"]["iter4_hints"] == 0
    assert index["abc123"]["done_main_ontology"] is True
    assert index["abc123"]["done_main_kg"] is False
    assert index["abc123"]["done_mop_derivation"] is False
    assert index["abc123"]["entity_ttls"] == 0


def test_truncate_steps_includes_main_kg_building() -> None:
    assert truncate_steps(list(KNOWN_STEPS), "main_kg_building")[-1] == "main_kg_building"
    assert "main_ontology_extractions" in truncate_steps(
        list(KNOWN_STEPS), "main_kg_building"
    )


def test_is_stalled_uses_log_mtime_when_present() -> None:
    assert is_stalled(started=0.0, last_log=100.0, now=1600.0, stall_seconds=1500) is True
    assert is_stalled(started=0.0, last_log=100.0, now=1599.0, stall_seconds=1500) is False
    assert is_stalled(started=0.0, last_log=None, now=10.0, stall_seconds=1500) is False
    assert is_stalled(started=0.0, last_log=None, now=10.0, stall_seconds=0) is False


def test_latest_log_mtime_picks_newest(tmp_path: Path) -> None:
    older = tmp_path / "w00.log"
    newer = tmp_path / "w00.err.log"
    older.write_text("a")
    newer.write_text("b")
    older.touch()
    newer.touch()
    assert latest_log_mtime([tmp_path / "missing.log"]) is None
    assert latest_log_mtime([older, newer]) == newer.stat().st_mtime


def test_should_spawn_fillin_when_one_worker_is_stalled() -> None:
    now = 10_000.0
    assert should_spawn_fillin(
        queued=3,
        in_progress=[
            {"started": 1000.0, "last_log": 2000.0},
            {"started": 8000.0, "last_log": 9900.0},
        ],
        now=now,
        stall_seconds=1500,
        target_workers=2,
        max_workers=6,
        current_worker_count=2,
    ) is True
    assert should_spawn_fillin(
        queued=3,
        in_progress=[
            {"started": 8000.0, "last_log": 9900.0},
            {"started": 8100.0, "last_log": 9800.0},
        ],
        now=now,
        stall_seconds=1500,
        target_workers=2,
        max_workers=6,
        current_worker_count=2,
    ) is False
    assert should_spawn_fillin(
        queued=0,
        in_progress=[{"started": 1000.0, "last_log": 2000.0}],
        now=now,
        stall_seconds=1500,
        target_workers=2,
        max_workers=6,
        current_worker_count=1,
    ) is False
    assert should_spawn_fillin(
        queued=3,
        in_progress=[{"started": 1000.0, "last_log": 2000.0}],
        now=now,
        stall_seconds=1500,
        target_workers=2,
        max_workers=2,
        current_worker_count=2,
    ) is False


def test_split_extract_rest_steps_keeps_main_extraction_first() -> None:
    extract, rest = split_extract_rest_steps(
        [
            "top_entity_extraction",
            "top_entity_kg_building",
            "main_ontology_extractions",
            "main_kg_building",
            "extensions_extractions",
            "extensions_kg_building",
        ]
    )
    assert extract[-1] == "main_ontology_extractions"
    assert rest[0] == "main_kg_building"
    assert split_extract_rest_steps(
        ["top_entity_extraction", "main_ontology_extractions"]
    ) is None
