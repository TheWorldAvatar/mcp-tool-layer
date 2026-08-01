from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    validate_prompt_runtime_bindings,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _prompt_artifact_generation_contract,
    _resolve_top_entity_from_tbox,
)
from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis import (
    _preserves_original_prompt,
    _validate_prompt_binding_candidate,
)


def test_extraction_prompt_requires_runtime_content_slot(tmp_path: Path) -> None:
    prompt = tmp_path / "EXTRACTION_ITER_2.md"
    prompt.write_text("Extract facts from the supplied document.", encoding="utf-8")

    report = validate_prompt_runtime_bindings(prompt)

    assert not report["ok"]
    assert report["failures"]


def test_iter1_generation_contract_uses_pipeline_top_entity_selection(
    tmp_path: Path,
) -> None:
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "ontology": type("Ontology", (), {"name": "ontosynthesis"})(),
            "parsed": {
                "classes": {
                    "ChemicalSynthesis": {
                        "iri": "https://example.test/ChemicalSynthesis",
                        "parent_classes": [],
                        "comment": "A source-supported synthesis process.",
                    }
                }
            },
            "contract": {
                "runtime_policies": {
                    "iter1_top_entity_kg": {
                        "prompt_rules": {
                            "top_level_entity_name": "ChemicalSynthesis"
                        }
                    },
                    "top_entity_extraction": {
                        "count_lines_starting_with": ["ChemicalSynthesis"],
                        "identifier_code_regex": "CODE",
                    },
                    "iteration_plan": {},
                }
            },
        },
    )()
    target = tmp_path / "EXTRACTION_ITER_1.md"

    contract = _prompt_artifact_generation_contract(context, target)

    selected = contract["tbox_scope"]["pipeline_selected_top_entity"]
    assert selected["class_local"] == "ChemicalSynthesis"
    assert selected["line_prefix"] == "ChemicalSynthesis"
    assert "ChemicalSynthesis-N" in selected["output_contract"]
    assert contract["tbox_scope"]["classes"] == {
        "ChemicalSynthesis": {
            "iri": "https://example.test/ChemicalSynthesis",
            "parent_classes": [],
            "comment": "A source-supported synthesis process.",
        }
    }


