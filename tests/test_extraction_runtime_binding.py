from src.pipelines.main_ontology_extractions.extract import bind_runtime_context


def test_extraction_pipeline_appends_undeclared_source_channel() -> None:
    rendered = bind_runtime_context(
        "Extract T-Box-constrained facts.",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Source text.",
    )

    assert "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----" in rendered
    assert "Source text." in rendered
    assert "Document DOI/hash: paper-doi" in rendered
    assert "Current entity label: route" in rendered
    assert "Current entity exact URI: urn:route" in rendered


def test_extraction_pipeline_replaces_declared_channels_without_duplication() -> None:
    rendered = bind_runtime_context(
        "{entity_label} {entity_uri}\n{paper_content}\n{iteration_input}",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Source text.",
        iteration_input="Previous hints.",
    )

    assert rendered == "route urn:route\nSource text.\nPrevious hints."
    assert "PIPELINE-INJECTED" not in rendered


def test_extraction_pipeline_replaces_doi_and_avoids_identity_duplication() -> None:
    rendered = bind_runtime_context(
        "{doi} {entity_label} {entity_uri}\n{context}",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Source text.",
    )

    assert rendered == "paper-doi route urn:route\nSource text."
    assert "PIPELINE-INJECTED" not in rendered


def test_enrichment_style_binding_includes_exact_uri_and_inputs_once() -> None:
    rendered = bind_runtime_context(
        "Enrich the current entity.",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Entity source.",
        iteration_input="Base hints.",
    )

    assert rendered.count("urn:route") == 1
    assert rendered.count("Entity source.") == 1
    assert rendered.count("Base hints.") == 1
