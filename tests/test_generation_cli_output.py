"""Tests for generation CLI report output."""

from __future__ import annotations

from src.agents.scripts_and_prompts_generation.agentic_generation_main import (
    _print_utf8,
    main,
)


def test_print_utf8_handles_unicode(capsys) -> None:
    _print_utf8('{"unit":"μL"}')
    assert "μL" in capsys.readouterr().out


def test_generation_cli_forwards_formal_prompt_enhancement(
    monkeypatch, tmp_path
) -> None:
    captured = {}

    def fake_run(names, **kwargs):
        captured["names"] = names
        captured.update(kwargs)
        return {"ok": True, "reports": []}

    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.agentic_generation_main."
        "run_agentic_generation_experiment",
        fake_run,
    )
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"document_md":"Document."}', encoding="utf-8")
    assert (
        main(
            [
                "--ontology",
                "ontosynthesis",
                "--stage",
                "all",
                "--prompt-enhancement",
                "--fixture",
                str(fixture),
            ]
        )
        == 0
    )
    assert captured["prompt_enhancement"] is True
    assert captured["prompt_enhancement_fixture"] == str(fixture)


def test_generation_cli_returns_two_when_validation_needs_revision(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.agentic_generation_main."
        "run_agentic_generation_experiment",
        lambda *_args, **_kwargs: {
            "ok": False,
            "reports": [
                {
                    "ontology": "ontosynthesis",
                    "ok": False,
                    "failures": ["validation failed"],
                    "repair_history": [],
                }
            ],
        },
    )

    assert (
        main(
            [
                "--ontology",
                "ontosynthesis",
                "--stage",
                "validate",
            ]
        )
        == 2
    )


def test_generation_cli_requires_explicit_true_summary_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.agentic_generation_main."
        "run_agentic_generation_experiment",
        lambda *_args, **_kwargs: {"reports": []},
    )

    assert (
        main(
            [
                "--ontology",
                "ontosynthesis",
                "--stage",
                "validate",
            ]
        )
        == 2
    )
