from pathlib import Path

import src.pipelines.extensions_kg_building.build as build
from src.pipelines.utils.kg_full_hints_onepass import (
    build_mcp_semantic_surface_task_prompt,
)
from src.pipelines.extensions_kg_building.build import (
    _fill_extension_prompt_template,
    _load_scoped_extension_ttl,
)
from src.pipelines.utils.runtime_paths import write_runtime_text
from src.pipelines.utils.top_entity_identity import (
    entity_artifact_name,
    entity_scope_name,
)

B284_LABEL = (
    "Synthesis of cubic polyoxometalate-organic molecular cage "
    "[Ni(en)2(H2O)2]6{Ni6(Tris)(en)3(BTC)1.5(B-R-PW9O34)}8-12en-54H2O (1)"
)
B284_STEM = (
    "Synthesis_of_cubic_polyoxometalate-organic_molecular_cage_"
    "Ni_en_2_H2O_2_6_Ni6_Tris_en_3_BTC_1.5_B-R-PW9O34_8-12en-54H2O_1"
)


def test_removed_filename_and_ontology_repairs() -> None:
    assert not hasattr(build, "_discover_persisted_extension_ttl")
    assert not hasattr(build, "_ttl_name_affinity")
    assert not hasattr(build, "_extension_ttl_quality")
    assert not hasattr(build, "_repair_ontospecies_scoped_anchor")
    assert not hasattr(build, "_repair_ontomops_missing_ccdc")
    assert not hasattr(build, "_entity_name_variants")
    assert not hasattr(build, "_looks_like_valid_extension_ttl")
    assert not hasattr(build, "_read_valid_extension_ttl")


def test_scoped_persist_accepts_nonempty_ttl_without_class_markers(
    tmp_path: Path,
) -> None:
    doi = tmp_path / "probe"
    memory = doi / "memory_ontomops"
    memory.mkdir(parents=True)
    content = "@prefix ex: <https://example.test/> .\n<urn:node> ex:note \"seed\" .\n"
    write_runtime_text(str(memory / "Cage-1.ttl"), content)

    found = _load_scoped_extension_ttl(str(doi), "ontomops", "Cage-1")

    assert found is not None
    assert Path(found[0]).name == "Cage-1.ttl"
    assert found[1] == content


def test_scoped_persist_uses_this_entity_export(tmp_path: Path) -> None:
    doi = tmp_path / "b284c4ea"
    (doi / "memory_ontomops").mkdir(parents=True)
    (doi / "exports_ontomops").mkdir()
    mop = (
        "@prefix ns1: <https://www.theworldavatar.com/kg/ontomops/> .\n"
        "<urn:mop> a ns1:MetalOrganicPolyhedron ;\n"
        '    ns1:hasMOPFormula "C236H616N104Ni54O434P8W72" .\n'
    )
    write_runtime_text(
        str(doi / "exports_ontomops" / f"{B284_STEM}_20260822_174227.ttl"),
        mop,
    )

    found = _load_scoped_extension_ttl(str(doi), "ontomops", B284_LABEL)

    assert found is not None
    path, content = found
    assert Path(path).name == f"{B284_STEM}_20260822_174227.ttl"
    assert "MetalOrganicPolyhedron" in content


def test_scoped_persist_uses_exact_memory_stem(tmp_path: Path) -> None:
    doi = tmp_path / "f4f7330e"
    memory = doi / "memory_ontospecies"
    memory.mkdir(parents=True)
    content = (
        "@prefix ns1: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .\n"
        "<urn:species> a ns1:Species ;\n"
        "    ns1:hasMolecularFormula <urn:mf> ;\n"
        '    ns1:hasCCDCNumberValue "1528352" .\n'
    )
    write_runtime_text(str(memory / "HCCF-1.ttl"), content)

    found = _load_scoped_extension_ttl(str(doi), "ontospecies", "HCCF-1")

    assert found is not None
    assert Path(found[0]).name == "HCCF-1.ttl"
    assert "Species" in found[1]


def test_scoped_persist_never_takes_sibling_file(tmp_path: Path) -> None:
    doi = tmp_path / "3a4646d4"
    memory = doi / "memory_ontospecies"
    memory.mkdir(parents=True)
    sibling = (
        "@prefix ns1: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .\n"
        "<urn:zrt1> a ns1:Species ;\n"
        '    ns1:hasCCDCNumberValue "950330" .\n'
    )
    write_runtime_text(str(memory / "Synthesis_of_ZrT-1.ttl"), sibling)

    found = _load_scoped_extension_ttl(
        str(doi), "ontospecies", "Synthesis of ZrT-2"
    )

    assert found is None


