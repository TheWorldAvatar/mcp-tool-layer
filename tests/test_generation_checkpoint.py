"""Tests for generation checkpoint replay and repair-only orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation import (
    pure_llm_generation,
    semantic_script_review,
)
from src.agents.scripts_and_prompts_generation.artifact_state import (
    ArtifactStateStore,
)
from src.agents.scripts_and_prompts_generation.generation_checkpoint import (
    copy_generation_checkpoint,
    replay_generation_checkpoint,
)


def test_replay_strips_legacy_apply_patch_sentinel(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "llm_agent_run": {
                            "history": [
                                {
                                    "mode": "per_file_initial_generation",
                                    "files": [
                                        {
                                            "target": "scripts/onto/main.py",
                                            "patch": {
                                                "ok": True,
                                                "changed_files": [
                                                    "scripts/onto/main.py"
                                                ],
                                                "patch_unified_diff": (
                                                    "--- a/scripts/onto/main.py\n"
                                                    "+++ b/scripts/onto/main.py\n"
                                                    "@@ -0,0 +1 @@\n"
                                                    "+VALUE = 1\n"
                                                    "*** End Patch"
                                                ),
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = replay_generation_checkpoint(
        summary_path=summary_path,
        output_root=tmp_path / "replayed",
    )

    assert report["ok"]
    assert (
        tmp_path / "replayed" / "scripts" / "onto" / "main.py"
    ).read_text(encoding="utf-8") == "VALUE = 1\n"


def test_replay_generation_checkpoint_applies_audited_initial_diff(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "source-summary.json"
    output_root = tmp_path / "checkpoint"
    summary_path.write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "llm_agent_run": {
                            "history": [
                                {
                                    "mode": "per_file_initial_generation",
                                    "files": [
                                        {
                                            "target": "old/main.py",
                                            "patch": {
                                                "ok": True,
                                                "changed_files": ["scripts/onto/main.py"],
                                                "patch_unified_diff": (
                                                    "--- a/scripts/onto/main.py\n"
                                                    "+++ b/scripts/onto/main.py\n"
                                                    "@@ -0,0 +1 @@\n"
                                                    "+VALUE = 1\n"
                                                ),
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = replay_generation_checkpoint(
        summary_path=summary_path,
        output_root=output_root,
    )

    assert report["ok"]
    assert (output_root / "scripts" / "onto" / "main.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"


def test_replay_generation_checkpoint_applies_package_synthesis_in_order(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "source-summary.json"
    output_root = tmp_path / "checkpoint"
    summary_path.write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "llm_agent_run": {
                            "history": [
                                {
                                    "mode": "per_file_initial_generation",
                                    "files": [
                                        {
                                            "patch": {
                                                "ok": True,
                                                "changed_files": ["scripts/onto/main.py"],
                                                "patch_unified_diff": (
                                                    "--- a/scripts/onto/main.py\n"
                                                    "+++ b/scripts/onto/main.py\n"
                                                    "@@ -0,0 +1 @@\n"
                                                    "+VALUE = 1\n"
                                                ),
                                            }
                                        },
                                        {
                                            "patch": {
                                                "ok": True,
                                                "changed_files": ["scripts/onto/tools.py"],
                                                "patch_unified_diff": (
                                                    "--- a/scripts/onto/tools.py\n"
                                                    "+++ b/scripts/onto/tools.py\n"
                                                    "@@ -0,0 +1 @@\n"
                                                    "+TOOLS = []\n"
                                                ),
                                            }
                                        },
                                    ],
                                },
                                {
                                    "mode": "package_synthesis",
                                    "patch": {
                                        "ok": True,
                                        "changed_files": [
                                            "scripts/onto/main.py",
                                            "scripts/onto/tools.py",
                                        ],
                                        "patch_unified_diff": (
                                            "diff --git a/scripts/onto/main.py b/scripts/onto/main.py\n"
                                            "--- a/scripts/onto/main.py\n"
                                            "+++ b/scripts/onto/main.py\n"
                                            "@@ -1 +1 @@\n"
                                            "-VALUE = 1\n"
                                            "+VALUE = 2\n"
                                            "diff --git a/scripts/onto/tools.py b/scripts/onto/tools.py\n"
                                            "--- a/scripts/onto/tools.py\n"
                                            "+++ b/scripts/onto/tools.py\n"
                                            "@@ -1 +1 @@\n"
                                            "-TOOLS = []\n"
                                            "+TOOLS = ['ready']\n"
                                        ),
                                    },
                                },
                            ]
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = replay_generation_checkpoint(
        summary_path=summary_path,
        output_root=output_root,
    )

    assert report["ok"]
    assert (output_root / "scripts" / "onto" / "main.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"
    assert (output_root / "scripts" / "onto" / "tools.py").read_text(
        encoding="utf-8"
    ) == "TOOLS = ['ready']\n"


def test_replay_can_exclude_legacy_package_synthesis(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "llm_agent_run": {
                            "history": [
                                {
                                    "mode": "per_file_initial_generation",
                                    "files": [
                                        {
                                            "patch": {
                                                "ok": True,
                                                "changed_files": ["scripts/onto/main.py"],
                                                "patch_unified_diff": (
                                                    "--- a/scripts/onto/main.py\n"
                                                    "+++ b/scripts/onto/main.py\n"
                                                    "@@ -0,0 +1 @@\n+VALUE = 1\n"
                                                ),
                                            }
                                        }
                                    ],
                                },
                                {
                                    "mode": "package_synthesis",
                                    "patch": {
                                        "ok": True,
                                        "changed_files": ["scripts/onto/main.py"],
                                        "patch_unified_diff": (
                                            "--- a/scripts/onto/main.py\n"
                                            "+++ b/scripts/onto/main.py\n"
                                            "@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
                                        ),
                                    },
                                },
                            ]
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    replay_generation_checkpoint(
        summary_path=summary_path,
        output_root=tmp_path / "replayed",
        include_package_synthesis=False,
    )

    assert (
        tmp_path / "replayed" / "scripts" / "onto" / "main.py"
    ).read_text(encoding="utf-8") == "VALUE = 1\n"


def test_replay_applies_accepted_per_artifact_stage_repair(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "llm_agent_run": {
                            "history": [
                                {
                                    "mode": "per_file_initial_generation",
                                    "files": [
                                        {
                                            "patch": {
                                                "ok": True,
                                                "changed_files": ["scripts/onto/main.py"],
                                                "patch_unified_diff": (
                                                    "--- a/scripts/onto/main.py\n"
                                                    "+++ b/scripts/onto/main.py\n"
                                                    "@@ -0,0 +1 @@\n+VALUE = 1\n"
                                                ),
                                            },
                                            "stage_repair": {
                                                "accepted": True,
                                                "patch": {
                                                    "ok": True,
                                                    "changed_files": [
                                                        "scripts/onto/main.py"
                                                    ],
                                                    "patch_unified_diff": (
                                                        "--- a/scripts/onto/main.py\n"
                                                        "+++ b/scripts/onto/main.py\n"
                                                        "@@ -1 +1 @@\n"
                                                        "-VALUE = 1\n+VALUE = 2\n"
                                                    ),
                                                },
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    replay_generation_checkpoint(
        summary_path=summary_path,
        output_root=tmp_path / "replayed",
        include_package_synthesis=False,
    )

    assert (
        tmp_path / "replayed" / "scripts" / "onto" / "main.py"
    ).read_text(encoding="utf-8") == "VALUE = 2\n"


def test_copy_generation_checkpoint_is_fresh_and_source_immutable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    script = source / "scripts" / "onto" / "main.py"
    prompt = source / "prompts" / "onto" / "ITER.md"
    script.parent.mkdir(parents=True)
    prompt.parent.mkdir(parents=True)
    script.write_text("VALUE = 1\n", encoding="utf-8")
    prompt.write_text("rule\n", encoding="utf-8")
    before = {script: script.read_bytes(), prompt: prompt.read_bytes()}

    report = copy_generation_checkpoint(
        checkpoint_root=source,
        output_root=tmp_path / "working",
        ontology_name="onto",
    )

    assert report["ok"]
    assert all(path.read_bytes() == content for path, content in before.items())
    manifest = json.loads(
        (tmp_path / "working" / "reports" / "checkpoint_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["artifact_inventory"] == [
        "prompts/onto/ITER.md",
        "scripts/onto/main.py",
    ]
    with pytest.raises(ValueError, match="destination"):
        copy_generation_checkpoint(
            checkpoint_root=source,
            output_root=tmp_path / "working",
            ontology_name="onto",
        )


def test_repair_only_never_calls_initial_generation_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts" / "onto"
    scripts.mkdir(parents=True)
    (scripts / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(tmp_path / "prompts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "run_llm_unified_diff_editor",
        lambda *args, **kwargs: pytest.fail("generation editor must not run"),
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        generate_scripts=True,
        generate_prompts=False,
        repair_only=True,
    )

    assert result["ok"], result
    assert result["mode"] == "pure_llm_repair_only"
    assert result["history"][0]["mode"] == "repair_checkpoint"
    state = json.loads(
        (
            tmp_path / "reports" / "onto" / "artifact_states.json"
        ).read_text(encoding="utf-8")
    )
    assert state["artifacts"]["scripts/onto/main.py"]["status"] == "passed"


def test_generation_resume_skips_hash_matching_passed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts" / "onto"
    scripts.mkdir(parents=True)
    target = scripts / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(tmp_path / "prompts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()
    state = ArtifactStateStore(tmp_path, "onto")
    state.initialize([target])
    state.transition(target, "passed")
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: {"ok": True, "stage_ok": True, "failures": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "run_llm_unified_diff_editor",
        lambda *args, **kwargs: pytest.fail("passed artifact must be skipped"),
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        generate_scripts=True,
        generate_prompts=False,
    )

    assert result["ok"]
    assert result["mode"] == "pure_llm_checkpoint_resume"
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_repair_only_preserves_accepted_partial_foundation_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts" / "onto"
    scripts.mkdir(parents=True)
    target = scripts / "onto_creation_base.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(tmp_path / "prompts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()
    reports = [
        {"ok": False, "failures": ["foundation_gap", "unrelated_gap"]},
        {"ok": False, "failures": ["unrelated_gap"]},
    ]
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: reports.pop(0),
    )

    def fake_editor(**kwargs):
        target.write_text("VALUE = 2\n", encoding="utf-8")
        validation = kwargs["validate"]()
        assert validation["ok"]
        return {
            "ok": True,
            "failures": [],
            "changed_files": ["scripts/onto/onto_creation_base.py"],
            "validation": validation,
        }

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        max_rounds=0,
        generate_scripts=True,
        generate_prompts=False,
        repair_only=True,
        creation_foundation_synthesis=True,
        creation_foundation_module=target.name,
    )

    assert not result["ok"]
    assert result["checkpoint_preserved"]
    assert "rolled_back" not in result
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert result["final_report"]["failures"] == ["unrelated_gap"]


def test_partial_validation_improvement_rejects_new_failures() -> None:
    assert pure_llm_generation._is_strict_validation_improvement(
        {"a", "b"}, {"b"}
    )
    assert not pure_llm_generation._is_strict_validation_improvement(
        {"a", "b", "c"}, {"b", "new"}
    )


def test_foundation_delta_reviewer_can_accept_unmasked_downstream_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts" / "onto"
    scripts.mkdir(parents=True)
    target = scripts / "onto_creation_base.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(tmp_path / "prompts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()
    reports = [
        {"ok": False, "failures": ["foundation_gap", "masked_runtime_gap"]},
        {"ok": False, "failures": ["unmasked_main_gap"]},
    ]
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: reports.pop(0),
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_delta_review",
        lambda **kwargs: {
            "decision": "accept",
            "reason": "foundation fixed; downstream validation became reachable",
            "regressions": [],
        },
    )

    def fake_editor(**kwargs):
        target.write_text("VALUE = 2\n", encoding="utf-8")
        validation = kwargs["validate"]()
        assert validation["ok"]
        assert validation["delta_review"]["decision"] == "accept"
        return {
            "ok": True,
            "failures": [],
            "changed_files": ["scripts/onto/onto_creation_base.py"],
            "validation": validation,
        }

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        max_rounds=0,
        generate_scripts=True,
        generate_prompts=False,
        repair_only=True,
        creation_foundation_synthesis=True,
        creation_foundation_module=target.name,
    )

    assert not result["ok"]
    assert result["checkpoint_preserved"]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert result["final_report"]["failures"] == ["unmasked_main_gap"]


def test_generation_only_preserves_failed_validation_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts" / "onto"
    scripts.mkdir(parents=True)
    target = scripts / "main.py"
    target.write_text("VALUE = 0\n", encoding="utf-8")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(tmp_path / "prompts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()
    reports = [
        {"ok": False, "failures": ["empty"]},
        {"ok": False, "failures": ["semantic_gap"]},
    ]
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: reports.pop(0),
    )

    def fake_editor(**kwargs):
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"ok": True, "failures": [], "changed_files": ["scripts/onto/main.py"]}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        generate_scripts=True,
        generate_prompts=False,
        generation_only=True,
    )

    assert not result["ok"]
    assert result["generation_complete"]
    assert result["checkpoint_preserved"]
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_package_synthesis_coordinates_generated_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts" / "onto"
    scripts.mkdir(parents=True)
    target = scripts / "main.py"
    target.write_text("VALUE = 0\n", encoding="utf-8")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(tmp_path / "prompts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()
    reports = [
        {
            "ok": False,
            "failures": ["uncoordinated"],
            "observations": [
                {
                    "check_id": "integration",
                    "subject_key": "onto",
                    "status": "fail",
                }
            ],
        },
        {
            "ok": True,
            "failures": [],
            "observations": [
                {
                    "check_id": "integration",
                    "subject_key": "onto",
                    "status": "pass",
                }
            ],
        },
    ]
    calls: list[int] = []
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: reports.pop(0),
    )

    def fake_editor(*, targets, validate=None, **kwargs):
        calls.append(len(targets))
        target.write_text(f"VALUE = {len(calls)}\n", encoding="utf-8")
        if validate is None:
            return {"ok": True}
        validation = validate()
        return {"ok": validation["ok"], "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_repair_focus",
        lambda **kwargs: {
            "status": "selected",
            "focus_id": "integration",
            "observation_ids": ["integration::onto"],
            "max_target_files": 1,
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
            "objective": "coordinate package",
            "targets": ["scripts/onto/main.py"],
        },
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "_request_delta_review",
        lambda **kwargs: {"decision": "accept", "reason": "resolved"},
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        generate_scripts=True,
        generate_prompts=False,
        repair_only=True,
        max_rounds=1,
        package_synthesis=True,
    )

    assert result["ok"], result
    assert calls == [1]
    assert result["history"][-1]["mode"] == "focused_package_integration"
    assert result["deprecated_alias_used"]


def _paired_repair_context(tmp_path: Path):
    prompts = tmp_path / "prompts" / "onto"
    prompts.mkdir(parents=True)
    return type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "prompts_dir": str(prompts),
            "scripts_dir": str(tmp_path / "scripts" / "onto"),
            "contract": {"ontology_name": "onto"},
            "ontology": type(
                "Ontology",
                (),
                {"name": "onto", "role": "main", "ttl_file": "onto.ttl"},
            )(),
        },
    )()


def _paired_repair_review(*targets: str) -> dict:
    return {
        "decision": "repair",
        "summary": "paired materialization is incomplete",
        "critical_errors": [
            {
                "finding": "The KG prompt drops extracted identity evidence.",
                "iteration": "3",
                "evidence": ["exact critical evidence"],
                "expected_behavior": (
                    "The KG prompt must preserve extracted identity evidence."
                ),
                "contract_evidence": ["identity evidence is creator input"],
                "repair_targets": list(targets),
            }
        ],
        "noncritical_observations": [],
        "confidence": 1.0,
    }


def test_paired_repair_routes_only_review_targets_in_dependency_order(
    tmp_path: Path,
) -> None:
    context = _paired_repair_context(tmp_path)
    prompts = Path(context.prompts_dir)
    extraction = prompts / "EXTRACTION_ITER_3.md"
    kg2 = prompts / "KG_BUILDING_ITER_2.md"
    kg3 = prompts / "KG_BUILDING_ITER_3.md"
    for path in (extraction, kg2, kg3):
        path.write_text("prompt\n", encoding="utf-8")

    routed, failures = pure_llm_generation._paired_prompt_review_targets(
        context=context,
        review=_paired_repair_review(kg3.name, extraction.name),
        editable_targets=[extraction, kg2, kg3],
    )

    assert not failures
    assert [path.name for path in routed] == [extraction.name, kg3.name]


def test_paired_repair_rejects_traversal_missing_and_protected_targets(
    tmp_path: Path,
) -> None:
    context = _paired_repair_context(tmp_path)
    prompts = Path(context.prompts_dir)
    editable = prompts / "EXTRACTION_ITER_3.md"
    protected = prompts / "KG_BUILDING_ITER_3.md"
    editable.write_text("prompt\n", encoding="utf-8")
    protected.write_text("prompt\n", encoding="utf-8")
    (prompts.parent / "escape.md").write_text("outside\n", encoding="utf-8")

    routed, failures = pure_llm_generation._paired_prompt_review_targets(
        context=context,
        review=_paired_repair_review(
            "../escape.md",
            "MISSING.md",
            protected.name,
        ),
        editable_targets=[editable],
    )

    assert routed == []
    assert "paired_repair_target_outside_prompts_dir:../escape.md" in failures
    assert "paired_repair_target_missing:MISSING.md" in failures
    assert f"paired_repair_target_not_editable:{protected.name}" in failures


def test_paired_review_waits_for_complete_plan_and_full_prompt_stage(
    tmp_path: Path,
) -> None:
    context = _paired_repair_context(tmp_path)
    context.iteration_blueprint = {
        "iterations": [{"iteration_number": 2}, {"iteration_number": 3}]
    }
    prompts = Path(context.prompts_dir)
    extraction = prompts / "EXTRACTION_ITER_2.md"
    kg = prompts / "KG_BUILDING_ITER_2.md"
    extraction.write_text("extract\n", encoding="utf-8")
    kg.write_text("build\n", encoding="utf-8")

    assert not pure_llm_generation._paired_prompt_review_ready(
        context=context,
        all_editable_targets=[extraction, kg],
        selected_targets=[extraction, kg],
        generation_only=False,
    )
    context.iteration_blueprint = {"iterations": [{"iteration_number": 2}]}
    assert not pure_llm_generation._paired_prompt_review_ready(
        context=context,
        all_editable_targets=[extraction, kg],
        selected_targets=[kg],
        generation_only=False,
    )
    assert pure_llm_generation._paired_prompt_review_ready(
        context=context,
        all_editable_targets=[extraction, kg],
        selected_targets=[extraction, kg],
        generation_only=False,
    )


def test_paired_repair_accepts_only_after_mechanical_and_review_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _paired_repair_context(tmp_path)
    target = Path(context.prompts_dir) / "KG_BUILDING_ITER_3.md"
    target.write_text("broken\n", encoding="utf-8")
    passed_review = {
        "decision": "pass",
        "summary": "complete",
        "critical_errors": [],
        "noncritical_observations": [],
        "confidence": 1.0,
    }
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: {
            "ok": kwargs["prompts_required"],
            "failures": [],
        },
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_materialization_with_llm",
        lambda **kwargs: passed_review,
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_finding_with_llm",
        lambda **kwargs: {
            "decision": "resolved",
            "summary": "the selected finding is fixed",
            "unresolved_reasons": [],
            "confidence": 1.0,
        },
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )

    def fake_editor(*, targets, task_prompt, validate, **kwargs):
        assert targets == [target]
        assert "exact critical evidence" in task_prompt
        target.write_text("repaired\n", encoding="utf-8")
        validation = validate()
        return {"ok": validation["ok"], "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    accepted_snapshots: dict[Path, bytes] = {}
    report, review, history = pure_llm_generation._run_paired_prompt_repairs(
        context=context,
        model_name="mock",
        review=_paired_repair_review(target.name),
        report={"ok": True, "failures": []},
        editable_targets=[target],
        foreign_contracts=None,
        edit_backend="exact_edits",
        accepted_snapshots=accepted_snapshots,
    )

    assert report["ok"]
    assert review["decision"] == "pass"
    finding_record = next(
        item
        for item in history
        if item["mode"] == "paired_prompt_finding_repair"
    )
    assert finding_record["accepted"]
    assert target.read_text(encoding="utf-8") == "repaired\n"
    assert accepted_snapshots[target] == target.read_bytes()


def test_paired_repair_fails_closed_on_mechanical_contradiction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _paired_repair_context(tmp_path)
    target = Path(context.prompts_dir) / "KG_BUILDING_ITER_3.md"
    target.write_text("broken\n", encoding="utf-8")
    calls = 0
    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {
            "ok": False,
            "failures": ["runtime_slot_contract_failure"],
        },
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_finding_with_llm",
        lambda **kwargs: pytest.fail("mechanical failure must short-circuit review"),
    )

    def fake_editor(*, validate, **kwargs):
        nonlocal calls
        calls += 1
        validation = validate()
        return {"ok": False, "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    report, review, history = pure_llm_generation._run_paired_prompt_repairs(
        context=context,
        model_name="mock",
        review=_paired_repair_review(target.name),
        report={"ok": True, "failures": []},
        editable_targets=[target],
        foreign_contracts=None,
        edit_backend="exact_edits",
    )

    assert calls == 1
    assert report["ok"]
    assert review["decision"] == "repair"
    finding_record = next(
        item
        for item in history
        if item["mode"] == "paired_prompt_finding_repair"
    )
    assert finding_record["fail_closed"]


def test_repair_only_returns_top_level_ok_after_paired_repair_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _paired_repair_context(tmp_path)
    prompts = Path(context.prompts_dir)
    extraction = prompts / "EXTRACTION_ITER_3.md"
    kg = prompts / "KG_BUILDING_ITER_3.md"
    extraction.write_text("extract\n", encoding="utf-8")
    kg.write_text("broken\n", encoding="utf-8")
    reviews = [
        _paired_repair_review(kg.name),
        {
            "decision": "pass",
            "summary": "complete",
            "critical_errors": [],
            "noncritical_observations": [],
            "confidence": 1.0,
        },
    ]
    monkeypatch.setattr(
        pure_llm_generation,
        "_write_materializable_prompt_component",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: {"ok": True, "stage_ok": True, "failures": []},
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_materialization_with_llm",
        lambda **kwargs: reviews.pop(0),
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_finding_with_llm",
        lambda **kwargs: {
            "decision": "resolved",
            "summary": "fixed",
            "unresolved_reasons": [],
            "confidence": 1.0,
        },
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )

    def fake_editor(*, validate, **kwargs):
        kg.write_text("repaired\n", encoding="utf-8")
        validation = validate()
        return {"ok": validation["ok"], "validation": validation}

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    result = pure_llm_generation.run_pure_llm_generation_rounds(
        context,
        model_name="mock",
        max_rounds=1,
        generate_scripts=False,
        generate_prompts=True,
        repair_only=True,
        target_artifacts=[extraction.name, kg.name],
    )

    assert result["ok"]
    assert result["final_report"]["ok"]
    assert result["paired_materialization_review"]["decision"] == "pass"
    assert any(
        item.get("accepted")
        for item in result["paired_repair_history"]
        if item["mode"] == "paired_prompt_finding_repair"
    )


def test_paired_repair_edits_every_finding_target_one_file_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _paired_repair_context(tmp_path)
    prompts = Path(context.prompts_dir)
    targets = [
        prompts / "EXTRACTION_ITER_2.md",
        prompts / "KG_BUILDING_ITER_2.md",
        prompts / "EXTRACTION_ITER_3.md",
        prompts / "KG_BUILDING_ITER_3.md",
    ]
    for target in targets:
        target.write_text(f"broken {target.name}\n", encoding="utf-8")
    review = _paired_repair_review(*(target.name for target in reversed(targets)))
    passed = {
        "decision": "pass",
        "summary": "complete",
        "critical_errors": [],
        "noncritical_observations": [],
        "confidence": 1.0,
    }
    edit_order: list[str] = []
    task_prompts: list[str] = []

    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_materialization_with_llm",
        lambda **kwargs: passed,
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_finding_with_llm",
        lambda **kwargs: {
            "decision": "resolved",
            "summary": "all target files now satisfy this finding",
            "unresolved_reasons": [],
            "confidence": 1.0,
        },
    )

    def fake_editor(*, targets, task_prompt, validate, max_targets, **kwargs):
        assert len(targets) == 1
        assert max_targets == 1
        target = targets[0]
        edit_order.append(target.name)
        task_prompts.append(task_prompt)
        target.write_text(f"repaired {target.name}\n", encoding="utf-8")
        validation = validate()
        return {
            "ok": validation["ok"],
            "backend": "pure_llm_exact_edits",
            "changed_files": [target.name],
            "failure_codes": [],
            "failure_messages": [],
        }

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    report, final_review, history = (
        pure_llm_generation._run_paired_prompt_repairs(
            context=context,
            model_name="mock",
            review=review,
            report={"ok": True, "failures": []},
            editable_targets=targets,
            foreign_contracts=None,
            edit_backend="exact_edits",
        )
    )

    assert report["ok"]
    assert final_review["decision"] == "pass"
    assert edit_order == [
        "EXTRACTION_ITER_2.md",
        "EXTRACTION_ITER_3.md",
        "KG_BUILDING_ITER_2.md",
        "KG_BUILDING_ITER_3.md",
    ]
    assert all('"where_it_is_wrong"' in prompt for prompt in task_prompts)
    assert all('"what_is_correct"' in prompt for prompt in task_prompts)
    assert all("paired_materialization_review" not in prompt for prompt in task_prompts)
    assert all("prompt_pairs" not in prompt for prompt in task_prompts)
    finding_record = next(
        item for item in history if item["mode"] == "paired_prompt_finding_repair"
    )
    assert finding_record["accepted"]
    assert len(finding_record["file_edits"]) == 4


def test_paired_repair_stops_after_fifth_global_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _paired_repair_context(tmp_path)
    target = Path(context.prompts_dir) / "KG_BUILDING_ITER_3.md"
    target.write_text("broken\n", encoding="utf-8")
    review = _paired_repair_review(target.name)
    global_review_calls = 0
    edit_calls = 0

    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_finding_with_llm",
        lambda **kwargs: {
            "decision": "resolved",
            "summary": "locally fixed",
            "unresolved_reasons": [],
            "confidence": 1.0,
        },
    )

    def fake_global_review(**kwargs):
        nonlocal global_review_calls
        global_review_calls += 1
        return review

    def fake_editor(*, targets, validate, **kwargs):
        nonlocal edit_calls
        edit_calls += 1
        targets[0].write_text(f"repair {edit_calls}\n", encoding="utf-8")
        validation = validate()
        return {
            "ok": validation["ok"],
            "backend": "pure_llm_exact_edits",
            "changed_files": [targets[0].name],
            "failure_codes": [],
            "failure_messages": [],
        }

    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_materialization_with_llm",
        fake_global_review,
    )
    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    _, final_review, history = pure_llm_generation._run_paired_prompt_repairs(
        context=context,
        model_name="mock",
        review=review,
        report={"ok": True, "failures": []},
        editable_targets=[target],
        foreign_contracts=None,
        edit_backend="exact_edits",
    )

    assert final_review["decision"] == "repair"
    assert global_review_calls == 4
    assert edit_calls == 4
    global_records = [
        item for item in history if item["mode"] == "paired_prompt_global_review"
    ]
    assert len(global_records) == 5
    assert global_records[-1]["final_review_exhausted"]


def test_paired_finding_retry_keeps_focused_feedback_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _paired_repair_context(tmp_path)
    target = Path(context.prompts_dir) / "KG_BUILDING_ITER_3.md"
    target.write_text("broken\n", encoding="utf-8")
    passed = {
        "decision": "pass",
        "summary": "complete",
        "critical_errors": [],
        "noncritical_observations": [],
        "confidence": 1.0,
    }
    verdicts = [
        {
            "decision": "repair",
            "summary": "lifecycle fixed but order is still missing",
            "unresolved_reasons": ["Pass hasOrder into create_DoStep."],
            "confidence": 1.0,
        },
        {
            "decision": "resolved",
            "summary": "finding resolved",
            "unresolved_reasons": [],
            "confidence": 1.0,
        },
    ]
    task_prompts: list[str] = []

    monkeypatch.setattr(
        pure_llm_generation,
        "validate_prompt_runtime_bindings",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        pure_llm_generation,
        "build_validation_report",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_materialization_with_llm",
        lambda **kwargs: passed,
    )
    monkeypatch.setattr(
        semantic_script_review,
        "review_paired_prompt_finding_with_llm",
        lambda **kwargs: verdicts.pop(0),
    )

    def fake_editor(*, targets, task_prompt, validate, **kwargs):
        task_prompts.append(task_prompt)
        prior = targets[0].read_text(encoding="utf-8")
        targets[0].write_text(prior + "repair\n", encoding="utf-8")
        validation = validate()
        return {
            "ok": validation["ok"],
            "backend": "pure_llm_exact_edits",
            "changed_files": [targets[0].name],
            "failure_codes": [],
            "failure_messages": [],
        }

    monkeypatch.setattr(
        pure_llm_generation, "run_llm_unified_diff_editor", fake_editor
    )

    _, final_review, history = pure_llm_generation._run_paired_prompt_repairs(
        context=context,
        model_name="mock",
        review=_paired_repair_review(target.name),
        report={"ok": True, "failures": []},
        editable_targets=[target],
        foreign_contracts=None,
        edit_backend="exact_edits",
    )

    assert final_review["decision"] == "pass"
    assert len(task_prompts) == 2
    assert "Pass hasOrder into create_DoStep." in task_prompts[1]
    finding_record = next(
        item for item in history if item["mode"] == "paired_prompt_finding_repair"
    )
    assert finding_record["accepted"]
    assert len(finding_record["focused_verdicts"]) == 2
    assert target.read_text(encoding="utf-8") == "broken\nrepair\nrepair\n"
