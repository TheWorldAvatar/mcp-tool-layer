import json
from pathlib import Path
from types import SimpleNamespace

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_generation_contract_bundle,
    build_ontology_publish_contract,
)
from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    _build_required_top_link_export_repair_block,
)
from src.pipelines.main_kg_building.build import (
    _augment_kg_prompt_with_runtime_rules,
    _get_hint_reconciliation_specs,
    _prune_unhinted_orphan_required_targets,
    _repair_published_entity_ttl,
    _validate_entity_ttl_structure,
)
from src.pipelines.utils.ttl_publisher import (
    _reusable_class_iris,
    enforce_published_graph_hygiene,
    publish_ttl,
)
from src.pipelines.utils import ttl_publisher


NS = "https://example.test/ontology/"
ENTITY = "https://example.test/entity/root"


def test_publish_ttl_can_bypass_all_semantic_composition(
    tmp_path: Path, monkeypatch
) -> None:
    doi = "paper"
    source = tmp_path / doi / "intermediate.ttl"
    top = tmp_path / doi / "memory" / "top.ttl"
    source.parent.mkdir(parents=True)
    top.parent.mkdir(parents=True)
    source.write_text(
        f"@prefix ex: <{NS}> .\n<{ENTITY}> a ex:Root ; ex:marker \"entity\" .\n",
        encoding="utf-8",
    )
    top.write_text(
        f"@prefix ex: <{NS}> .\n<{ENTITY}> ex:topMarker \"top\" .\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ttl_publisher,
        "get_output_naming_config",
        lambda **_: SimpleNamespace(
            output_dir="output",
            entity_ttl_pattern="{entity_safe}.ttl",
        ),
    )
    monkeypatch.setattr(
        ttl_publisher,
        "_get_main_entity_kg_policy",
        lambda _: {"publish": {"merge_top_ttl_into_entity_ttl": True}},
    )

    published = publish_ttl(
        doi_hash=doi,
        ontology_name="fixture",
        entity_safe="entity",
        data_dir=str(tmp_path),
        src_candidates=[str(source)],
        apply_semantic_processing=False,
    )

    assert published is not None
    graph = Graph().parse(published, format="turtle")
    assert (URIRef(ENTITY), URIRef(f"{NS}marker"), Literal("entity")) in graph
    assert (URIRef(ENTITY), URIRef(f"{NS}topMarker"), Literal("top")) not in graph


def test_publish_hygiene_deduplicates_only_policy_reusable_classes() -> None:
    root = URIRef(f"{NS}root")
    occurrence_class = URIRef(f"{NS}Occurrence")
    has_occurrence = URIRef(f"{NS}hasOccurrence")
    first = URIRef(f"{NS}first")
    second = URIRef(f"{NS}second")
    graph = Graph()
    graph.add((root, RDF.type, URIRef(f"{NS}Root")))
    for node in (first, second):
        graph.add((root, has_occurrence, node))
        graph.add((node, RDF.type, occurrence_class))
        graph.add((node, RDFS.label, Literal("same label")))

    non_reusable = {
        "reuse_policy": {
            "classes": [
                {"class_iri": str(occurrence_class), "reusable": False}
            ]
        }
    }
    preserved = enforce_published_graph_hygiene(
        graph,
        top_entity=root,
        messages=[],
        reusable_class_iris=_reusable_class_iris(non_reusable),
    )
    assert set(preserved.objects(root, has_occurrence)) == {first, second}

    reusable = {
        "reuse_policy": {
            "classes": [
                {"class_iri": str(occurrence_class), "reusable": True}
            ]
        }
    }
    merged = enforce_published_graph_hygiene(
        graph,
        top_entity=root,
        messages=[],
        reusable_class_iris=_reusable_class_iris(reusable),
    )
    assert len(set(merged.objects(root, has_occurrence))) == 1


