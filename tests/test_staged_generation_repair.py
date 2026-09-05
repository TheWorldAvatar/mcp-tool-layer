"""Tests for LLM-planned staged artifact repair."""

from __future__ import annotations

import json
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.scripts_and_prompts_generation import pure_llm_generation
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    OntologySpec,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import LLMJsonResult


def _context(tmp_path: Path) -> AgenticGenerationContext:
    scripts = tmp_path / "scripts" / "onto"
    prompts = tmp_path / "prompts" / "onto"
    structures = tmp_path / "ontology_structures" / "onto"
    reports = tmp_path / "reports" / "onto"
    scripts.mkdir(parents=True)
    prompts.mkdir(parents=True)
    (scripts / "main.py").write_text("", encoding="utf-8")
    for suffix in (
        "_creation_entities.py",
        "_creation_relationships.py",
        "_creation_checks.py",
    ):
        (scripts / f"onto{suffix}").write_text("__all__ = []\n", encoding="utf-8")
    (prompts / "ITER.md").write_text("", encoding="utf-8")
    return AgenticGenerationContext(
        output_root=str(tmp_path),
        ontology_structure_dir=str(structures),
        scripts_dir=str(scripts),
        prompts_dir=str(prompts),
        parsed_summary_path=str(structures / "parsed.json"),
        parsed_markdown_path=str(structures / "parsed.md"),
        contract_path=str(structures / "generation_contract.json"),
        integrity_profile_path=str(structures / "integrity_profile.json"),
        report_path=str(reports / "generation_report.json"),
        config_provenance_path=str(structures / "config_provenance.json"),
        parsed={"classes": {}, "properties": {}},
        contract={"ontology_name": "onto"},
        integrity_profile={},
        pipeline_runtime_policies={},
        iteration_blueprint={},
        config_provenance={},
        ontology=OntologySpec(
            name="onto",
            role="main",
            ttl_file="onto.ttl",
            meta_task_config_path="meta.json",
        ),
    )


def test_focused_validation_projection_bounds_large_failure_evidence() -> None:
    focus_id = "generation.external_failures::onto"
    report = {
        "failures": ["x" * 10000 for _ in range(20)],
        "observations": [
            {
                "check_id": "generation.external_failures",
                "subject_key": "onto",
                "status": "fail",
                "stage": "semantic",
                "message": "m" * 10000,
                "evidence": {"failures": ["e" * 10000 for _ in range(20)]},
                "observed_artifacts": ["scripts/onto/main.py"],
                "blocked_by": [],
            },
            {
                "check_id": "unrelated",
                "subject_key": "onto",
                "status": "fail",
                "evidence": {"failures": ["must not be projected"]},
            },
        ],
    }

    projection = pure_llm_generation._focused_validation_projection(
        report, {"observation_ids": [focus_id]}
    )

    assert len(projection["failure_summary"]) == 8
    assert len(projection["focus_observations"]) == 1
    assert projection["focus_observations"][0]["observation_id"] == focus_id
    assert "unrelated" not in str(projection)
    assert len(str(projection)) < 25000


