from src.pipelines.top_entity_kg_building.build import bind_iter1_runtime_context


def test_iter1_without_slots_gets_complete_bootstrap_boundary() -> None:
    rendered = bind_iter1_runtime_context(
        "Build top roots.",
        doi_hash="paper-doi",
        paper_content="Paper text.",
        top_entities="Alpha\nBeta",
    )

    assert "PIPELINE-INJECTED ITER1 BOOTSTRAP CONTEXT" in rendered
    assert "Document DOI/hash: paper-doi" in rendered
    assert "Paper/source text:\nPaper text." in rendered
    assert "Upstream top-entity labels:\nAlpha\nBeta" in rendered
    assert "Never invent one shared entity IRI for multiple labels" in rendered


def test_iter1_declared_slots_are_bound_once_but_boundary_is_retained() -> None:
    rendered = bind_iter1_runtime_context(
        "{doi}\n{paper_content}\n{top_entities}",
        doi_hash="paper-doi",
        paper_content="Paper text.",
        top_entities="Alpha\nBeta",
    )

    assert rendered.count("paper-doi") == 1
    assert rendered.count("Paper text.") == 1
    assert rendered.count("Alpha\nBeta") == 1
    assert "Bootstrap rules:" in rendered
    assert "Document DOI/hash:" not in rendered
    assert "Paper/source text:" not in rendered
    assert "Upstream top-entity labels:" not in rendered