def _write_fixture(
    tmp_path: Path, *, tbox_text: str, shell_validation: dict | None = None
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tbox = tmp_path / "ontology.ttl"
    tbox.write_text(tbox_text, encoding="utf-8")
    config = tmp_path / "meta.json"
    config.write_text(
        json.dumps(
            {
                "ontologies": {
                    "main": {
                        "name": "fixture",
                        "ttl_file": str(tbox),
                        "runtime_policies": {
                            "main_entity_kg": {
                                "shell_validation": shell_validation or {}
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return config, tbox


def _tbox() -> str:
    return f"""
@prefix ex: <{NS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Root a owl:Class .
ex:Step a owl:Class .
ex:Add a owl:Class ; rdfs:subClassOf ex:Step .
ex:Other a owl:Class .
ex:hasStep a owl:ObjectProperty ; rdfs:domain ex:Root ; rdfs:range ex:Step .
"""


def _write_entity(tmp_path: Path, object_type: str, *, include_edge: bool = True) -> Path:
    edge = "ex:hasStep ex:child ;" if include_edge else ""
    entity = tmp_path / "entity.ttl"
    entity.write_text(
        f"""
@prefix ex: <{NS}> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
<{ENTITY}> a ex:Root ; {edge} ex:marker "present" .
ex:child a <{object_type}> .
""",
        encoding="utf-8",
    )
    return entity


def test_add_subclass_object_satisfies_synthesis_step_range(tmp_path: Path) -> None:
    config, _ = _write_fixture(tmp_path, tbox_text=_tbox())
    contract = build_ontology_publish_contract(meta_task_config_path=config)
    entity = _write_entity(tmp_path, f"{NS}Add")

    ok, messages = _validate_entity_ttl_structure(
        ttl_path=str(entity),
        entity_uri=ENTITY,
        entity_label="root",
        ontology_contract=contract,
    )

    assert ok, messages
    assert contract["required_links"] == []


def test_missing_edge_is_allowed_without_machine_cardinality(tmp_path: Path) -> None:
    config, _ = _write_fixture(
        tmp_path,
        tbox_text=_tbox(),
        shell_validation={
            "required_links": [
                {
                    "predicate_iri": f"{NS}hasStep",
                    "target_class_iri": f"{NS}Step",
                    "min_count": 1,
                }
            ]
        },
    )
    contract = build_ontology_publish_contract(meta_task_config_path=config)
    entity = _write_entity(tmp_path, f"{NS}Add", include_edge=False)

    ok, messages = _validate_entity_ttl_structure(
        ttl_path=str(entity),
        entity_uri=ENTITY,
        entity_label="root",
        ontology_contract=contract,
    )

    assert ok, messages
    assert contract["required_links"] == []


def test_wrong_object_range_fails(tmp_path: Path) -> None:
    config, _ = _write_fixture(tmp_path, tbox_text=_tbox())
    contract = build_ontology_publish_contract(meta_task_config_path=config)
    entity = _write_entity(tmp_path, f"{NS}Other")

    ok, messages = _validate_entity_ttl_structure(
        ttl_path=str(entity),
        entity_uri=ENTITY,
        entity_label="root",
        ontology_contract=contract,
    )

    assert not ok
    assert any("range mismatch" in message for message in messages)


def test_required_links_defer_known_external_top_roots_to_own_fragments(
    tmp_path: Path,
) -> None:
    external = f"{NS}external-root"
    source = f"{NS}source"
    entity = tmp_path / "entity.ttl"
    entity.write_text(
        (
            f"@prefix ex: <{NS}> .\n"
            f"<{ENTITY}> a ex:Root ; ex:retrievedFrom <{source}> .\n"
            f"<{external}> a ex:Root .\n"
            f"<{source}> a ex:Document .\n"
        ),
        encoding="utf-8",
    )
    contract = {
        "resolved_ttl_file": "active.ttl",
        "subclass_closure": [],
        "object_properties": [],
        "required_links": [
            {
                "subject_class_iri": f"{NS}Root",
                "predicate_iri": f"{NS}retrievedFrom",
                "target_class_iri": f"{NS}Document",
                "min_count": 1,
            }
        ],
    }

    ok, messages = _validate_entity_ttl_structure(
        ttl_path=str(entity),
        entity_uri=ENTITY,
        entity_label="root",
        ontology_contract=contract,
        known_top_entity_uris={ENTITY, external},
    )
    assert ok, messages

    local_ok, local_messages = _validate_entity_ttl_structure(
        ttl_path=str(entity),
        entity_uri=ENTITY,
        entity_label="root",
        ontology_contract=contract,
        known_top_entity_uris={ENTITY},
    )
    assert not local_ok
    assert any(external in message for message in local_messages)


def test_legacy_shell_validation_mutation_does_not_change_contract(
    tmp_path: Path,
) -> None:
    config_a, _ = _write_fixture(
        tmp_path / "a",
        tbox_text=_tbox(),
        shell_validation={"required_links": [{"predicate_iri": "urn:wrong"}]},
    )
    config_b, _ = _write_fixture(
        tmp_path / "b",
        tbox_text=_tbox(),
        shell_validation={"top_entity_class_iri": "urn:also-wrong"},
    )

    contract_a = build_ontology_publish_contract(meta_task_config_path=config_a)
    contract_b = build_ontology_publish_contract(meta_task_config_path=config_b)

    for contract in (contract_a, contract_b):
        contract.pop("resolved_ttl_file")
        for collection in (
            "classes",
            "subclass_closure",
            "object_properties",
            "constraints",
            "required_links",
        ):
            for item in contract[collection]:
                item.get("evidence", {}).pop("ttl_file", None)
    for key in (
        "ontology_name",
        "classes",
        "subclass_closure",
        "object_properties",
        "constraints",
        "required_links",
    ):
        assert contract_a[key] == contract_b[key]


def test_generation_bundle_ignores_legacy_semantic_fields(tmp_path: Path) -> None:
    config_a, _ = _write_fixture(
        tmp_path / "a",
        tbox_text=_tbox(),
        shell_validation={
            "top_entity_class_iri": f"{NS}Root",
            "required_links": [{"predicate_iri": f"{NS}hasStep", "min_count": 9}],
        },
    )
    config_b, _ = _write_fixture(
        tmp_path / "b",
        tbox_text=_tbox(),
        shell_validation={
            "top_entity_class_iri": "urn:tampered:Top",
            "required_links": [{"predicate_iri": "urn:tampered:link", "min_count": 1}],
        },
    )

    bundle_a = build_generation_contract_bundle(meta_task_config_path=config_a)
    bundle_b = build_generation_contract_bundle(meta_task_config_path=config_b)

    assert bundle_a["required_links"] == bundle_b["required_links"] == []
    assert bundle_a["top_entity"]["status"] == "unknown"
    assert bundle_b["top_entity"]["status"] == "unknown"
    assert bundle_a["top_entity"]["class_iri"] == bundle_b["top_entity"]["class_iri"] == ""


def test_direct_repair_block_is_empty_without_tbox_cardinality(tmp_path: Path) -> None:
    config, tbox = _write_fixture(
        tmp_path,
        tbox_text=_tbox(),
        shell_validation={
            "top_entity_class_iri": f"{NS}Root",
            "required_links": [
                {
                    "predicate_iri": f"{NS}hasStep",
                    "target_class_iri": f"{NS}Step",
                    "min_count": 1,
                }
            ],
        },
    )
    meta_cfg = json.loads(config.read_text(encoding="utf-8"))

    assert (
        _build_required_top_link_export_repair_block(
            ontology_name="fixture",
            ontology_path=tbox,
            meta_cfg=meta_cfg,
        )
        == ""
    )


def test_missing_tbox_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "meta.json"
    config.write_text(
        json.dumps(
            {
                "ontologies": {
                    "main": {
                        "name": "fixture",
                        "ttl_file": str(tmp_path / "missing.ttl"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    try:
        build_ontology_publish_contract(meta_task_config_path=config)
    except FileNotFoundError as exc:
        assert "T-Box not found" in str(exc)
    else:
        raise AssertionError("Missing T-Box must fail closed")


def test_legacy_required_links_do_not_change_prompt_or_hint_specs() -> None:
    legacy_link = {
        "section_name": "Legacy",
        "predicate_iri": "urn:legacy:required",
        "target_class_iri": "urn:legacy:Target",
        "min_count": 1,
    }
    base_policy = {
        "prompt_rules": {
            "require_top_entity_reuse": True,
            "require_required_links_before_export": True,
        }
    }
    mutated_policy = {
        **base_policy,
        "shell_validation": {"required_links": [legacy_link]},
    }
    kwargs = {
        "kg_prompt": "Build.",
        "entity_label": "root",
        "entity_uri": ENTITY,
        "doi_hash": "hash",
        "hints_content": "",
        "ontology_contract": {"required_links": []},
    }

    assert _augment_kg_prompt_with_runtime_rules(
        **kwargs, main_entity_policy=base_policy
    ) == _augment_kg_prompt_with_runtime_rules(
        **kwargs, main_entity_policy=mutated_policy
    )
    assert _get_hint_reconciliation_specs(mutated_policy) == []


def test_machine_required_link_is_written_to_prompt() -> None:
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt="Build.",
        entity_label="root",
        entity_uri=ENTITY,
        doi_hash="hash",
        main_entity_policy={
            "prompt_rules": {"require_required_links_before_export": True}
        },
        ontology_contract={
            "required_links": [
                {
                    "predicate_iri": f"{NS}hasStep",
                    "min_count": 2,
                    "source": "owl_restriction",
                }
            ]
        },
    )

    assert f"{NS}hasStep" in prompt
    assert "minimum 2 link(s)" in prompt


def test_repair_ignores_legacy_required_link_and_creates_no_placeholder(
    tmp_path: Path,
) -> None:
    top_ttl = tmp_path / "iteration_1.ttl"
    entity_ttl = tmp_path / "entity.ttl"
    top_ttl.write_text(
        (
            f"@prefix ex: <{NS}> .\n"
            f"<{ENTITY}> a ex:Root ; ex:marker \"top\" .\n"
        ),
        encoding="utf-8",
    )
    entity_ttl.write_text(
        (
            f"@prefix ex: <{NS}> .\n"
            f"<{ENTITY}> a ex:Root ; ex:marker \"entity\" .\n"
        ),
        encoding="utf-8",
    )
    legacy_predicate = URIRef("urn:legacy:required")
    legacy_target_class = URIRef("urn:legacy:Target")
    policy = {
        "publish": {"merge_top_ttl_into_entity_ttl": True},
        "shell_validation": {
            "top_entity_class_iri": "urn:legacy:WrongTop",
            "required_links": [
                {
                    "predicate_iri": str(legacy_predicate),
                    "target_class_iri": str(legacy_target_class),
                    "min_count": 1,
                    "placeholder_target_if_missing": {"label": "legacy placeholder"},
                }
            ],
        },
    }

    ok, messages = _repair_published_entity_ttl(
        ttl_path=str(entity_ttl),
        doi_folder=str(tmp_path),
        ontology_name="fixture",
        entity_uri=ENTITY,
        entity_label="root",
        meta_cfg={},
        main_entity_policy=policy,
        ontology_contract={"required_links": []},
    )

    assert ok, messages
    repaired = Graph()
    repaired.parse(str(entity_ttl), format="turtle")
    assert not list(repaired.triples((URIRef(ENTITY), legacy_predicate, None)))
    assert not list(repaired.subjects(RDF.type, legacy_target_class))


def test_prune_is_noop_without_machine_required_links(tmp_path: Path) -> None:
    entity_ttl = tmp_path / "entity.ttl"
    orphan = URIRef(f"{NS}orphan")
    entity_ttl.write_text(
        (
            f"@prefix ex: <{NS}> .\n"
            "ex:orphan a ex:Step ; ex:marker \"keep\" .\n"
        ),
        encoding="utf-8",
    )

    ok, messages = _prune_unhinted_orphan_required_targets(
        ttl_path=str(entity_ttl),
        raw_hints=[],
        main_entity_policy={
            "shell_validation": {
                "required_links": [
                    {
                        "predicate_iri": f"{NS}hasStep",
                        "target_class_iri": f"{NS}Step",
                    }
                ]
            }
        },
        ontology_contract={"required_links": []},
    )

    assert ok
    assert messages == []
    graph = Graph()
    graph.parse(str(entity_ttl), format="turtle")
    assert (orphan, RDF.type, URIRef(f"{NS}Step")) in graph
