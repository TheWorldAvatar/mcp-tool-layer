"""Frozen 0903 extraction-prompt contracts and mechanical prompt gates.

The configured model authors EXTRACTION / PRE_EXTRACTION prompts against these contracts.
Do not rewrite the English contract strings. MCP script contracts do not live here.
File map: README.md in this folder.
"""

from src.extraction_prompt_generation.generate.extraction_prompts.contracts.markers import (
    _DETERMINISTIC_TBOX_BEGIN,
    _DETERMINISTIC_TBOX_END,
    _NESTED_OWNED_SCALAR_BEGIN,
    _NESTED_OWNED_SCALAR_END,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _canonical_iteration_filename_token,
    _enrich_iteration_spec_with_compiled_scope,
    _is_enrichment_iteration_spec,
    _iteration_has_semantic_scope,
    _iteration_owned_scope,
    _planned_extraction_prompt_paths,
    _prompt_can_build_generation_contract,
    _prompt_iteration_spec,
    _unplanned_prompt_artifact_paths,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.tbox_slice import (
    _prompt_tbox_slice,
    _subclass_comment_projection,
    _warning_marked_tbox_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.sub_contracts import (
    _iter1_pipeline_top_entity_contract,
    _lexical_quantity_hint_contract,
    _pre_extraction_candidate_type_contract,
    _subclass_decision_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.role_and_guidance import (
    _extension_meta_prompt_policy,
    _generic_prompt_pipeline_role,
    _prompt_generation_guidance,
    _prompt_role_contract,
    _semantic_text_natural_ledger_rules,
    _semantic_text_ox_sensitive_ledger_rules,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.generation_contract import (
    _format_nested_owned_dependent_scalar_contract,
    _nested_owned_dependent_scalar_contract,
    _prompt_artifact_generation_contract,
    _semantic_scalar_output_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.materializable import (
    _detach_deterministic_tbox_from_pre_prompt,
    _detach_nested_owned_scalar_from_prompt,
    _external_mcp_prompt_component_text,
    _format_verbatim_tbox_comment_blocks,
    _is_pre_extraction_prompt,
    _materializable_prompt_component_path,
    _materializable_prompt_component_text,
    _pre_extraction_tbox_component_text,
    _prompt_contains_deterministic_component,
    _properties_touching_classes,
    _splice_deterministic_tbox_into_pre_prompt,
    _splice_nested_owned_scalar_into_extraction_prompt,
    _strip_deterministic_tbox_splice,
    _strip_nested_owned_scalar_splice,
    _tbox_ancestor_class_locals,
    _write_materializable_prompt_component,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.gates import (
    _JSON_LEDGER_MANDATE_PATTERNS,
    _ORDER_HEADING_MANDATE_PATTERNS,
    _detach_mechanically_injected_runtime_slots,
    _nested_owned_dependent_scalar_failures,
    _semantic_text_structured_ledger_expectation_failures,
    _validate_generated_prompt_hard_gates,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.repair import (
    _prompt_semantic_repair_task,
    _repair_generated_prompt_semantics,
    _validate_generated_prompt,
    run_llm_unified_diff_editor,
)

__all__ = [
    "_DETERMINISTIC_TBOX_BEGIN",
    "_DETERMINISTIC_TBOX_END",
    "_JSON_LEDGER_MANDATE_PATTERNS",
    "_NESTED_OWNED_SCALAR_BEGIN",
    "_NESTED_OWNED_SCALAR_END",
    "_ORDER_HEADING_MANDATE_PATTERNS",
    "_canonical_iteration_filename_token",
    "_detach_deterministic_tbox_from_pre_prompt",
    "_detach_mechanically_injected_runtime_slots",
    "_detach_nested_owned_scalar_from_prompt",
    "_enrich_iteration_spec_with_compiled_scope",
    "_extension_meta_prompt_policy",
    "_external_mcp_prompt_component_text",
    "_format_nested_owned_dependent_scalar_contract",
    "_format_verbatim_tbox_comment_blocks",
    "_generic_prompt_pipeline_role",
    "_is_enrichment_iteration_spec",
    "_is_pre_extraction_prompt",
    "_iter1_pipeline_top_entity_contract",
    "_iteration_has_semantic_scope",
    "_iteration_owned_scope",
    "_lexical_quantity_hint_contract",
    "_materializable_prompt_component_path",
    "_materializable_prompt_component_text",
    "_nested_owned_dependent_scalar_contract",
    "_nested_owned_dependent_scalar_failures",
    "_planned_extraction_prompt_paths",
    "_pre_extraction_candidate_type_contract",
    "_pre_extraction_tbox_component_text",
    "_prompt_artifact_generation_contract",
    "_prompt_can_build_generation_contract",
    "_prompt_contains_deterministic_component",
    "_prompt_generation_guidance",
    "_prompt_iteration_spec",
    "_prompt_role_contract",
    "_prompt_semantic_repair_task",
    "_prompt_tbox_slice",
    "_properties_touching_classes",
    "_repair_generated_prompt_semantics",
    "_semantic_scalar_output_contract",
    "_semantic_text_natural_ledger_rules",
    "_semantic_text_ox_sensitive_ledger_rules",
    "_semantic_text_structured_ledger_expectation_failures",
    "_splice_deterministic_tbox_into_pre_prompt",
    "_splice_nested_owned_scalar_into_extraction_prompt",
    "_strip_deterministic_tbox_splice",
    "_strip_nested_owned_scalar_splice",
    "_subclass_comment_projection",
    "_subclass_decision_contract",
    "_tbox_ancestor_class_locals",
    "_unplanned_prompt_artifact_paths",
    "_validate_generated_prompt",
    "_validate_generated_prompt_hard_gates",
    "_warning_marked_tbox_contract",
    "_write_materializable_prompt_component",
    "run_llm_unified_diff_editor",
]
