from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.semantic_loop_core import (
    ReactBuildRequest,
    load_semantic_loop_config,
    run_react_stage,
)


ROOT = Path(__file__).resolve().parents[1]


def test_ontosynthesis_config_selects_om2_without_core_domain_symbols() -> None:
    config = load_semantic_loop_config(
        ROOT / "configs/semantic_loops/ontosynthesis.json",
        repository_root=ROOT,
    )

    assert config.ontology_name == "ontosynthesis"
    assert config.unit_system.id == "om2"
    assert config.unit_system.reasoner_violation_keys == (
        "om2_quantity_violations",
    )
    assert all(path.is_absolute() for path in config.tbox_paths)


def test_medical_config_disables_unit_policy() -> None:
    config = load_semantic_loop_config(
        ROOT / "configs/semantic_loops/medical.json",
        repository_root=ROOT,
    )

    assert config.ontology_name == "medical"
    assert config.unit_system.id == "none"
    assert config.unit_system.reasoner_violation_keys == ()
    assert "MedicalCase" in config.required_coverage


def test_new_core_exposes_only_react_adapter_contract(tmp_path: Path) -> None:
    calls: list[ReactBuildRequest] = []

    class Adapter:
        def build_abox(self, request: ReactBuildRequest) -> dict:
            calls.append(request)
            return {"ok": True, "abox_path": str(request.abox_path)}

    request = ReactBuildRequest(
        artifact_root=tmp_path / "artifacts",
        meta_task_config=tmp_path / "meta.json",
        document_text="source",
        abox_path=tmp_path / "abox.ttl",
        runtime_root=tmp_path / "runtime",
        doi="example",
    )

    assert run_react_stage(Adapter(), request)["ok"] is True
    assert calls == [request]


def test_config_rejects_unknown_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "loop.json"
    config_path.write_text('{"schema_version":"unknown"}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported semantic loop config schema"):
        load_semantic_loop_config(config_path, repository_root=tmp_path)
