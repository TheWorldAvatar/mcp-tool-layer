from __future__ import annotations

import json
from pathlib import Path

from src.agents.mops.cbu_derivation import integration
from src.agents.mops.ontomop_derivation import agent_mop_formula
from src.pipelines.mop_derivation.derive import (
    _derivation_subprocess_env,
    perform_final_integration,
)


def test_derivation_subprocess_env_has_explicit_runtime_and_resource_paths(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    runtime_root = tmp_path / "runtime"
    ontology_dir = repository_root / "data" / "ontologies"
    ccdc_dir = ontology_dir / "ccdc"
    ccdc_dir.mkdir(parents=True)
    runtime_root.mkdir()
    cbu_database = ontology_dir / "full_cbus_with_canonical_smiles_updated.csv"
    cbu_database.write_text("formula\n", encoding="utf-8")

    child_env = _derivation_subprocess_env(
        data_dir=str(runtime_root),
        project_root_path=str(repository_root),
    )

    assert child_env["DATA_DIR"] == str(runtime_root.resolve())
    assert child_env["TWA_AGENTIC_DATA_DIR"] == str(runtime_root.resolve())
    assert child_env["DATA_LOG_DIR"] == str((runtime_root / "log").resolve())
    assert child_env["DATA_CCDC_DIR"] == str(ccdc_dir.resolve())
    assert child_env["CBU_DATABASE_PATH"] == str(cbu_database.resolve())


def _write_final_integration_inputs(runtime_root: Path, *, exact_name: bool) -> None:
    case_dir = runtime_root / "abc12345"
    full_dir = case_dir / "cbu_derivation" / "full"
    integrated_dir = case_dir / "cbu_derivation" / "integrated"
    output_dir = case_dir / "ontomops_output"
    full_dir.mkdir(parents=True)
    integrated_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    (full_dir / "Entity_A.json").write_text(
        json.dumps({"entity": "Entity A", "mop_formula": "[A]2"}),
        encoding="utf-8",
    )
    integrated_name = "Entity_A.json" if exact_name else "unrelated.json"
    (integrated_dir / integrated_name).write_text(
        json.dumps(
            {
                "entity": "Entity A",
                "metal_cbu": {"iri": "https://example.test/metal"},
                "organic_cbu": {"iri": "https://example.test/organic"},
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "ontomops_output_mapping.json").write_text(
        json.dumps({"Entity A": "entity-a.ttl"}),
        encoding="utf-8",
    )


def test_final_integration_uses_configured_data_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_final_integration_inputs(runtime_root, exact_name=True)
    calls: list[dict] = []

    def fake_write(*args, **kwargs) -> None:
        calls.append(kwargs)
        Path(kwargs["out_dir"], "Entity_A.ttl").write_text(
            "@prefix ex: <https://example.test/> .",
            encoding="utf-8",
        )

    monkeypatch.setattr(integration, "_write_integrated_ttl", fake_write)

    result = perform_final_integration(
        "abc12345",
        data_dir=str(runtime_root),
    )

    assert result is not None
    assert len(result) == 1
    assert calls[0]["data_dir"] == str(runtime_root)


def test_final_integration_does_not_discover_noncanonical_filename(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_final_integration_inputs(runtime_root, exact_name=False)

    assert perform_final_integration(
        "abc12345",
        data_dir=str(runtime_root),
    ) is None


def test_integration_reads_canonical_structured_cbu_filename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    label = "Synthesis of [V3(mu3-O)O(OH)2(BPD)1,5(HCOO)3] 1"
    structured = (
        tmp_path
        / "abc12345"
        / "cbu_derivation"
        / "metal"
        / "structured"
    )
    structured.mkdir(parents=True)
    (structured / "Synthesis_of_V3_mu3-O_O_OH_2_BPD_1_5_HCOO_3_1.json").write_text(
        json.dumps({"metal_cbu": "[V3O2(OH)2(HCO2)3]"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(integration, "DATA_DIR", str(tmp_path))

    result = integration._read_metal_cbu_pair("abc12345", label)

    assert result["formula"] == "[V3O2(OH)2(HCO2)3]"


def test_mop_formula_reader_uses_canonical_structured_filename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    label = "Synthesis of [V3(mu3-O)O(OH)2(BPD)1,5(HCOO)3] 1"
    structured = (
        tmp_path
        / "abc12345"
        / "cbu_derivation"
        / "metal"
        / "structured"
    )
    structured.mkdir(parents=True)
    (structured / "Synthesis_of_V3_mu3-O_O_OH_2_BPD_1_5_HCOO_3_1.json").write_text(
        json.dumps({"metal_cbu": "[V3O2(OH)2(HCO2)3]"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_mop_formula, "DATA_DIR", str(tmp_path))

    assert (
        agent_mop_formula._read_metal_cbu_formula("abc12345", label)
        == "[V3O2(OH)2(HCO2)3]"
    )
