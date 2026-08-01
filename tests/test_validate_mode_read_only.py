from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    run_agentic_generation_experiment,
)


def test_validation_does_not_overwrite_existing_artifacts(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts" / "ontosynthesis"
    prompts = tmp_path / "prompts" / "ontosynthesis"
    scripts.mkdir(parents=True)
    prompts.mkdir(parents=True)
    main = scripts / "main.py"
    prompt = prompts / "EXTRACTION_ITER_1.md"
    main.write_text("SENTINEL_MAIN = True\n", encoding="utf-8")
    prompt.write_text("SENTINEL PROMPT\n", encoding="utf-8")

    run_agentic_generation_experiment(
        ["ontosynthesis"],
        output_root=tmp_path,
        generate_scripts=False,
        generate_prompts=False,
        llm_agent_generation=False,
        write_context_files=False,
    )

    assert main.read_text(encoding="utf-8") == "SENTINEL_MAIN = True\n"
    assert prompt.read_text(encoding="utf-8") == "SENTINEL PROMPT\n"
