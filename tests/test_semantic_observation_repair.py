from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.agents.scripts_and_prompts_generation import pure_llm_generation


def _semantic_report(score: float) -> dict[str, object]:
    return {
        "consensus": {
            "overall_score": score,
            "scores": {
                name: score
                for name in (
                    "groundedness",
                    "coverage",
                    "semantic_correctness",
                    "quantity_fidelity",
                    "hallucination_control",
                )
            },
        },
        "acceptance": {
            "accepted": score >= 0.95,
            "overall_score": score,
        },
        "observations": [],
    }


def test_semantic_repair_delegates_to_transactional_editor(
    tmp_path: Path, monkeypatch
) -> None:
    prompt = tmp_path / "prompts" / "extract.md"
    prompt.parent.mkdir()
    prompt.write_text("Extract facts.", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_editor(**kwargs):
        captured.update(kwargs)
        validation = kwargs["validate"]()
        return {"ok": validation["ok"], "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_delta_review",
        lambda **_kwargs: {
            "decision": "accept",
            "resolved_or_improved": ["semantic score improved"],
            "regressions": [],
        },
    )
    context = SimpleNamespace(
        output_root=tmp_path,
        contract={"classes": ["Neutral"]},
    )

    result = pure_llm_generation.run_semantic_observation_repair(
        model_name="judge",
        context=context,
        diagnosis={
            "repair_kind": "prompt",
            "summary": "Improve generic extraction contract.",
            "target_artifacts": [str(prompt)],
            "causal_findings": [],
        },
        before_semantic_report=_semantic_report(0.4),
        validate_candidate=lambda: {
            "health_ok": True,
            "semantic_report": _semantic_report(0.8),
        },
    )

    assert result["ok"] is True
    assert captured["targets"] == [prompt.resolve()]
    assert "fixture entities" in str(captured["task_prompt"])
