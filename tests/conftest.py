"""Collection policy for independently runnable generated-package test stages."""

from __future__ import annotations

import pytest


_STAGE_TESTS = {
    "mcp_surface": {
        "test_tool_surface_rejects_exposed_generic_mutation_helper",
        "test_launcher_adapts_object_tool_registry",
    },
    "contract_rejection": {
        "test_fixed_runtime_rejects_wrong_range_without_mutating_graph",
        "test_fixed_runtime_datatype_capability_rejects_wrong_value_without_mutation",
        "test_public_relationship_tool_rejects_wrong_range_before_mutation",
    },
    "package_runtime": {
        "test_fixed_rdf_runtime_serializes_and_resets_graph",
        "test_fixed_runtime_create_link_export_share_retained_graph",
        "test_fixed_runtime_loads_persisted_turtle_for_resume",
        "test_harness_and_hermit_gate",
        "test_semantic_poison_fails_reasoner_gate",
    },
    "semantic_acceptance": {
        "test_semantic_judge_aggregates_independent_soft_scores",
        "test_semantic_acceptance_requires_every_dimension_and_no_critical_errors",
        "test_semantic_judge_uses_adjudicator_when_judges_disagree",
    },
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply stable stage markers without coupling production code to test layout."""
    for item in items:
        for marker_name, test_names in _STAGE_TESTS.items():
            if item.name in test_names:
                item.add_marker(getattr(pytest.mark, marker_name))
