from __future__ import annotations

import json
import threading
from pathlib import Path

from src.agents.scripts_and_prompts_generation import (
    tbox_reusability_experiment as experiment,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)


def _valid_result(class_iri: str, tbox_sha256: str) -> dict:
    return {
        "schema_version": "single-tbox-operational-reusability.v3",
        "decision_target": "pipeline_reuse_enabled",
        "tbox_sha256": tbox_sha256,
        "reusable_classes": [],
        "non_reusable_classes": [
            {
                "class_iri": class_iri,
                "confidence": "high",
                "tbox_evidence": ["Declared as an OWL class."],
                "pipeline_evidence": ["Materialized as an owned occurrence."],
                "contextual_value_veto": {
                    "applies": False,
                    "direct_contextual_properties": [],
                    "repeated_owner_paths": [],
                    "ownership_recoverable_after_merge": True,
                    "explanation": "No contextual values are attached.",
                },
                "reason": "The pipeline requires occurrence identity.",
            }
        ],
    }


def test_three_parallel_trials_recover_schema_failures(
    tmp_path: Path, monkeypatch
) -> None:
    class_iri = "https://example.com/TestClass"
    tbox_content = (
        "@prefix ex: <https://example.com/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:TestClass a owl:Class .\n"
    )
    tbox_path = tmp_path / "tbox.ttl"
    tbox_path.write_text(tbox_content, encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text(
        "{class_inventory_json}\n{materialization_plan_json}\n"
        "{cross_tbox_contexts_json}\n{supporting_tboxes_json}\n"
        "{tbox_sha256}\n{tbox_content}",
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    supporting_tbox_path = tmp_path / "supporting.ttl"
    supporting_tbox_path.write_text(
        "@prefix ex: <https://example.com/> .\n"
        "ex:hasStableIdentifier a "
        "<http://www.w3.org/2002/07/owl#DatatypeProperty> .\n",
        encoding="utf-8",
    )
    cross_tbox_context_path = tmp_path / "domain.json"
    cross_tbox_context_path.write_text(
        json.dumps({"runtime": {"extensions": [{"name": "supporting"}]}}),
        encoding="utf-8",
    )
    tbox_sha256 = experiment._sha256_text(tbox_content)

    calls: list[str] = []
    calls_lock = threading.Lock()

    def fake_invoke_json(model: str, prompt: str, **_: object) -> LLMJsonResult:
        with calls_lock:
            calls.append(prompt)
        assert "hasStableIdentifier" in prompt
        assert '"extensions"' in prompt
        if "FORMAT CORRECTION REQUEST" not in prompt:
            data = {
                **_valid_result(class_iri, tbox_sha256),
                "non_reusable_classes": [],
            }
        else:
            assert "missing inventory classes: https://example.com/TestClass" in prompt
            assert '"non_reusable_classes": []' in prompt
            data = _valid_result(class_iri, tbox_sha256)
        return LLMJsonResult(
            data=data,
            elapsed_seconds=0.01,
            token_usage={"total_tokens": 1},
            raw_response=json.dumps(data),
        )

    monkeypatch.setattr(experiment, "invoke_json", fake_invoke_json)
    output_dir = tmp_path / "results"
    summary = experiment.run_experiment(
        tbox_path=tbox_path,
        prompt_path=prompt_path,
        output_dir=output_dir,
        model="fake-model",
        trials=3,
        parallelism=3,
        materialization_plan_path=plan_path,
        format_retry_limit=2,
        supporting_tbox_paths=[supporting_tbox_path],
        cross_tbox_context_paths=[cross_tbox_context_path],
    )

    assert summary["requested_trials"] == 3
    assert summary["valid_trials"] == 3
    assert summary["all_trials_valid"] is True
    assert summary["total_attempt_count"] == 6
    assert summary["trial_attempt_counts"] == {"1": 2, "2": 2, "3": 2}
    assert summary["recovered_format_failures"] == 3
    assert summary["recovered_format_failure_trials"] == [1, 2, 3]
    assert summary["exhausted_format_failures"] == 0
    assert len(calls) == 6

    manifest = json.loads(
        (output_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["format_retry_limit"] == 2
    assert manifest["supporting_tboxes"][0]["path"] == str(supporting_tbox_path)
    assert manifest["cross_tbox_contexts"][0]["path"] == str(cross_tbox_context_path)
    for trial in range(1, 4):
        first_attempt = json.loads(
            (output_dir / f"trial_{trial}_attempt_1.json").read_text(encoding="utf-8")
        )
        second_attempt = json.loads(
            (output_dir / f"trial_{trial}_attempt_2.json").read_text(encoding="utf-8")
        )
        final = json.loads(
            (output_dir / f"trial_{trial}.json").read_text(encoding="utf-8")
        )
        assert first_attempt["validation"]["ok"] is False
        assert second_attempt["validation"]["ok"] is True
        assert final["accepted_attempt"] == 2
        assert final["attempt_count"] == 2
        assert final["recovered_format_failure"] is True
        assert [item["attempt"] for item in final["attempt_history"]] == [1, 2]