def test_load_entity_ttl_never_takes_sibling(tmp_path: Path) -> None:
    doi_hash = "paper"
    current = ("CS-2", "https://example.test/ChemicalSynthesis/two")
    sibling = ("CS-1", "https://example.test/ChemicalSynthesis/one")
    output_dir = tmp_path / doi_hash / "ontosynthesis_output"
    output_dir.mkdir(parents=True)
    (output_dir / f"{entity_scope_name(*sibling)}.ttl").write_text(
        "@prefix ex: <https://example.test/sibling> .\n",
        encoding="utf-8",
    )

    loaded = build.load_entity_ttl(
        doi_hash=doi_hash,
        entity_safe=current[0],
        entity_uri=current[1],
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

    assert loaded == ""


def test_long_label_scope_and_artifact_stems_differ() -> None:
    label = (
        "Synthesis of [Co24(C-pentylpyrogallol[4]arene)6] nanocapsule (1) by "
        "reacting C-pentylpyrogallol[4]arene (PgC5), CoCl2-6H2O, and NaOMe in "
        "1:1 (v/v) DMF/methanol mixture, yielding dark blue crystals over "
        "several weeks under slow evaporation"
    )
    uri = (
        "https://www.theworldavatar.com/kg/instance/ChemicalSynthesis/"
        "24f5b3586b45b0b29c9a922e742bf5dc755dded9"
    )
    scope = entity_scope_name(label, uri)
    artifact = entity_artifact_name(label)
    assert scope == "Synthesis_of_Co24_C-pentylpyroga--8816a66c81dd"
    assert artifact == "Synthesis_of_Co24_C-pentylpyrogallol_4_arene_6_n--89d1bab6081f"
    assert scope != artifact


def test_scoped_persist_finds_main_kg_scope_memory(tmp_path: Path) -> None:
    label = (
        "Synthesis of [Co24(C-pentylpyrogallol[4]arene)6] nanocapsule (1) by "
        "reacting C-pentylpyrogallol[4]arene (PgC5), CoCl2-6H2O, and NaOMe in "
        "1:1 (v/v) DMF/methanol mixture, yielding dark blue crystals over "
        "several weeks under slow evaporation"
    )
    uri = (
        "https://www.theworldavatar.com/kg/instance/ChemicalSynthesis/"
        "24f5b3586b45b0b29c9a922e742bf5dc755dded9"
    )
    doi = tmp_path / "a527729b"
    memory = doi / "memory_ontomops"
    memory.mkdir(parents=True)
    stem = entity_scope_name(label, uri)
    content = (
        "@prefix ns1: <https://www.theworldavatar.com/kg/ontomops/> .\n"
        "<urn:mop> a ns1:MetalOrganicPolyhedron ;\n"
        '    ns1:hasMOPFormula "[Co24(H2O)x(DMF)y(C48H56O12)6]" .\n'
    )
    write_runtime_text(str(memory / f"{stem}.ttl"), content)

    found = _load_scoped_extension_ttl(
        str(doi), "ontomops", label, entity_uri=uri
    )

    assert found is not None
    assert Path(found[0]).name == f"{stem}.ttl"
    assert "MetalOrganicPolyhedron" in found[1]
    assert entity_artifact_name(label) not in Path(found[0]).name


def test_scoped_persist_scope_memory_does_not_take_other_uri(tmp_path: Path) -> None:
    label = "Preparation of I"
    current = "https://example.test/ChemicalSynthesis/one"
    other = "https://example.test/ChemicalSynthesis/two"
    doi = tmp_path / "paper"
    memory = doi / "memory_ontomops"
    memory.mkdir(parents=True)
    write_runtime_text(
        str(memory / f"{entity_scope_name(label, other)}.ttl"),
        "@prefix ns1: <https://www.theworldavatar.com/kg/ontomops/> .\n"
        "<urn:mop> a ns1:MetalOrganicPolyhedron .\n",
    )

    found = _load_scoped_extension_ttl(
        str(doi), "ontomops", label, entity_uri=current
    )

    assert found is None


def test_fill_extension_prompt_keeps_chemistry_braces() -> None:
    template = (
        "DOI {doi} entity {entity_label}\n"
        "formula {[Cp3Zr3µ3-O(µ2-OH)3]4(BDC)6}\n"
        "targets {enrichment_targets}"
    )
    filled = _fill_extension_prompt_template(
        template,
        {
            "doi": "a527729b",
            "entity_label": "cage",
            "enrichment_targets": "[]",
        },
    )
    assert "DOI a527729b entity cage" in filled
    assert "{[Cp3Zr3µ3-O(µ2-OH)3]4(BDC)6}" in filled
    assert "targets []" in filled


def test_extension_kg_task_prompt_is_semantic_surface_not_generated_contract() -> None:
    prompt = build_mcp_semantic_surface_task_prompt()
    filled = _fill_extension_prompt_template(
        prompt,
        {
            "doi": "a527729b",
            "entity_label": "cage",
            "entity_uri": "urn:entity:1",
            "iteration_hints": "ExtractedHints",
        },
    )
    assert "DOI: a527729b" in filled
    assert "Bound root IRI: urn:entity:1" in filled
    assert "ExtractedHints" in filled
    assert "hasChemicalBuildingUnit" not in filled
    assert "ONEPASS" not in filled