def test_all_production_script_prompts_embed_capability_security_contract(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    report = {"ok": False, "failures": ["probe"]}
    plan = {"steps": []}
    prompts = [
        pure_llm_generation._generation_task(
            context=context,
            report=report,
            round_index=1,
            generate_scripts=True,
            generate_prompts=False,
            target=Path(context.scripts_dir) / "main.py",
        ),
        pure_llm_generation._package_synthesis_task(
            context=context,
            report=report,
        ),
        pure_llm_generation._runtime_adapter_synthesis_task(
            context=context,
            report=report,
        ),
        pure_llm_generation._creation_foundation_synthesis_task(
            context=context,
            report=report,
        ),
        pure_llm_generation._repair_task(
            context=context,
            plan=plan,
            report=report,
        ),
    ]

    for prompt in prompts:
        assert pure_llm_generation.MCP_CAPABILITY_SECURITY_CONTRACT in prompt
        assert "may exist and may be used internally" in prompt
        assert "Never register or otherwise expose generic" in prompt
        assert "MCP tool registry" in prompt


def test_entity_prompt_context_contains_only_owned_parsed_classes(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context = replace(
        context,
        parsed={
            "classes": {
                "Owned": {"iri": "urn:owned:Owned", "parent_classes": []},
            }
        },
        contract={
            "ontology_publish_contract": {
                "classes": [
                    {"class_iri": "urn:owned:Owned"},
                    {"class_iri": "urn:external:Referenced"},
                    {"class_iri": "http://www.w3.org/2001/XMLSchema#integer"},
                ]
            }
        },
    )
    target = Path(context.scripts_dir) / "synthetic_creation_entities.py"

    prompt = pure_llm_generation._generation_task(
        context=context,
        report={"ok": False, "failures": []},
        round_index=1,
        generate_scripts=True,
        generate_prompts=False,
        target=target,
    )

    assert '"public_tool": "create_Owned"' in prompt
    assert "create_Referenced" not in prompt
    assert "create_integer" not in prompt
    assert "invalid labels are rejected before graph mutation" in prompt
    assert "normalized label calls reuse the same IRI" in prompt


def test_iter1_kg_meta_contract_is_generic_and_tbox_projection_isolated(
    tmp_path: Path,
) -> None:
    target = tmp_path / "prompts" / "synthetic" / "KG_BUILDING_ITER_1.md"
    target.parent.mkdir(parents=True)

    def context_for(
        ontology_name: str, class_local: str, class_iri: str
    ) -> SimpleNamespace:
        return SimpleNamespace(
            output_root=str(tmp_path / ontology_name),
            contract={
                "top_entity": {
                    "class_local": class_local,
                    "class_iri": class_iri,
                    "iter1_allows_multiple": True,
                    "main_pass_reuses_scoped_root": False,
                },
                "required_links": [
                    {
                        "predicate_iri": f"urn:{ontology_name}:downstream",
                        "target_class_iri": f"urn:{ontology_name}:Child",
                    }
                ],
            },
            parsed={
                "classes": {
                    class_local: {
                        "iri": class_iri,
                        "parent_classes": [],
                        "comment": "",
                    }
                },
                "properties": {},
            },
            ontology=SimpleNamespace(name=ontology_name, role="main"),
        )

    alpha = pure_llm_generation._prompt_artifact_generation_contract(
        context_for("alpha", "Record", "urn:alpha:Record"), target
    )
    beta = pure_llm_generation._prompt_artifact_generation_contract(
        context_for("beta", "Observation", "urn:beta:Observation"), target
    )

    assert alpha["generic_pipeline_role"] == beta["generic_pipeline_role"]
    assert alpha["runtime_binding_contract"]["allowed_slots"] == [
        "{doi}",
        "{paper_content}",
        "{top_entities}",
    ]
    assert alpha["required_links"] == []
    assert beta["required_links"] == []
    generic_text = str(alpha["generic_pipeline_role"])
    assert "Hints need only provide source-supported labels" in generic_text
    assert "taking the root class only from the active T-Box projection" in generic_text
    assert "never invent, derive, or hardcode a scope name" in generic_text
    assert "Do not enumerate domain-specific child types" in generic_text
    assert "<type-prefix>-<index> [<label>]" in generic_text
    assert "Render the exact creator_tool supplied by tbox_scope.top_entity" in generic_text
    assert "Deduplicate equal normalized labels" in generic_text
    assert alpha["tbox_scope"]["top_entity"] == {
        "class_local": "Record",
        "class_iri": "urn:alpha:Record",
        "creator_tool": "create_Record",
        "allows_multiple_source_roots": True,
        "reuse_scoped_root": False,
        "source": "active_tbox_projection",
    }
    assert "Record" not in str(alpha["generic_pipeline_role"])
    assert "Observation" not in str(beta["generic_pipeline_role"])


def test_dependency_constraints_require_checks_after_tool_layers() -> None:
    constraints = pure_llm_generation._artifact_dependency_constraints(
        [
            Path("scripts/x/x_creation_base.py"),
            Path("scripts/x/x_creation_checks.py"),
            Path("scripts/x/x_creation_entities.py"),
            Path("scripts/x/x_creation_relationships.py"),
            Path("scripts/x/main.py"),
        ]
    )

    assert {
        "before": "scripts/x/x_creation_entities.py",
        "after": "scripts/x/x_creation_checks.py",
    } in constraints
    assert {
        "before": "scripts/x/x_creation_relationships.py",
        "after": "scripts/x/x_creation_checks.py",
    } in constraints


def test_fixed_dependency_order_matches_pipeline_architecture(tmp_path: Path) -> None:
    paths = [
        tmp_path / "scripts/x/KG_BUILDING_ITER_2.md",
        tmp_path / "scripts/x/main.py",
        tmp_path / "scripts/x/x_creation_relationships.py",
        tmp_path / "scripts/x/EXTRACTION_ITER_1.md",
        tmp_path / "scripts/x/x_creation_checks.py",
        tmp_path / "scripts/x/x_creation_base.py",
        tmp_path / "scripts/x/x_creation_entities.py",
    ]

    ordered = pure_llm_generation._fixed_artifact_dependency_order(
        root=tmp_path,
        targets=paths,
    )

    assert [Path(value).name for value in ordered] == [
        "x_creation_base.py",
        "x_creation_entities.py",
        "x_creation_relationships.py",
        "x_creation_checks.py",
        "main.py",
        "EXTRACTION_ITER_1.md",
        "KG_BUILDING_ITER_2.md",
    ]


def test_parallel_generation_waves_follow_dependency_boundaries(
    tmp_path: Path,
) -> None:
    targets = [
        tmp_path / "x_creation_base.py",
        tmp_path / "x_creation_entities.py",
        tmp_path / "x_creation_relationships.py",
        tmp_path / "x_creation_checks.py",
        tmp_path / "main.py",
        tmp_path / "EXTRACTION_ITER_1.md",
        tmp_path / "KG_BUILDING_ITER_2.md",
    ]

    assert pure_llm_generation._parallel_generation_wave(
        targets[0], targets
    ) == [targets[0]]
    assert pure_llm_generation._parallel_generation_wave(
        targets[1], targets
    ) == [targets[1], targets[2]]
    assert pure_llm_generation._parallel_generation_wave(
        targets[3], targets
    ) == [targets[3]]
    assert pure_llm_generation._parallel_generation_wave(
        targets[5], targets
    ) == [targets[5], targets[6]]


def test_parallel_generation_wave_is_bounded_and_collects_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    targets = [Path(context.prompts_dir) / f"ITER_{index}.md" for index in range(4)]
    for target in targets:
        target.write_text("", encoding="utf-8")
    observed_workers: list[int] = []
    observed_contexts: list[str] = []

    class FakeProcessPool:
        def __init__(self, *, max_workers, mp_context):
            observed_workers.append(max_workers)
            assert mp_context.get_start_method() == "spawn"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit(self, fn, payload):
            future = Future()
            future.set_result(fn(payload))
            return future

    def fake_editor(*, targets, **_kwargs):
        observed_contexts.append(str(targets[0]))
        targets[0].write_text("generated", encoding="utf-8")
        return {"ok": True, "changed_files": [str(targets[0])], "failures": []}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )
    monkeypatch.setattr(
        pure_llm_generation, "_generation_task", lambda **_kwargs: "test task"
    )
    monkeypatch.setattr(
        pure_llm_generation, "ProcessPoolExecutor", FakeProcessPool
    )

    results = pure_llm_generation._generate_artifact_wave(
        context=context,
        report={"ok": False, "failures": []},
        targets=targets,
        model_name="test",
        edit_backend="exact_edits",
        max_workers=2,
    )

    assert observed_workers == [2]
    assert len(set(observed_contexts)) == len(targets)
    assert all(str(tmp_path.resolve()) not in value for value in observed_contexts)
    assert set(results) == set(targets)
    assert all(result["ok"] for result in results.values())
    assert all(target.read_text(encoding="utf-8") == "" for target in targets)
    for target in targets:
        patch = pure_llm_generation._publish_isolated_candidate(
            target, results[target]
        )
        assert patch["ok"]
        assert "_isolated_candidate_bytes" not in patch
        assert target.read_text(encoding="utf-8") == "generated"


def test_parallel_attempt_log_preserves_every_validation_failure(
    tmp_path: Path,
) -> None:
    attempts = [
        {
            "attempt": attempt,
            "ok": False,
            "failures": [{"code": "candidate_validation_failed"}],
            "validation": {
                "ok": False,
                "failures": [f"ITER_3.md: failure from attempt {attempt}"],
            },
            "rollback_performed": True,
            "elapsed_seconds": float(attempt),
        }
        for attempt in range(1, 6)
    ]

    pure_llm_generation._persist_parallel_candidate_attempts(
        output_root=tmp_path,
        ontology_name="onto",
        artifact="prompts/onto/ITER_3.md",
        patch={"ok": False, "attempts": attempts},
    )

    log_path = (
        tmp_path
        / "reports"
        / "onto"
        / "parallel_candidate_attempts.jsonl"
    )
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["attempt"] for record in records] == [1, 2, 3, 4, 5]
    assert records[0]["artifact"] == "prompts/onto/ITER_3.md"
    assert records[-1]["validation_failures"] == [
        "ITER_3.md: failure from attempt 5"
    ]


