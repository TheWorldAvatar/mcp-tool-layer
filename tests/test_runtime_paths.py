import os
from pathlib import Path

from src.pipelines.extensions_extractions.extract import _safe_name as extraction_safe_name
from src.pipelines.extensions_kg_building.build import _safe_name as kg_safe_name
from src.pipelines.utils.runtime_paths import (
    extension_filename_stems,
    find_existing_extension_artifact,
    first_existing_runtime_path,
    measured_path_len,
    read_runtime_text,
    resolve_extension_artifact,
    windows_fs_path,
    write_runtime_text,
)
from src.pipelines.utils.top_entity_identity import (
    entity_artifact_name,
    entity_scope_name,
)

A014_LABEL = (
    "Transformation of VMOP-beta to VMOP-alpha via ultrasonic treatment "
    "and heating in DMF/CH3OH at 120degC"
)


def test_entity_artifact_name_caps_the_a014d993_label() -> None:
    stem = entity_artifact_name(A014_LABEL)

    assert stem == extraction_safe_name(A014_LABEL)
    assert stem == kg_safe_name(A014_LABEL)
    assert len(stem) <= 62
    assert "--" in stem
    assert stem.startswith("Transformation_of_VMOP-beta_to_VMOP-alpha")


def test_extension_write_path_stays_under_budget_in_deep_runtime(tmp_path: Path) -> None:
    doi_folder = (
        tmp_path
        / "scenarios"
        / "mops"
        / "runs"
        / "20260822_eval30_ext30"
        / "runtime"
        / "a014d993"
    )
    write_path, candidates = resolve_extension_artifact(
        str(doi_folder),
        "mcp_run_ontomops/extraction_{entity_safe}.txt",
        A014_LABEL,
    )

    assert measured_path_len(write_path) <= 240
    assert write_path in candidates
    assert any(
        "ultrasonic_treatment_and_heating" in Path(path).name
        for path in candidates
    )


def test_lookup_still_finds_legacy_untruncated_file(tmp_path: Path) -> None:
    doi_folder = tmp_path / "a014d993"
    write_path, candidates = resolve_extension_artifact(
        str(doi_folder),
        "mcp_run_ontomops/extraction_{entity_safe}.txt",
        A014_LABEL,
    )
    legacy = next(
        path
        for path in candidates
        if "ultrasonic_treatment_and_heating" in Path(path).name
    )
    Path(legacy).parent.mkdir(parents=True, exist_ok=True)
    Path(legacy).write_text("legacy extraction", encoding="utf-8")

    found = first_existing_runtime_path(candidates)
    assert found == legacy
    assert found != write_path
    assert read_runtime_text(found) == "legacy extraction"


def test_windows_long_path_write_survives_max_path(tmp_path: Path) -> None:
    deep = tmp_path
    while measured_path_len(deep / "x") < 200:
        deep = deep / "nested_runtime_directory"
    filename = "extraction_" + ("very_long_entity_name_" * 8) + ".txt"
    target = deep / filename
    if os.name == "nt":
        assert measured_path_len(target) > 260

    write_runtime_text(str(target), "kept")
    assert read_runtime_text(str(target)) == "kept"
    if os.name == "nt":
        assert windows_fs_path(str(target)).startswith("\\\\?\\")


def test_cbu_safe_name_matches_extension_cap() -> None:
    from src.agents.mops.cbu_derivation.utils.metal_cbu import safe_name as cbu_safe_name

    assert cbu_safe_name(A014_LABEL) == entity_artifact_name(A014_LABEL)


def test_cbu_loader_finds_legacy_untruncated_extraction(tmp_path: Path, monkeypatch) -> None:
    from src.agents.mops.cbu_derivation.utils import metal_cbu
    from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
        safe_filename_component,
    )

    monkeypatch.setattr(metal_cbu, "DATA_DIR", str(tmp_path))
    run_dir = tmp_path / "deadbeef" / "mcp_run_ontomops"
    run_dir.mkdir(parents=True)
    legacy = run_dir / f"extraction_{safe_filename_component(A014_LABEL)}.txt"
    write_runtime_text(str(legacy), "legacy extraction")

    loaded = metal_cbu.load_entity_extraction_content("deadbeef", A014_LABEL)
    assert loaded == "legacy extraction"