def test_context_alias_does_not_satisfy_runtime_content_contract(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "EXTRACTION_ITER_1.md"
    prompt.write_text("Runtime context: {context}", encoding="utf-8")

    report = validate_prompt_runtime_bindings(prompt)

    assert not report["ok"]
    assert report["evidence"]["missing_slot_groups"] == [["{paper_content}"]]


def test_kg_prompt_accepts_hints_injection_slot(tmp_path: Path) -> None:
    prompt = tmp_path / "KG_BUILDING_ITER_2.md"
    prompt.write_text(
        "Document: {doi}\nEntity: {entity_label} ({entity_uri})\n"
        "Materialize only these extracted hints:\n{iteration_hints}\n",
        encoding="utf-8",
    )

    report = validate_prompt_runtime_bindings(prompt)

    assert report["ok"]


def test_kg_prompt_rejects_legacy_paper_content_hint_channel(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "KG_BUILDING_ITER_2.md"
    prompt.write_text(
        "Document: {doi}\nEntity: {entity_label} ({entity_uri})\n"
        "Materialize hints from:\n{paper_content}\n",
        encoding="utf-8",
    )

    report = validate_prompt_runtime_bindings(prompt)

    assert not report["ok"]
    assert report["evidence"]["missing_slot_groups"] == [["{iteration_hints}"]]


def test_kg_prompt_rejects_paper_content_even_when_hints_are_present(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "KG_BUILDING_ITER_2.md"
    prompt.write_text(
        "Document: {doi}\nEntity: {entity_label} ({entity_uri})\n"
        "Materialize only these extracted hints:\n{iteration_hints}\n"
        "Do not use this source text:\n{paper_content}\n",
        encoding="utf-8",
    )

    report = validate_prompt_runtime_bindings(prompt)

    assert not report["ok"]
    assert report["evidence"]["forbidden_slots"] == ["paper_content"]


def test_kg_prompt_rejects_legacy_doi_alias_even_when_hints_are_present(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "KG_BUILDING_ITER_2.md"
    prompt.write_text(
        "Document: {doi} / {hash}\nEntity: {entity_label} ({entity_uri})\n"
        "Materialize only these extracted hints:\n{iteration_hints}\n",
        encoding="utf-8",
    )

    report = validate_prompt_runtime_bindings(prompt)

    assert not report["ok"]
    assert report["evidence"]["forbidden_slots"] == ["hash"]


def test_kg_iter2_generation_contract_matches_pipeline_slots(tmp_path: Path) -> None:
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        output_root=tmp_path,
        write_files=True,
    )

    contract = _prompt_artifact_generation_contract(
        context,
        Path(context.prompts_dir) / "KG_BUILDING_ITER_2.md",
    )

    assert contract["runtime_binding_contract"]["allowed_slots"] == [
        "{iteration_hints}",
        "{doi}",
        "{entity_label}",
        "{entity_uri}",
    ]


def test_top_entity_is_semantically_selected_without_rdf_role_annotation(
    tmp_path: Path, monkeypatch
) -> None:
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        output_root=tmp_path,
        write_files=True,
    )
    assert context.contract["top_entity"]["status"] == "unknown"

    class Response:
        data = {
            "class_local": "ChemicalSynthesis",
            "rationale": "It organizes the procedure-level inputs, outputs, and steps.",
            "evidence": [
                "ChemicalSynthesis",
                "hasChemicalInput",
                "hasChemicalOutput",
                "hasSynthesisStep",
            ],
        }

    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.pure_llm_generation.invoke_json",
        lambda *args, **kwargs: Response(),
    )

    top = _resolve_top_entity_from_tbox(context, model_name="unused")

    assert top["class_local"] == "ChemicalSynthesis"
    assert top["class_iri"].endswith("/ChemicalSynthesis")
    assert top["source"] == "llm_tbox_semantic_selection"
    assert context.contract["top_entity"] == top


def test_iter1_kg_requires_document_and_top_entity_channels(tmp_path: Path) -> None:
    prompt = tmp_path / "KG_BUILDING_ITER_1.md"
    prompt.write_text("Document:\n{paper_content}\n", encoding="utf-8")

    report = validate_prompt_runtime_bindings(prompt)

    assert not report["ok"]
    assert len(report["failures"]) == 2


def test_runtime_binding_candidate_must_preserve_original_as_subsequence() -> None:
    original = "Task\n- Keep this rule.\nOutput schema\n- label: string\n"

    assert _preserves_original_prompt(
        original,
        "Runtime: {paper_content}\n" + original + "Entity: {entity_label}\n",
    )
    assert not _preserves_original_prompt(
        original,
        original.replace("- Keep this rule.\n", ""),
    )


def test_semantic_reviewer_rejection_fails_candidate(tmp_path: Path) -> None:
    target = tmp_path / "EXTRACTION_ITER_2.md"
    original = "Task\nOutput schema\n- label: string\n"
    target.write_text(
        original
        + "\nRuntime context\n"
        + "- Source: {paper_content}\n"
        + "- Label: {entity_label}\n"
        + "- URI: {entity_uri}\n",
        encoding="utf-8",
    )

    report = _validate_prompt_binding_candidate(
        target=target,
        original=original,
        model="unused",
        generation_contract={},
        semantic_reviewer=lambda **_: {
            "ok": False,
            "violations": ["runtime URI was mapped into the output schema"],
            "rationale": "schema expansion",
        },
    )

    assert not report["ok"]
    assert report["insertion_only"]
    assert "output schema" in " ".join(report["failures"])
