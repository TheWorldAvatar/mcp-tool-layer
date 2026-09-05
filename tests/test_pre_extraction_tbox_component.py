from __future__ import annotations

import inspect
from pathlib import Path

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _DETERMINISTIC_TBOX_BEGIN,
    _DETERMINISTIC_TBOX_END,
    _detach_deterministic_tbox_from_pre_prompt,
    _format_verbatim_tbox_comment_blocks,
    _materializable_prompt_component_text,
    _pre_extraction_tbox_component_text,
    _properties_touching_classes,
    _strip_deterministic_tbox_splice,
    _tbox_ancestor_class_locals,
    _write_materializable_prompt_component,
)
from src.pipelines.main_ontology_extractions.extract import load_prompt
from tests.test_ontomock_domain_smoke import DOMAIN_CONFIG, ROOT, _planner


_GENERIC_RENDERER_SOURCES = (
    _tbox_ancestor_class_locals,
    _properties_touching_classes,
    _format_verbatim_tbox_comment_blocks,
    _pre_extraction_tbox_component_text,
    _strip_deterministic_tbox_splice,
    _detach_deterministic_tbox_from_pre_prompt,
)


def test_verbatim_tbox_comment_helpers_are_ontology_agnostic() -> None:
    leaked = (
        "HeatChill",
        "Filter",
        "Dry",
        "SynthesisStep",
        "ChemicalSynthesis",
        "ChemicalInput",
        "soak",
        "cooling",
        "OntoSyn",
        "ontosynthesis",
    )
    source = "\n".join(inspect.getsource(fn) for fn in _GENERIC_RENDERER_SOURCES)
    for token in leaked:
        assert token not in source


def test_verbatim_tbox_comments_follow_supplied_scope_and_ancestors() -> None:
    parsed = {
        "classes": {
            "Parent": {
                "comment": "PARENT_RULE stays verbatim.",
                "parent_classes": [],
            },
            "Child": {
                "comment": "CHILD_RULE stays verbatim.",
                "parent_classes": ["Parent"],
            },
            "Other": {
                "comment": "OTHER_RULE must stay out of scope.",
                "parent_classes": [],
            },
        },
        "properties": {
            "hasFlag": {
                "comment": "PROP_RULE stays verbatim.",
                "domains": ["Child"],
                "kind": "datatype",
            },
            "hasOther": {
                "comment": "OTHER_PROP must stay out of scope.",
                "domains": ["Other"],
                "kind": "datatype",
            },
        },
    }

    child_only = _format_verbatim_tbox_comment_blocks(
        parsed,
        class_locals=["Child"],
        include_ancestors=False,
    )
    assert "CHILD_RULE stays verbatim." in child_only
    assert "PROP_RULE stays verbatim." in child_only
    assert "PARENT_RULE stays verbatim." not in child_only
    assert "OTHER_RULE must stay out of scope." not in child_only
    assert "OTHER_PROP must stay out of scope." not in child_only

    with_ancestors = _format_verbatim_tbox_comment_blocks(
        parsed,
        class_locals=["Child"],
        include_ancestors=True,
    )
    assert "PARENT_RULE stays verbatim." in with_ancestors
    assert "CHILD_RULE stays verbatim." in with_ancestors
    assert "OTHER_RULE must stay out of scope." not in with_ancestors


def test_ontomock_pre_component_injects_scoped_tbox_verbatim(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    target = Path(context.prompts_dir) / "PRE_EXTRACTION_ITER_3.md"
    component = _materializable_prompt_component_text(context, target)
    written = _write_materializable_prompt_component(context, target)

    assert written is not None
    assert written.name == "PRE_EXTRACTION_ITER_3.materializable.inc"
    assert "Scoped T-Box Contract (mechanically injected)" in component
    assert "Abstract parent for ordered actions" in component
    assert "An explicit operation that introduces one named Input." in component
    assert "True or false only when the source explicitly states the enabled state." in (
        component
    )
    assert "SEMANTIC_HINTS_V1" not in component
    for foreign_local in (
        "ChemicalInput",
        "ChemicalSynthesis",
        "HeatChill",
        "hasAddedChemicalInput",
        "hasWashingSolvent",
    ):
        assert foreign_local not in component

    target.write_text("PRE ledger instructions.\n", encoding="utf-8")
    written = _write_materializable_prompt_component(context, target)
    assert written is not None
    spliced = target.read_text(encoding="utf-8")
    assert spliced.startswith("PRE ledger instructions.")
    assert _DETERMINISTIC_TBOX_BEGIN in spliced
    assert _DETERMINISTIC_TBOX_END in spliced
    assert spliced.count("Abstract parent for ordered actions") == 1
    _write_materializable_prompt_component(context, target)
    assert (
        target.read_text(encoding="utf-8").count("Abstract parent for ordered actions")
        == 1
    )
    rendered = load_prompt(str(target))
    assert rendered.count("Abstract parent for ordered actions") == 1
    _detach_deterministic_tbox_from_pre_prompt(target)
    assert _DETERMINISTIC_TBOX_BEGIN not in target.read_text(encoding="utf-8")
    assert _strip_deterministic_tbox_splice(spliced) == "PRE ledger instructions."
