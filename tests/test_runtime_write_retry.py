from pathlib import Path

import pytest

from src.pipelines.main_ontology_extractions import extract


def test_write_text_recreates_parent_after_concurrent_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "mcp_run" / "hints.txt"
    real_open = open
    calls = 0

    def flaky_open(path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            target.parent.rmdir()
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", flaky_open)
    extract._write_text_with_parent(str(target), "payload")

    assert target.read_text(encoding="utf-8") == "payload"
    assert calls == 2


@pytest.mark.asyncio
async def test_semantic_shape_diagnostics_do_not_retry_extraction(
    monkeypatch, tmp_path: Path
) -> None:
    content = (
        '{"SynthesisStepList":[{"type":"SynthesisStep","label":"Heat sample"}]}'
    )

    class FakeLlm:
        calls = 0

        async def ainvoke(self, _prompt: str) -> str:
            self.calls += 1
            return content

    fake_llm = FakeLlm()

    class FakeCreator:
        def __init__(self, **_kwargs) -> None:
            pass

        def setup_llm(self) -> FakeLlm:
            return fake_llm

    monkeypatch.setattr(extract, "LLMCreator", FakeCreator)
    monkeypatch.setattr(extract, "get_extraction_model", lambda _key: "fake")
    hints_file = tmp_path / "mcp_run" / "hints" / "entity.json"

    result = await extract.run_extraction(
        doi_hash="doi",
        entity_label="entity",
        entity_uri="urn:entity",
        source_text="Heat the sample.",
        prompt_template="{paper_content}",
        model_key="fake",
        hints_file=str(hints_file),
        iter_num=3,
        extraction_validation={
            "forbid_generic_ordered_member_types": {
                "enabled": True,
                "generic_labels": ["SynthesisStep"],
                "type_keys": ["type"],
            }
        },
    )

    assert result == content
    assert hints_file.read_text(encoding="utf-8") == content
    assert fake_llm.calls == 1
