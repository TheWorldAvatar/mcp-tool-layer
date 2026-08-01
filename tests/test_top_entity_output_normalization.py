import json
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.pipelines.top_entity_extraction.extract import (
    bind_paper_content,
    _normalize_top_entity_output,
    _persist_and_validate_top_class_selection,
    _resolve_selected_top_class_iri,
)
from src.pipelines.top_entity_kg_building.build import (
    _materialize_supplemented_top_entities,
    _merge_txt_top_entity_fallback,
    _mint_top_entity_iri,
    parse_top_entities_from_ttl,
)
from src.pipelines.utils.top_entity_identity import (
    entity_scope_name,
    hydrate_and_validate_top_entity_types,
    persist_entity_identity_sidecars,
)


def test_pipeline_binds_source_when_top_entity_prompt_omits_slot() -> None:
    rendered = bind_paper_content(
        "Extract the top-level entity under the T-Box rules.",
        "Source procedure text.",
    )

    assert "Extract the top-level entity" in rendered
    assert "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----" in rendered
    assert "Source procedure text." in rendered


def test_pipeline_replaces_declared_top_entity_source_slot() -> None:
    rendered = bind_paper_content(
        "Source:\n{paper_content}",
        "Source procedure text.",
    )

    assert "{paper_content}" not in rendered
    assert "Source:\nSource procedure text." in rendered
    assert "PIPELINE-INJECTED" not in rendered