def test_parallel_wave_defers_successful_sibling_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    targets = [Path(context.prompts_dir) / f"ITER_{index}.md" for index in range(2)]
    for target in targets:
        target.write_text("", encoding="utf-8")

    class FakeProcessPool:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit(self, fn, payload):
            future = Future()
            future.set_result(fn(payload))
            return future

    def fake_editor(*, targets, **_kwargs):
        target = targets[0]
        if target.name == "ITER_0.md":
            target.write_text("accepted", encoding="utf-8")
            return {"ok": True, "changed_files": [str(target)], "failures": []}
        return {"ok": False, "changed_files": [], "failures": ["provider_error"]}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )
    monkeypatch.setattr(
        pure_llm_generation, "_generation_task", lambda **_kwargs: "test task"
    )
    monkeypatch.setattr(
        pure_llm_generation, "ProcessPoolExecutor", FakeProcessPool
    )

    results = pure_llm_generation._generate_artifact_wave(
        context=context,
        report={"ok": False, "failures": []},
        targets=targets,
        model_name="test",
        edit_backend="exact_edits",
        max_workers=2,
    )

    assert results[targets[0]]["ok"]
    assert not results[targets[1]]["ok"]
    assert targets[0].read_text(encoding="utf-8") == ""
    assert targets[1].read_text(encoding="utf-8") == ""

    published = pure_llm_generation._publish_isolated_candidate(
        targets[0], results[targets[0]]
    )
    assert published["ok"]
    assert targets[0].read_text(encoding="utf-8") == "accepted"


def test_parallel_candidate_publication_fails_closed_without_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "ITER.md"
    target.write_text("", encoding="utf-8")

    result = pure_llm_generation._publish_isolated_candidate(
        target,
        {"ok": True, "failures": []},
    )

    assert result["ok"] is False
    assert result["failures"] == ["isolated_candidate_bytes_missing"]
    assert target.read_text(encoding="utf-8") == ""


