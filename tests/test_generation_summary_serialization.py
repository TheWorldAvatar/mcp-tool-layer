"""Regression tests for generation report ownership."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    run_agentic_generation_experiment,
)


def test_generation_summary_has_no_circular_report_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_result = {
        "ok": False,
        "mode": "pure_llm_unified_diff",
        "final_report": {"ok": False, "failures": ["candidate_failed"]},
        "history": [],
    }
    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.agentic_generation_runner."
        "run_pure_llm_generation_rounds",
        lambda *args, **kwargs: llm_result,
    )

    summary = run_agentic_generation_experiment(
        ["ontosynthesis"],
        output_root=tmp_path,
        generate_scripts=True,
        generate_prompts=False,
        llm_agent_generation=True,
    )

    assert not summary["ok"]
    assert summary["reports"][0]["llm_agent_run"] is llm_result
    json.dumps(summary)
