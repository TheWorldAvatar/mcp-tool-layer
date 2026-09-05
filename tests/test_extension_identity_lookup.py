from pathlib import Path

from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    safe_filename_component,
)
from src.agents.mops.cbu_derivation.utils.metal_cbu import safe_name as cbu_safe_name
from src.pipelines.extensions_extractions.extract import _safe_name as extraction_safe_name
from src.pipelines.extensions_kg_building.build import (
    _safe_name as kg_building_safe_name,
    load_entity_ttl,
)
from src.pipelines.utils.top_entity_identity import entity_scope_name


def test_extension_loads_exact_identity_main_ttl(tmp_path: Path) -> None:
    doi_hash = "paper"
    label = "CS-1"
    uri = "https://example.test/ChemicalSynthesis/one"
    scope = entity_scope_name(label, uri)
    output_dir = tmp_path / doi_hash / "ontosynthesis_output"
    output_dir.mkdir(parents=True)
    expected = "@prefix ex: <https://example.test/> .\n"
    (output_dir / f"{scope}.ttl").write_text(expected, encoding="utf-8")

    loaded = load_entity_ttl(
        doi_hash=doi_hash,
        entity_safe=label,
        entity_uri=uri,
        data_dir=str(tmp_path),
        test_mode=True,
        ontology_name="ontosynthesis",
        meta_cfg={
            "ontologies": {
                "main": {
                    "name": "ontosynthesis",
                    "output": {
                        "dir": "ontosynthesis_output",
                        "entity_ttl_pattern": "{entity_safe}.ttl",
                    },
                },
                "extensions": [],
            }
        },
    )

    assert loaded == expected


def test_extension_memory_filename_uses_fixed_runtime_policy() -> None:
    label = "Synthesis of Zr-bpydc-CuCl2 (one-pot method)"

    expected = "Synthesis_of_Zr-bpydc-CuCl2_one-pot_method"
    assert safe_filename_component(label) == expected
    assert extraction_safe_name(label) == expected
    assert kg_building_safe_name(label) == expected
    assert cbu_safe_name(label) == expected
