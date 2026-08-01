from src.pipelines.main_kg_building.build import bind_kg_runtime_context


def test_kg_iter2_legacy_paper_slot_receives_extracted_hints_only() -> None:
    rendered, warnings = bind_kg_runtime_context(
        "Materialize:\n{paper_content}",
        doi_hash="doi",
        entity_label="entity",
        entity_uri="urn:entity",
        hints_content='{"Add": [{"label": "source fact"}]}',
        iter_num=2,
    )

    assert "{paper_content}" not in rendered
    assert "ExtractedHints:" in rendered
    assert '"source fact"' in rendered
    assert warnings == [
        "Legacy KG Iteration 2+ prompt used {paper_content}; bound it to "
        "ExtractedHints rather than raw paper content."
    ]


def test_kg_iter2_without_hint_slot_gets_pipeline_boundary() -> None:
    rendered, warnings = bind_kg_runtime_context(
        "Use MCP tools.",
        doi_hash="doi",
        entity_label="entity",
        entity_uri="urn:entity",
        hints_content="{}",
        iter_num=2,
    )

    assert "PIPELINE-INJECTED EXTRACTED HINTS" in rendered
    assert "ExtractedHints:" in rendered
    assert "Document DOI/hash: doi" in rendered
    assert "Current entity label: entity" in rendered
    assert "Current entity exact URI" in rendered
    assert len(warnings) == 1


def test_kg_iter2_declared_hint_slot_has_no_compatibility_warning() -> None:
    rendered, warnings = bind_kg_runtime_context(
        "Materialize:\n{iteration_hints}",
        doi_hash="doi",
        entity_label="entity",
        entity_uri="urn:entity",
        hints_content="{}",
        iter_num=2,
    )

    assert "{iteration_hints}" not in rendered
    assert "ExtractedHints:" in rendered
    assert warnings == []


def test_kg_entity_context_appends_only_undeclared_identity_channels() -> None:
    rendered, warnings = bind_kg_runtime_context(
        "DOI={doi}\nEntity={entity_label}\nURI={entity_uri}\n{iteration_hints}",
        doi_hash="doi",
        entity_label="entity",
        entity_uri="urn:entity",
        hints_content="{}",
        iter_num=2,
    )

    assert rendered.count("doi") == 1
    assert rendered.count("entity") == 2
    assert "PIPELINE-INJECTED ENTITY RUNTIME CONTEXT" not in rendered
    assert warnings == []


def test_kg_legacy_hint_slot_still_gets_missing_identity_context() -> None:
    rendered, warnings = bind_kg_runtime_context(
        "Materialize:\n{paper_content}",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="https://example.com/route",
        hints_content="facts",
        iter_num=3,
    )

    assert rendered.count("facts") == 1
    assert "Document DOI/hash: paper-doi" in rendered
    assert "Current entity label: route" in rendered
    assert "https://example.com/route" in rendered
    assert len(warnings) == 1