def test_cbu_loader_finds_capped_extraction(tmp_path: Path, monkeypatch) -> None:
    from src.agents.mops.cbu_derivation.utils import metal_cbu

    monkeypatch.setattr(metal_cbu, "DATA_DIR", str(tmp_path))
    run_dir = tmp_path / "deadbeef" / "mcp_run_ontomops"
    run_dir.mkdir(parents=True)
    capped = run_dir / f"extraction_{entity_artifact_name(A014_LABEL)}.txt"
    write_runtime_text(str(capped), "capped extraction")

    loaded = metal_cbu.load_entity_extraction_content("deadbeef", A014_LABEL)
    assert loaded == "capped extraction"


def test_organic_instruction_write_survives_deep_runtime(tmp_path: Path) -> None:
    from src.agents.mops.cbu_derivation.utils.markdown_utils import (
        safe_name,
        write_instruction_md,
    )

    deep = tmp_path
    while measured_path_len(deep / "x") < 180:
        deep = deep / "nested_runtime_directory"
    written = write_instruction_md(str(deep / "instructions"), A014_LABEL, "prompt body")

    assert safe_name(A014_LABEL) == entity_artifact_name(A014_LABEL)
    assert measured_path_len(written) <= 240
    assert "prompt body" in read_runtime_text(str(written))


def test_extension_stems_include_main_kg_scope_when_uri_is_known() -> None:
    uri = "https://example.test/ChemicalSynthesis/one"
    stems = extension_filename_stems(A014_LABEL, entity_uri=uri)

    assert stems[0] == entity_scope_name(A014_LABEL, uri)
    assert entity_artifact_name(A014_LABEL) in stems


def test_find_existing_recovers_copied_bounded_digest(tmp_path: Path) -> None:
    import hashlib

    doi_folder = tmp_path / "a527729b"
    folder = doi_folder / "mcp_run_ontomops"
    folder.mkdir(parents=True)
    artifact = entity_artifact_name(A014_LABEL)
    digest = hashlib.sha256(f"extraction_{artifact}".encode("utf-8")).hexdigest()[:12]
    copied = folder / f"extraction_Transformation_of_VMOP-beta_to_--{digest}.txt"
    copied.write_text("copied official extraction", encoding="utf-8")

    found = find_existing_extension_artifact(
        str(doi_folder),
        "mcp_run_ontomops/extraction_{entity_safe}.txt",
        A014_LABEL,
        entity_uri="https://example.test/ChemicalSynthesis/one",
    )

    assert found is not None
    assert Path(found).name == copied.name
    assert read_runtime_text(found) == "copied official extraction"


def test_find_existing_distinguishes_two_bounded_siblings(tmp_path: Path) -> None:
    import hashlib

    doi_folder = tmp_path / "50307a45"
    folder = doi_folder / "mcp_run_ontomops"
    folder.mkdir(parents=True)
    labels = (
        "Zr-bpydc-CuCl2 (tetrahedral coordination cage, post-synthetic metallization)",
        "Zr-bpydc-CuCl2 (tetrahedral coordination cage, metalloligand approach)",
    )
    files = []
    for label in labels:
        artifact = entity_artifact_name(label)
        digest = hashlib.sha256(f"extraction_{artifact}".encode("utf-8")).hexdigest()[:12]
        path = folder / f"extraction_Zr-bpydc-CuCl2_tetrahedral_coordination_cage_--{digest}.txt"
        path.write_text(label, encoding="utf-8")
        files.append(path)

    found = [
        find_existing_extension_artifact(
            str(doi_folder),
            "mcp_run_ontomops/extraction_{entity_safe}.txt",
            label,
        )
        for label in labels
    ]
    assert [Path(path).name for path in found] == [path.name for path in files]
    assert found[0] != found[1]