def test_normalize_top_entity_output_accepts_structured_json() -> None:
    output = _normalize_top_entity_output(
        '[{"class":"ChemicalSynthesis","label":"UMC-1 synthesis"}]',
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [UMC-1]\n"


def test_normalize_top_entity_output_accepts_canonical_class_sections() -> None:
    output = _normalize_top_entity_output(
        '{"ChemicalSynthesis":[{"entity_label":"route alpha","evidence":[]}]}',
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [route alpha]\n"


def test_normalize_top_entity_output_accepts_fenced_json() -> None:
    output = _normalize_top_entity_output(
        '```json\n{"type":"ontosyn:ChemicalSynthesis","name":"UMC-1"}\n```',
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [UMC-1]\n"


def test_top_entity_merge_enforces_unique_label_and_uri(tmp_path) -> None:
    (tmp_path / "top_entities.txt").write_text(
        "ChemicalSynthesis-1 [Preferred label]\n", encoding="utf-8"
    )
    top_class = "https://example.test/ChemicalSynthesis"
    shared_uri = _mint_top_entity_iri("Preferred label", top_class)

    merged = _merge_txt_top_entity_fallback(
        str(tmp_path),
        [
            {"uri": shared_uri, "label": "runtime label", "types": []},
            {"uri": "https://example.test/top/2", "label": "Preferred label", "types": []},
        ],
        top_class,
    )

    assert len(merged) == 1
    assert merged[0]["label"] == "Preferred label"


def test_top_entity_merge_rejects_blank_node_identifiers(tmp_path) -> None:
    merged = _merge_txt_top_entity_fallback(
        str(tmp_path),
        [
            {
                "uri": "n26ecf16c948145bc9bba819573fba278b1",
                "label": "n26ecf16c948145bc9bba819573fba278b1",
                "types": [],
            }
        ],
        "https://example.test/TopEntity",
    )

    assert merged == []


def test_top_entity_materialization_merges_same_class_and_label() -> None:
    graph = Graph()
    top_class = URIRef("https://example.test/ChemicalSynthesis")
    runtime_node = URIRef("urn:uuid:runtime")
    canonical_node = URIRef("https://example.test/top/canonical")
    child = URIRef("https://example.test/child")
    predicate = URIRef("https://example.test/hasChild")
    label = "Primary synthesis"
    graph.add((runtime_node, RDF.type, top_class))
    graph.add((runtime_node, RDFS.label, Literal(label)))
    graph.add((runtime_node, predicate, child))

    changed = _materialize_supplemented_top_entities(
        graph,
        [{"uri": str(canonical_node), "label": label}],
        str(top_class),
    )

    assert changed
    assert (canonical_node, RDF.type, top_class) in graph
    assert (canonical_node, predicate, child) in graph
    assert not list(graph.triples((runtime_node, None, None)))


def test_pipeline_selected_top_class_resolves_without_tbox_top_role(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "src.pipelines.top_entity_extraction.extract.build_ontology_publish_contract",
        lambda **_: {
            "top_role": {"status": "unknown"},
            "classes": [
                {"class_iri": "https://example.test/schema/NeutralRoot"},
                {"class_iri": "https://example.test/schema/Other"},
            ],
        },
    )

    resolved = _resolve_selected_top_class_iri(
        str(tmp_path / "meta.json"),
        "neutral",
        "NeutralRoot",
    )

    assert resolved == "https://example.test/schema/NeutralRoot"


def test_top_class_selection_is_successful_extraction_postcondition(tmp_path) -> None:
    assert _persist_and_validate_top_class_selection(
        doi_dir=str(tmp_path),
        class_local="NeutralRoot",
        class_iri="https://example.test/schema/NeutralRoot",
    )

    persisted = (tmp_path / "top_entity_selection.json").read_text(encoding="utf-8")
    assert '"class_local": "NeutralRoot"' in persisted
    assert '"class_iri": "https://example.test/schema/NeutralRoot"' in persisted


def test_top_class_selection_rejects_incomplete_lineage(tmp_path) -> None:
    assert not _persist_and_validate_top_class_selection(
        doi_dir=str(tmp_path),
        class_local="NeutralRoot",
        class_iri="",
    )
    assert not (tmp_path / "top_entity_selection.json").exists()


def test_parse_top_entities_preserves_rdf_types_and_writes_sidecar(
    tmp_path, monkeypatch
) -> None:
    doi_hash = "paper"
    doi_folder = tmp_path / doi_hash
    doi_folder.mkdir()
    top_class = "https://example.test/schema/NeutralRoot"
    ancestor_class = "https://example.test/schema/NamedThing"
    entity_uri = "https://example.test/entity/root"
    (doi_folder / "top_entity_selection.json").write_text(
        json.dumps({"class_iri": top_class, "class_local": "NeutralRoot"}),
        encoding="utf-8",
    )
    (doi_folder / "iteration_1.ttl").write_text(
        f"""
        <{entity_uri}> a <{top_class}>, <{ancestor_class}> ;
            <http://www.w3.org/2000/01/rdf-schema#label> "Root A" .
        """,
        encoding="utf-8",
    )
    query = tmp_path / "top_entity_parsing.sparql"
    query.write_text(
        f"""
        SELECT ?entity ?label WHERE {{
          ?entity a <{top_class}> ;
                  <http://www.w3.org/2000/01/rdf-schema#label> ?label .
        }}
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.pipelines.top_entity_kg_building.build.resolve_generated_file",
        lambda _: str(query),
    )
    monkeypatch.setattr(
        "src.pipelines.top_entity_kg_building.build.load_meta_config",
        lambda _: {},
    )

    assert parse_top_entities_from_ttl(
        doi_hash,
        "neutral",
        data_dir=str(tmp_path),
        meta_task_config_path=str(tmp_path / "meta.json"),
    )

    manifest = json.loads(
        (doi_folder / "mcp_run" / "iter1_top_entities.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest == [
        {
            "uri": entity_uri,
            "label": "Root A",
            "types": sorted([top_class, ancestor_class]),
        }
    ]
    scope = entity_scope_name("Root A", entity_uri)
    sidecar = json.loads(
        (doi_folder / "memory" / f"{scope}.identity.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["identity"] == {
        "uri": entity_uri,
        "label": "Root A",
        "types": sorted([top_class, ancestor_class]),
        "top_class_iri": top_class,
    }
    assert sidecar["checkpoint"]["last_completed_iteration"] == 1


def test_entity_scope_is_safe_and_uri_disambiguated() -> None:
    first = entity_scope_name("../same:name", "urn:entity:one")
    second = entity_scope_name("../same:name", "urn:entity:two")

    assert first.startswith("same_name--")
    assert second.startswith("same_name--")
    assert first != second
    assert "/" not in first and "\\" not in first and ":" not in first


def test_legacy_manifest_types_are_backfilled_only_after_ttl_validation(
    tmp_path,
) -> None:
    top_class = "https://example.test/schema/NeutralRoot"
    ancestor_class = "https://example.test/schema/NamedThing"
    entity_uri = "urn:entity:legacy"
    ttl_path = tmp_path / "iteration_1.ttl"
    ttl_path.write_text(
        f"<{entity_uri}> a <{top_class}>, <{ancestor_class}> .",
        encoding="utf-8",
    )

    hydrated = hydrate_and_validate_top_entity_types(
        entities=[{"uri": entity_uri, "label": "Legacy root"}],
        iteration_1_ttl=str(ttl_path),
        top_class_iri=top_class,
    )

    assert hydrated[0]["types"] == sorted([top_class, ancestor_class])


def test_legacy_manifest_backfill_fails_closed_for_wrong_selected_class(
    tmp_path,
) -> None:
    ttl_path = tmp_path / "iteration_1.ttl"
    ttl_path.write_text(
        "<urn:entity:legacy> a <https://example.test/schema/Other> .",
        encoding="utf-8",
    )

    try:
        hydrate_and_validate_top_entity_types(
            entities=[{"uri": "urn:entity:legacy", "label": "Legacy root"}],
            iteration_1_ttl=str(ttl_path),
            top_class_iri="https://example.test/schema/NeutralRoot",
        )
    except ValueError as exc:
        assert "is not typed as selected class" in str(exc)
    else:
        raise AssertionError("Expected fail-closed top-class validation")


def test_sidecars_do_not_collide_for_same_label_different_uri(tmp_path) -> None:
    top_class = "https://example.test/schema/NeutralRoot"
    paths = persist_entity_identity_sidecars(
        doi_hash="paper",
        doi_folder=str(tmp_path),
        entities=[
            {
                "uri": "urn:entity:one",
                "label": "Same root",
                "types": [top_class],
            },
            {
                "uri": "urn:entity:two",
                "label": "Same root",
                "types": [top_class],
            },
        ],
        top_class_iri=top_class,
    )

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert all((tmp_path / "memory" / Path(path).name).is_file() for path in paths)
