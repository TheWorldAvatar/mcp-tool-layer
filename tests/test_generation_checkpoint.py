"""Tests for generation checkpoint replay and repair-only orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation import pure_llm_generation
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
