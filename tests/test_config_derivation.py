from __future__ import annotations

import json

import pytest

from src.agents.scripts_and_prompts_generation.config_derivation import (
    build_candidate_reuse_policy,
    derive_orchestration_config,
    promote_reviewed_reuse_policy,
    validate_tbox_only_orchestration_config,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import load_reuse_policy


def test_derivation_removes_semantic_priors_and_preserves_execution_wiring() -> None:
    raw = {
        "schema_version": "legacy",
        "domain_id": "example",
        "tbox": {"primary": "ontology.ttl", "supporting": ["base.ttl"]},
        "models": {"script_generation": "gpt-5"},
        "reuse_policy": "reviewed_reuse.json",
        "runtime": {
            "required_link_bindings": [{"predicate": "hasThing"}],
            "ordered_member_contracts": [{"member_class": "Thing"}],
            "binding": {
                "execution_channel": "extension",
                "upstream_scope": {"class_local": "DomainRoot"},
            },
            "extensions": [
                {
                    "name": "extension",
                    "description": "Domain-specific prose.",
                    "ttl_file": "base.ttl",
                    "mcp_list": ["extension_tool"],
                }
            ],
            "workflow": {
                "pipeline_iteration_number": 2,
                "iterations": [
                    {
                        "name": "main",
                        "use_agent": True,
                        "max_attempts": 3,
                        "linked_materialization_classes": ["Thing"],
                        "extraction_validation": {
                            "required_tool_groups": [["lookup_thing"]],
                            "required_materialization": ["Thing"],
                        },
                    }
                ],
            },
        },
    }

    derived, removed = derive_orchestration_config(raw)

    assert derived["schema_version"] == "domain-generation-config.v1"
    assert derived["derivation"]["semantic_authority"] == "tbox_bundle_only"
    assert derived["reuse_policy"] == "reviewed_reuse.json"
    assert "required_link_bindings" not in derived["runtime"]
    assert "ordered_member_contracts" not in derived["runtime"]
    assert "upstream_scope" not in derived["runtime"]["binding"]
    assert derived["runtime"]["extensions"][0]["description"] == "Domain-specific prose."
    assert derived["runtime"]["extensions"][0]["mcp_list"] == ["extension_tool"]
    iteration = derived["runtime"]["workflow"]["iterations"][0]
    assert "linked_materialization_classes" not in iteration
    assert "extraction_validation" not in iteration
    assert iteration["use_agent"] is True
    assert iteration["max_attempts"] == 3
    assert derived["models"] == raw["models"]
    assert derived["tbox"] == raw["tbox"]
    assert removed == [
        "runtime.ordered_member_contracts",
        "runtime.required_link_bindings",
        "runtime.workflow.iterations[0].linked_materialization_classes",
        "runtime.workflow.iterations[0].name",
        "runtime.workflow.iterations[0].extraction_validation",
        "runtime.binding.upstream_scope",
    ]
    validate_tbox_only_orchestration_config(derived)

    derived["runtime"]["ordered_member_contracts"] = [{"member_class": "Thing"}]
    with pytest.raises(ValueError, match="runtime.ordered_member_contracts"):
        validate_tbox_only_orchestration_config(derived)
    derived["runtime"].pop("ordered_member_contracts")
    derived["hidden_domain_prior"] = "Thing"
    with pytest.raises(ValueError, match="hidden_domain_prior"):
        validate_tbox_only_orchestration_config(derived)


def test_candidate_reuse_policy_requires_manual_match_basis_approval(
    tmp_path,
) -> None:
    class_iri = "https://example.test/StableThing"
    summary = {
        "passed_10_of_10_gate": True,
        "class_stability": [{"class_iri": class_iri}],
    }
    trial = {
        "parsed_response": {
            "reusable_classes": [
                {
                    "class_iri": class_iri,
                    "reuse_scope": "document",
                    "match_basis": "stable identifier",
                    "false_merge_risk": "identifier collision",
                    "confidence": "high",
                }
            ],
            "non_reusable_classes": [],
        }
    }
    policy = build_candidate_reuse_policy(
        summary=summary,
        representative_trial=trial,
    )
    assert policy["classes"][0]["review"] == "pending_match_basis_review"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="complete match_basis review"):
        load_reuse_policy(path)


def test_promote_reviewed_reuse_policy_requires_every_reusable_basis(
    tmp_path,
) -> None:
    source = tmp_path / "candidate.json"
    output = tmp_path / "runtime.json"
    payload = {
        "schema_version": "binary-class-reuse-review.v0",
        "generated_candidate": True,
        "status": "approved_for_runtime",
        "classes": [
            {
                "class_iri": "https://example.test/StableThing",
                "class_local": "StableThing",
                "reusable": True,
                "reuse_scope": "document",
                "match_basis": "stable identifier",
                "review": "pending_match_basis_review",
            }
        ],
    }
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="still lack approved match_basis"):
        promote_reviewed_reuse_policy(source_path=source, output_path=output)

    payload["classes"][0]["review"] = "approved_match_basis"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = promote_reviewed_reuse_policy(
        source_path=source,
        output_path=output,
    )
    assert result["reusable_count"] == 1
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert "source_path" not in load_reuse_policy(output)