def test_reviewer_acceptance_clears_stale_mechanical_failure_fields() -> None:
    result = pure_llm_generation._validation_outcome(
        {
            "ok": False,
            "failures": ["generation package still has out-of-scope failures"],
            "focus_progress": True,
            "protected_regression": False,
        },
        accepted=True,
        rejection_failure="delta_reviewer_accept:must not survive",
        delta_review={"decision": "accept", "reason": "focused progress"},
    )

    assert result["ok"] is True
    assert result["failures"] == []
    assert result["delta_review"]["decision"] == "accept"


def test_repair_planner_rejects_targets_outside_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    targets = pure_llm_generation._editable_artifacts(
        context, generate_scripts=True, generate_prompts=True
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *args, **kwargs: LLMJsonResult(
            data={
                "status": "inspect",
                "inspection_question": "escape",
                "inspect_paths": ["../outside.py"],
                "hypotheses": [],
                "why_these_files": "",
            },
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    with pytest.raises(ValueError, match="outside inventory"):
        pure_llm_generation._request_inspection_scope(
            model_name="test",
            context=context,
            targets=targets,
            report={"ok": False, "failures": ["failure"]},
            step_index=1,
            previous_steps=[],
        )


def test_repair_focus_selects_known_golden_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    observation = {
        "check_id": "generation.import_smoke",
        "subject_key": "onto:obligation-a",
        "stage": "runtime",
        "status": "fail",
        "observed_artifacts": ["scripts/onto/main.py"],
        "blocked_by": [],
        "evidence": {"failures": ["cannot import main"]},
        "message": "cannot import main",
    }
    captured: dict[str, str] = {}

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        captured["prompt"] = prompt
        return LLMJsonResult(
            data={
                "status": "selected",
                "focus_id": "import",
                "observation_ids": ["generation.import_smoke::onto:obligation-a"],
                "dependency_ids": [],
                "objective": "restore import",
                "selection_reason": "upstream blocker",
                "completion_evidence": ["import smoke passes"],
                "repair_skill_ids": ["syntax-import-recovery", "small-unified-diff"],
                "max_target_files": 1,
            },
            elapsed_seconds=0.0,
            token_usage={},
        )

    monkeypatch.setattr(pure_llm_generation, "invoke_json", fake_invoke)
    focus = pure_llm_generation._request_repair_focus(
        model_name="test",
        context=context,
        report={"ok": False, "observations": [observation]},
        active_focus=None,
        previous_steps=[],
        max_target_files=1,
    )

    assert focus["repair_skill_ids"] == [
        "syntax-import-recovery",
        "small-unified-diff",
    ]
    assert '"skill_id": "mcp-tool-discoverability"' in captured["prompt"]


def test_repair_focus_discards_selected_failure_as_own_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    observation = {
        "check_id": "generation.prompt_runtime_binding",
        "subject_key": "onto/EXTRACTION_ITER_3.md",
        "stage": "prompt",
        "status": "fail",
        "observed_artifacts": ["prompts/onto/EXTRACTION_ITER_3.md"],
        "blocked_by": [],
        "evidence": {},
        "message": "missing runtime binding",
    }
    observation_id = (
        "generation.prompt_runtime_binding::onto/EXTRACTION_ITER_3.md"
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *_args, **_kwargs: LLMJsonResult(
            data={
                "status": "selected",
                "focus_id": "repair-iter3-binding",
                "observation_ids": [observation_id],
                "dependency_ids": [observation_id],
                "objective": "restore the binding",
                "repair_skill_ids": [],
                "max_target_files": 1,
            },
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    focus = pure_llm_generation._request_repair_focus(
        model_name="test",
        context=context,
        report={"ok": False, "observations": [observation]},
        active_focus=None,
        previous_steps=[],
        max_target_files=1,
    )

    assert focus["observation_ids"] == [observation_id]
    assert focus["dependency_ids"] == []


def test_repair_focus_rejects_unknown_golden_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    observation = {
        "check_id": "generation.import_smoke",
        "subject_key": "onto:obligation-a",
        "stage": "runtime",
        "status": "fail",
        "observed_artifacts": ["scripts/onto/main.py"],
        "blocked_by": [],
        "evidence": {},
        "message": "cannot import main",
    }
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *_args, **_kwargs: LLMJsonResult(
            data={
                "status": "selected",
                "focus_id": "import",
                "observation_ids": ["generation.import_smoke::onto:obligation-a"],
                "dependency_ids": [],
                "objective": "restore import",
                "repair_skill_ids": ["domain-specific-magic"],
                "max_target_files": 1,
            },
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    with pytest.raises(ValueError, match="unknown skills"):
        pure_llm_generation._request_repair_focus(
            model_name="test",
            context=context,
            report={"ok": False, "observations": [observation]},
            active_focus=None,
            previous_steps=[],
            max_target_files=1,
        )


def test_inspection_can_read_noneditable_package_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    support = Path(context.scripts_dir) / "_fixed_om2_runtime.py"
    support.write_text("FIXED = True\n", encoding="utf-8")
    targets = pure_llm_generation._editable_artifacts(
        context, generate_scripts=True, generate_prompts=True
    )
    responses = iter(
        [
            LLMJsonResult(
                data={
                    "status": "inspect",
                    "inspection_question": "verify fixed runtime",
                    "inspect_paths": ["scripts/onto/_fixed_om2_runtime.py"],
                    "hypotheses": [],
                    "why_these_files": "import evidence",
                },
                elapsed_seconds=0.0,
                token_usage={},
            ),
            LLMJsonResult(
                data={
                    "status": "diagnosed",
                    "causal_findings": [
                        {
                            "observation_ids": [],
                            "source_path": "scripts/onto/_fixed_om2_runtime.py",
                            "symbols_or_sections": ["FIXED"],
                            "cause": "support contract",
                            "evidence": "FIXED = True",
                            "downstream_impact": [],
                        }
                    ],
                    "unresolved_questions": [],
                    "confidence": "high",
                },
                elapsed_seconds=0.0,
                token_usage={},
            ),
        ]
    )
    monkeypatch.setattr(
        pure_llm_generation, "invoke_json", lambda *args, **kwargs: next(responses)
    )

    scope = pure_llm_generation._request_inspection_scope(
        model_name="test",
        context=context,
        targets=targets,
        report={"ok": False, "failures": ["failure"]},
        step_index=1,
        previous_steps=[],
    )
    diagnosis = pure_llm_generation._request_causal_diagnosis(
        model_name="test",
        context=context,
        targets=targets,
        report={"ok": False, "failures": ["failure"]},
        inspection_scope=scope,
        previous_steps=[],
    )

    assert diagnosis["causal_findings"][0]["source_path"].endswith(
        "_fixed_om2_runtime.py"
    )
    assert support.resolve() not in {path.resolve() for path in targets}


def test_causal_plan_rejects_targets_outside_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    targets = pure_llm_generation._editable_artifacts(
        context, generate_scripts=True, generate_prompts=True
    )
    invalid = {
        "status": "actionable",
        "objective": "repair script",
        "targets": ["../outside.py"],
        "causal_findings": [],
        "dependency_order": [],
        "must_preserve": [],
        "acceptance_focus": [],
    }
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *args, **kwargs: LLMJsonResult(
            data=invalid,
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    with pytest.raises(ValueError, match="outside inventory"):
        pure_llm_generation._request_impact_plan(
            model_name="test",
            context=context,
            targets=targets,
            report={"ok": False, "failures": ["failure"]},
            inspection_scope={
                "status": "inspect",
                "inspect_paths": ["scripts/onto/main.py"],
            },
            diagnosis={"status": "diagnosed", "causal_findings": []},
            previous_steps=[],
        )


def test_impact_plan_requires_declared_coedits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    targets = pure_llm_generation._editable_artifacts(
        context, generate_scripts=True, generate_prompts=True
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *args, **kwargs: LLMJsonResult(
            data={
                "status": "actionable",
                "objective": "repair adapter",
                "targets": ["scripts/onto/main.py"],
                "required_coedits": ["prompts/onto/ITER.md"],
                "read_only_dependencies": [],
                "dependency_order": ["scripts/onto/main.py"],
                "impact_plan": [],
                "must_preserve": [],
                "acceptance_focus": [],
            },
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    with pytest.raises(ValueError, match="omitted required co-edits"):
        pure_llm_generation._request_impact_plan(
            model_name="test",
            context=context,
            targets=targets,
            report={"ok": False, "failures": ["failure"]},
            inspection_scope={
                "status": "inspect",
                "inspect_paths": ["scripts/onto/main.py"],
            },
            diagnosis={"status": "diagnosed", "causal_findings": []},
            previous_steps=[],
            focus={"max_target_files": 3},
        )


def test_impact_plan_normalizes_path_annotated_required_coedits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    entity_path = "scripts/onto/onto_creation_entities.py"
    targets = [Path(context.output_root) / entity_path]
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *args, **kwargs: LLMJsonResult(
            data={
                "status": "actionable",
                "objective": "add missing creator",
                "targets": [entity_path],
                "required_coedits": [
                    f"{entity_path}::Add the missing module-scope creator.",
                    f"{entity_path}::Register the creator in __all__.",
                ],
                "read_only_dependencies": [],
                "dependency_order": [
                    f"{entity_path}::Patch creator before dependents."
                ],
                "impact_plan": [],
                "must_preserve": [],
                "acceptance_focus": [],
            },
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    plan = pure_llm_generation._request_impact_plan(
        model_name="test",
        context=context,
        targets=targets,
        report={"ok": False, "failures": ["missing creator"]},
        inspection_scope={
            "status": "inspect",
            "inspect_paths": [entity_path],
        },
        diagnosis={"status": "diagnosed", "causal_findings": []},
        previous_steps=[],
        focus={"max_target_files": 3},
    )

    assert plan["required_coedits"] == [entity_path]
    assert plan["dependency_order"] == [entity_path]


def test_causal_plan_can_change_strategy_for_same_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    targets = pure_llm_generation._editable_artifacts(
        context, generate_scripts=True, generate_prompts=True
    )
    revised = {
        "status": "actionable",
        "objective": "Repair imported registration symbol instead of adding duplicate wrappers",
        "targets": ["scripts/onto/main.py"],
        "causal_findings": [
            {
                "failure_ids": ["missing tool"],
                "source_path": "scripts/onto/main.py",
                "symbols_or_sections": ["register_tools"],
                "cause": "wrong imported registry",
                "evidence": "registration calls local empty registry",
                "downstream_impact": [],
            }
        ],
        "dependency_order": ["scripts/onto/main.py"],
        "must_preserve": [],
        "acceptance_focus": [],
        "alternative_to_rejected_strategies": "Fix causative import, not duplicate wrappers",
    }
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *args, **kwargs: LLMJsonResult(
            data=revised,
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    plan = pure_llm_generation._request_impact_plan(
        model_name="test",
        context=context,
        targets=targets,
        report={"ok": False, "failures": ["failure"]},
        inspection_scope={
            "status": "inspect",
            "inspect_paths": ["scripts/onto/main.py"],
        },
        diagnosis={"status": "diagnosed", "causal_findings": revised["causal_findings"]},
        previous_steps=[
            {
                "accepted": False,
                "plan": {
                    "objective": "Add three wrappers",
                    "targets": ["scripts/onto/main.py"],
                },
            }
        ],
    )

    assert plan["objective"] == revised["objective"]


def test_step_history_projection_excludes_raw_patch_and_validation() -> None:
    projected = pure_llm_generation._project_step_history(
        [
            {
                "step_index": 1,
                "accepted": False,
                "before_failure_count": 10,
                "after_failure_count": 10,
                "inspection_scope": {
                    "inspection_question": "where",
                    "inspect_paths": ["scripts/onto/main.py"],
                },
                "diagnosis": {
                    "status": "diagnosed",
                    "causal_findings": [{"cause": "registry"}],
                },
                "plan": {
                    "status": "actionable",
                    "objective": "repair registry",
                    "targets": ["scripts/onto/main.py"],
                },
                "patch": {
                    "patch_unified_diff": "very large raw patch",
                    "attempts": [{"validation": {"failures": ["huge"]}}],
                },
                "validation": {"failures": ["huge repeated report"]},
                "delta_review": {"decision": "reject", "reason": "regression"},
            }
        ]
    )

    text = str(projected)
    assert "repair registry" in text
    assert "very large raw patch" not in text
    assert "huge repeated report" not in text


def test_observation_transition_accepts_focus_progress_and_unmasked_downstream() -> None:
    before = {
        "observations": [
            {"check_id": "foundation.api", "status": "fail"},
            {"check_id": "runtime.graph", "status": "blocked"},
            {"check_id": "syntax.base", "status": "pass"},
        ]
    }
    after = {
        "observations": [
            {"check_id": "foundation.api", "status": "pass"},
            {"check_id": "runtime.graph", "status": "fail"},
            {"check_id": "syntax.base", "status": "pass"},
        ]
    }

    delta = pure_llm_generation._observation_transition_report(
        before_report=before,
        after_report=after,
        focus_observation_ids=["foundation.api"],
    )

    assert delta["focus_progress"]
    assert not delta["protected_regression"]
    assert delta["resolved_observation_ids"] == ["foundation.api"]
    assert delta["newly_unmasked_observation_ids"] == ["runtime.graph"]


def test_observation_transition_rejects_previously_passing_regression() -> None:
    before = {
        "observations": [
            {"check_id": "foundation.api", "status": "fail"},
            {"check_id": "syntax.base", "status": "pass"},
        ]
    }
    after = {
        "observations": [
            {"check_id": "foundation.api", "status": "pass"},
            {"check_id": "syntax.base", "status": "fail"},
        ]
    }

    delta = pure_llm_generation._observation_transition_report(
        before_report=before,
        after_report=after,
        focus_observation_ids=["foundation.api"],
    )

    assert delta["focus_progress"]
    assert delta["protected_regression"]
    assert delta["regression_observation_ids"] == ["syntax.base"]


def test_observation_transition_resolves_disappearing_focused_failure() -> None:
    before = {
        "observations": [
            {"check_id": "external.semantic", "status": "fail"},
            {"check_id": "syntax.base", "status": "pass"},
        ]
    }
    after = {
        "observations": [
            {"check_id": "syntax.base", "status": "pass"},
        ]
    }

    delta = pure_llm_generation._observation_transition_report(
        before_report=before,
        after_report=after,
        focus_observation_ids=["external.semantic"],
    )

    assert delta["focus_progress"]
    assert not delta["protected_regression"]
    assert delta["resolved_observation_ids"] == ["external.semantic"]
    assert delta["missing_observation_ids"] == ["external.semantic"]


def test_all_green_stage_and_semantic_reviews_override_delta_bookkeeping() -> None:
    assert pure_llm_generation._stage_and_semantic_reviews_pass(
        candidate_report={"stage_ok": True},
        semantic_validation={"decision": "pass", "critical_errors": []},
        semantic_review_required=True,
    )


def test_all_green_gate_requires_each_configured_review() -> None:
    assert not pure_llm_generation._stage_and_semantic_reviews_pass(
        candidate_report={"stage_ok": False},
        semantic_validation={"decision": "pass", "critical_errors": []},
        semantic_review_required=True,
    )
    assert not pure_llm_generation._stage_and_semantic_reviews_pass(
        candidate_report={"stage_ok": True},
        semantic_validation={"decision": "repair", "critical_errors": ["defect"]},
        semantic_review_required=True,
    )


def test_observation_transition_rejects_missing_or_new_unblocked_failure() -> None:
    before = {
        "observations": [
            {"check_id": "foundation.api", "status": "fail"},
            {"check_id": "syntax.base", "status": "pass"},
        ]
    }
    after = {
        "observations": [
            {"check_id": "foundation.api", "status": "pass"},
            {"check_id": "runtime.new", "status": "fail"},
        ]
    }

    delta = pure_llm_generation._observation_transition_report(
        before_report=before,
        after_report=after,
        focus_observation_ids=["foundation.api"],
    )

    assert delta["protected_regression"]
    assert delta["missing_observation_ids"] == ["syntax.base"]
    assert delta["newly_failed_observation_ids"] == ["runtime.new"]


def test_focus_selector_rejects_unknown_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        pure_llm_generation,
        "invoke_json",
        lambda *args, **kwargs: LLMJsonResult(
            data={
                "status": "selected",
                "focus_id": "invalid",
                "observation_ids": ["unknown"],
                "dependency_ids": [],
                "objective": "escape facts",
                "selection_reason": "",
                "completion_evidence": [],
                "max_target_files": 1,
            },
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    with pytest.raises(ValueError, match="non-failing observations"):
        pure_llm_generation._request_repair_focus(
            model_name="test",
            context=context,
            report={
                "observations": [
                    {"check_id": "known", "status": "fail"},
                ]
            },
            active_focus=None,
            previous_steps=[],
            max_target_files=3,
        )


def test_fixed_artifact_dependency_order_preserves_complete_inventory(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    targets = pure_llm_generation._editable_artifacts(
        context, generate_scripts=True, generate_prompts=True
    )
    ordered = pure_llm_generation._fixed_artifact_dependency_order(
        root=Path(context.output_root),
        targets=targets,
    )

    expected = {
        path.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
        for path in targets
    }
    assert len(ordered) == len(targets)
    assert set(ordered) == expected


def test_stage_focused_repair_commits_strict_partial_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    target = Path(context.scripts_dir) / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    before_report = {
        "ok": False,
        "stage_ok": False,
        "failures": ["signature_a", "signature_b", "signature_c", "signature_d"],
        "observations": [],
    }
    after_report = {
        "ok": False,
        "stage_ok": False,
        "failures": ["signature_d"],
        "observations": [],
    }

    monkeypatch.setattr(
        pure_llm_generation,
        "_request_repair_focus",
        lambda **kwargs: {
            "status": "selected",
            "focus_id": "creation-check-signatures",
            "objective": "repair fixed runtime signatures",
            "observation_ids": [],
        },
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_inspection_scope",
        lambda **kwargs: {
            "status": "inspect",
            "inspect_paths": ["scripts/onto/main.py"],
        },
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_causal_diagnosis",
        lambda **kwargs: {"status": "diagnosed", "causal_findings": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_impact_plan",
        lambda **kwargs: {
            "status": "actionable",
            "objective": "repair fixed runtime signatures",
            "targets": ["scripts/onto/main.py"],
        },
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: after_report,
    )

    def fail_if_delta_reviewed(**kwargs):
        pytest.fail("strict partial progress must not depend on an LLM delta review")

    monkeypatch.setattr(
        pure_llm_generation, "_request_delta_review", fail_if_delta_reviewed
    )

    def fake_editor(*, validate, **kwargs):
        target.write_text("VALUE = 2\n", encoding="utf-8")
        validation = validate()
        assert validation["ok"]
        assert validation["delta_review"]["skipped"]
        return {"ok": True, "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    report, result = pure_llm_generation._run_stage_focused_repair(
        model_name="test",
        context=context,
        targets=[target],
        report=before_report,
        foreign_contracts=None,
        active_artifacts=["scripts/onto/main.py"],
        max_focus_targets=1,
        edit_backend="exact_edits",
    )

    assert result["accepted"]
    assert report["failures"] == ["signature_d"]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_staged_repair_replans_after_each_accepted_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    (Path(context.scripts_dir) / "main.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (Path(context.prompts_dir) / "ITER.md").write_text(
        "Existing prompt\n", encoding="utf-8"
    )
    reports = [
        {"ok": False, "failures": ["script_failure", "prompt_failure"]},
        {"ok": True, "failures": []},
        {"ok": False, "failures": ["prompt_failure"]},
        {"ok": True, "failures": []},
        {"ok": True, "failures": []},
    ]
    plans = [
        {
            "status": "actionable",
            "objective": "repair script",
            "targets": ["scripts/onto/main.py"],
        },
        {
            "status": "actionable",
            "objective": "repair prompt",
            "targets": ["prompts/onto/ITER.md"],
        },
    ]
    scopes = [
        {"status": "inspect", "inspect_paths": ["scripts/onto/main.py"]},
        {"status": "inspect", "inspect_paths": ["prompts/onto/ITER.md"]},
    ]
    plan_reports: list[list[str]] = []
    patch_targets: list[list[str]] = []

    def fake_validation(*args, **kwargs):
        return reports.pop(0)

    def fake_plan(**kwargs):
        plan_reports.append(list(kwargs["report"]["failures"]))
        return plans.pop(0)

    def fake_editor(*, targets, validate=None, **kwargs):
        patch_targets.append([path.name for path in targets])
        if validate is None:
            return {"ok": True}
        validation = validate()
        return {"ok": validation["ok"], "validation": validation}

    monkeypatch.setattr(pure_llm_generation, "build_validation_report", fake_validation)
    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_inspection_scope",
        lambda **kwargs: scopes.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_causal_diagnosis",
        lambda **kwargs: {"status": "diagnosed", "causal_findings": []},
    )
    monkeypatch.setattr(pure_llm_generation, "_request_impact_plan", fake_plan)
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_delta_review",
        lambda **kwargs: {"decision": "accept", "reason": "improved"},
    )
    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        model_name="test",
        max_rounds=2,
        generate_scripts=True,
        generate_prompts=True,
        repair_only=True,
    )

    assert result["ok"]
    assert plan_reports == [
        ["script_failure", "prompt_failure"],
        ["prompt_failure"],
    ]
    assert patch_targets[-2:] == [["main.py"], ["ITER.md"]]
    assert [step["accepted"] for step in result["repair_steps"]] == [True, True]


def test_rejected_step_rolls_back_then_replans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    (Path(context.scripts_dir) / "main.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    reports = [
        {"ok": False, "failures": ["remaining"]},
        {"ok": False, "failures": ["remaining"]},
        {"ok": True, "failures": []},
        {"ok": True, "failures": []},
    ]
    plans = [
        {
            "status": "actionable",
            "objective": "bad step",
            "targets": ["scripts/onto/main.py"],
        },
        {
            "status": "actionable",
            "objective": "good step",
            "targets": ["scripts/onto/main.py"],
        },
    ]
    scopes = [
        {"status": "inspect", "inspect_paths": ["scripts/onto/main.py"]},
        {"status": "inspect", "inspect_paths": ["scripts/onto/main.py"]},
    ]
    editor_results = [False, True]

    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: reports.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_inspection_scope",
        lambda **kwargs: scopes.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_causal_diagnosis",
        lambda **kwargs: {"status": "diagnosed", "causal_findings": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_impact_plan",
        lambda **kwargs: plans.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_delta_review",
        lambda **kwargs: {"decision": "accept", "reason": "improved"},
    )

    def fake_editor(*, validate=None, **kwargs):
        if validate is None:
            return {"ok": True}
        ok = editor_results.pop(0)
        if ok:
            validation = validate()
            return {"ok": validation["ok"], "validation": validation}
        return {"ok": False, "failures": ["rejected"]}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        model_name="test",
        max_rounds=2,
        generate_scripts=True,
        generate_prompts=False,
        repair_only=True,
    )

    assert result["ok"]
    assert [step["accepted"] for step in result["repair_steps"]] == [False, True]


def test_completed_inspection_reuses_diagnosis_and_continues_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    (context.scripts_dir / Path("main.py")).write_text("VALUE = 1\n", encoding="utf-8")
    reports = [
        {"ok": False, "failures": ["remaining"]},
        {"ok": False, "failures": ["remaining"]},
        {"ok": True, "failures": []},
        {"ok": True, "failures": []},
    ]
    diagnosis = {"status": "diagnosed", "causal_findings": [{"cause": "known"}]}
    scopes = [
        {"status": "inspect", "inspect_paths": ["scripts/onto/main.py"]},
        {"status": "complete", "inspect_paths": []},
    ]
    plans = [
        {
            "status": "actionable",
            "objective": "first rejected step",
            "targets": ["scripts/onto/main.py"],
        },
        {
            "status": "actionable",
            "objective": "reuse evidence",
            "targets": ["scripts/onto/main.py"],
        },
    ]
    diagnoses: list[dict] = []
    editor_results = [False, True]

    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: reports.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_inspection_scope",
        lambda **kwargs: scopes.pop(0),
    )

    def fake_diagnosis(**kwargs):
        diagnoses.append(kwargs)
        return diagnosis

    monkeypatch.setattr(
        pure_llm_generation, "_request_causal_diagnosis", fake_diagnosis
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_impact_plan",
        lambda **kwargs: plans.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_delta_review",
        lambda **kwargs: {"decision": "accept", "reason": "fixed"},
    )

    def fake_editor(*, validate=None, **kwargs):
        ok = editor_results.pop(0)
        if not ok:
            return {"ok": False, "failures": ["rejected"]}
        validation = validate()
        return {"ok": validation["ok"], "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        model_name="test",
        max_rounds=2,
        generate_scripts=True,
        generate_prompts=False,
        repair_only=True,
    )

    assert result["ok"]
    assert len(diagnoses) == 1
    assert len(result["repair_steps"]) == 2
