from __future__ import annotations

import json
from types import SimpleNamespace

from rdflib import Graph, Literal, RDFS, URIRef

from src.pipelines.top_entity_kg_building import build


def test_iter1_repair_accepts_public_runtime_envelope(
    monkeypatch, tmp_path
) -> None:
    doi_hash = "abc123"
    doi_dir = tmp_path / doi_hash
    doi_dir.mkdir()
    (doi_dir / "top_entities.txt").write_text(
        "ChemicalSynthesis-1 [UMC-1]\n", encoding="utf-8"
    )
    (doi_dir / "top_entity_selection.json").write_text(
        json.dumps(
            {
                "schema_version": "top-entity-selection.v1",
                "class_local": "ChemicalSynthesis",
                "class_iri": "https://example.com/ChemicalSynthesis",
                "source": "pipeline_runtime_policy",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    graph = Graph()

    def create_top(label: str) -> str:
        iri = URIRef("https://example.com/s")
        graph.add(
            (
                iri,
                build.RDF.type,
                URIRef("https://example.com/ChemicalSynthesis"),
            )
        )
        graph.add((iri, RDFS.label, Literal(label)))
        calls.append({"label": label, "top_iri": str(iri)})
        return json.dumps({"status": "ok", "iri": str(iri)})

    def export_memory() -> str:
        ttl = graph.serialize(format="turtle")
        memory = doi_dir / "memory" / "top.ttl"
        memory.parent.mkdir(exist_ok=True)
        memory.write_text(ttl, encoding="utf-8")
        return ttl

    main = SimpleNamespace(
        init_memory=lambda: calls.append("init"),
        export_memory=export_memory,
    )
    modules = {
        "main": main,
        "base": SimpleNamespace(),
        "entities": SimpleNamespace(create_ChemicalSynthesis=create_top),
        "relationships": SimpleNamespace(),
    }
    monkeypatch.setattr(build, "_load_generated_iter1_modules", lambda **_: modules)
    monkeypatch.setattr(build, "publish_top_ttl", lambda **_: {"ok": True})
    monkeypatch.setattr(build, "project_root", str(tmp_path))
    tbox = tmp_path / "ontology.ttl"
    tbox.write_text(
        (
            "@prefix ex: <https://example.com/> .\n"
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
            "ex:SomeClass a owl:Class .\n"
        ),
        encoding="utf-8",
    )

    ok = build._repair_iter1_ttl_with_generated_tools(
        doi_hash=doi_hash,
        data_dir=str(tmp_path),
        ontology_name="ontosynthesis",
        meta_config={
            "ontologies": {
                "main": {
                    "name": "ontosynthesis",
                    "ttl_file": str(tbox),
                    "runtime_policies": {
                        "main_entity_kg": {
                            "shell_validation": {
                                "top_entity_class_iri": "https://example.com/ChemicalSynthesis"
                            }
                        }
                    }
                }
            }
        },
        entity_context_name="top",
    )

    assert ok
    assert calls[0] == "init"
    assert (doi_dir / "iteration_1.ttl").is_file()


def test_iter1_repair_respects_pipeline_selected_top_class(
    monkeypatch, tmp_path
) -> None:
    doi_hash = "neutral"
    doi_dir = tmp_path / doi_hash
    doi_dir.mkdir()
    (doi_dir / "top_entities.txt").write_text(
        "NeutralCandidate-1 [selected item]\n", encoding="utf-8"
    )
    (doi_dir / "top_entity_selection.json").write_text(
        json.dumps(
            {
                "schema_version": "top-entity-selection.v1",
                "class_local": "NeutralClass",
                "class_iri": "urn:NeutralClass",
                "source": "pipeline_runtime_policy",
            }
        ),
        encoding="utf-8",
    )
    def create_neutral(label):
        iri = build._mint_top_entity_iri(label, "urn:NeutralClass")
        memory = doi_dir / "memory" / "runtime-context.ttl"
        memory.parent.mkdir()
        graph = Graph()
        graph.add((URIRef(iri), build.RDF.type, URIRef("urn:NeutralClass")))
        graph.add((URIRef(iri), RDFS.label, Literal(label)))
        graph.serialize(destination=memory, format="turtle")
        return json.dumps({"iri": iri})

    modules = {
        "main": SimpleNamespace(
            init_memory=lambda: None,
            materialize_hints=lambda _hint: None,
            export_memory=lambda: {"ttl": ""},
        ),
        "base": SimpleNamespace(),
        "entities": SimpleNamespace(create_NeutralClass=create_neutral),
        "relationships": SimpleNamespace(),
    }
    monkeypatch.setattr(build, "_load_generated_iter1_modules", lambda **_: modules)
    monkeypatch.setattr(build, "publish_top_ttl", lambda **_: {"ok": True})
    monkeypatch.setattr(build, "project_root", str(tmp_path))
    tbox = tmp_path / "ontology.ttl"
    tbox.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "<urn:NeutralClass> a owl:Class .\n",
        encoding="utf-8",
    )

    ok = build._repair_iter1_ttl_with_generated_tools(
        doi_hash=doi_hash,
        data_dir=str(tmp_path),
        ontology_name="neutral",
        meta_config={
            "ontologies": {
                "main": {"name": "neutral", "ttl_file": str(tbox)}
            }
        },
        entity_context_name="runtime-context",
    )

    assert ok
    graph = Graph().parse(doi_dir / "iteration_1.ttl", format="turtle")
    selected = next(graph.subjects(RDFS.label, None))
    assert isinstance(selected, URIRef)
    assert str(next(graph.objects(selected, RDFS.label))) == "selected item"
    assert (selected, build.RDF.type, URIRef("urn:NeutralClass")) in graph


def _write_minimal_iter1_runtime(doi_dir, *, label: str = "UMC-1") -> None:
    doi_dir.mkdir(parents=True, exist_ok=True)
    (doi_dir / "top_entities.txt").write_text(
        f"ChemicalSynthesis-1 [{label}]\n", encoding="utf-8"
    )
    (doi_dir / "top_entity_selection.json").write_text(
        json.dumps(
            {
                "schema_version": "top-entity-selection.v1",
                "class_local": "ChemicalSynthesis",
                "class_iri": "https://example.com/ChemicalSynthesis",
                "source": "pipeline_runtime_policy",
            }
        ),
        encoding="utf-8",
    )


def test_iter1_repair_fail_opens_when_generated_package_has_no_memory_tools(
    monkeypatch, tmp_path
) -> None:
    doi_hash = "no-tools"
    doi_dir = tmp_path / doi_hash
    _write_minimal_iter1_runtime(doi_dir)
    monkeypatch.setattr(
        build,
        "_load_generated_iter1_modules",
        lambda **_: {
            "main": SimpleNamespace(),
            "base": SimpleNamespace(),
            "entities": SimpleNamespace(),
            "relationships": SimpleNamespace(),
        },
    )
    monkeypatch.setattr(build, "publish_top_ttl", lambda **_: {"ok": True})
    monkeypatch.setattr(build, "project_root", str(tmp_path))

    ok = build._repair_iter1_ttl_with_generated_tools(
        doi_hash=doi_hash,
        data_dir=str(tmp_path),
        ontology_name="ontosynthesis",
        meta_config={"ontologies": {"main": {"name": "ontosynthesis"}}},
        entity_context_name="top",
    )

    assert ok
    assert (doi_dir / "iteration_1.ttl").is_file()
    assert (doi_dir / "mcp_run" / "iter1_kg_fail_open.json").is_file()
    graph = Graph().parse(doi_dir / "iteration_1.ttl", format="turtle")
    assert any(
        str(label) == "UMC-1" for label in graph.objects(None, RDFS.label)
    )


def test_iter1_fail_open_writes_json_from_top_txt(tmp_path) -> None:
    doi_hash = "txt-only"
    doi_dir = tmp_path / doi_hash
    _write_minimal_iter1_runtime(doi_dir, label="VMOC-6")

    ok = build._fail_open_iter1_for_extraction(
        doi_hash=doi_hash,
        data_dir=str(tmp_path),
        ontology_name="ontosynthesis",
        meta_config={"ontologies": {"main": {"name": "ontosynthesis"}}},
        meta_task_config_path="configs/meta_task/meta_task_config.json",
        entity_context_name="top",
        reason="generated package exposes neither wrapper nor public memory tools",
    )

    assert ok
    payload = json.loads(
        (doi_dir / "mcp_run" / "iter1_top_entities.json").read_text(encoding="utf-8")
    )
    assert len(payload) == 1
    assert payload[0]["label"] == "VMOC-6"
    assert payload[0]["uri"]
    warning = json.loads(
        (doi_dir / "mcp_run" / "iter1_kg_fail_open.json").read_text(encoding="utf-8")
    )
    assert warning["kind"] == "generated_tool_repair_unavailable"


def test_iter1_needs_repair_false_when_lock_matches_seeded_graph(tmp_path) -> None:
    doi_hash = "locked"
    doi_dir = tmp_path / doi_hash
    _write_minimal_iter1_runtime(doi_dir, label="VMOC-6")
    top_class = "https://example.com/ChemicalSynthesis"
    entities = build._top_entities_from_txt(str(doi_dir), top_class)
    build._seed_iter1_top_entity_lock(
        doi_hash=doi_hash,
        doi_folder=str(doi_dir),
        top_entities=entities,
        top_class_iri=top_class,
        entity_context_name="top",
        entity_context_aliases=["top"],
    )
    import shutil

    shutil.copy2(doi_dir / "memory" / "top.ttl", doi_dir / "iteration_1.ttl")

    assert not build._iter1_needs_generated_top_uri_repair(
        doi_hash=doi_hash,
        data_dir=str(tmp_path),
        ontology_name="ontosynthesis",
        meta_config={"ontologies": {"main": {"name": "ontosynthesis"}}},
    )
