from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    extract_json_object,
)
from src.agents.scripts_and_prompts_generation.llm_global_context_resolver import (
    AUDIT_SCHEMA_VERSION,
    BRIEF_BEGIN,
    INHERITANCE_RULE,
    SCHEMA_VERSION,
    inject_global_context_brief,
    load_global_context_brief,
    render_global_context_brief,
    resolve_global_context,
    validate_global_context_resolution,
)


def _resolution() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "contexts": [
            {
                "context_id": "G001",
                "context_class_iri": "https://example.com/Context",
                "target_property_iri": "https://example.com/hasContext",
                "canonical_value": "Controlled context",
                "source_evidence": "Every procedure in family Q used the controlled context.",
                "declared_scope": "every procedure in family Q",
                "scope_kind": "procedure-family",
                "inheritance_rule": INHERITANCE_RULE,
                "exceptions": [],
            }
        ],
        "unresolved_references": [],
        "rationale": "The complete source explicitly quantifies the context.",
    }


def test_resolution_coerces_fixed_inheritance_rule() -> None:
    candidate = _resolution()
    candidate["contexts"][0]["inheritance_rule"] = "free-form"
    normalized = validate_global_context_resolution(candidate)
    assert normalized["contexts"][0]["inheritance_rule"] == INHERITANCE_RULE


def test_resolver_is_audited_cached_and_rendered_idempotently(tmp_path: Path) -> None:
    candidate = _resolution()
    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "accepted": True,
        "gaps": [],
        "rationale": "Complete and scoped.",
    }
    calls = 0
    lock = threading.Lock()

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        nonlocal calls
        with lock:
            calls += 1
        payload = audit if "completeness auditor" in prompt else candidate
        return LLMJsonResult(
            data=copy.deepcopy(payload),
            elapsed_seconds=0.0,
            token_usage={},
        )

    cache = tmp_path / "global_procedure_context.json"
    kwargs = {
        "source_text": "Every procedure in family Q used the controlled context.",
        "tbox_contract": "Context is explicitly scoped and inheritable.",
        "model": "judge",
        "invoke": fake_invoke,
        "cache_path": cache,
    }
    first = resolve_global_context(**kwargs)
    second = resolve_global_context(**kwargs)
    assert first == second
    assert calls == 3
    brief = render_global_context_brief(first)
    assert "scope_resolution.source_dependencies" in brief
    assert "every compatible owned occurrence" in brief
    prompt = inject_global_context_brief("STATIC", brief)
    assert inject_global_context_brief(prompt, brief).count(BRIEF_BEGIN) == 1
    assert load_global_context_brief(cache) == brief


def test_transport_strips_extra_keys_and_keeps_filled_contexts() -> None:
    candidate = _resolution()
    candidate["notes"] = "extra root key"
    candidate["contexts"][0]["comment"] = "extra context key"
    candidate["contexts"][0]["exceptions"] = ["", "keep this override"]
    normalized = validate_global_context_resolution(candidate)
    assert list(normalized) == [
        "schema_version",
        "contexts",
        "unresolved_references",
        "rationale",
    ]
    assert normalized["contexts"][0]["canonical_value"] == "Controlled context"
    assert normalized["contexts"][0]["exceptions"] == ["keep this override"]
    assert "comment" not in normalized["contexts"][0]


def test_incomplete_or_placeholder_rows_collapse_to_empty_table() -> None:
    normalized = validate_global_context_resolution(
        {
            "schema_version": SCHEMA_VERSION,
            "contexts": [
                {
                    "context_id": "G001",
                    "context_class_iri": "",
                    "target_property_iri": "",
                    "canonical_value": "",
                    "source_evidence": "",
                    "declared_scope": "",
                    "scope_kind": "",
                    "inheritance_rule": INHERITANCE_RULE,
                    "exceptions": [""],
                }
            ],
            "unresolved_references": [],
            "rationale": "model copied the empty example row",
            "notes": "extra",
        }
    )
    assert normalized["contexts"] == []


def test_planner_non_object_payload_becomes_empty_table(tmp_path: Path) -> None:
    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        if "completeness auditor" in prompt:
            return LLMJsonResult(
                data={
                    "schema_version": AUDIT_SCHEMA_VERSION,
                    "accepted": True,
                    "gaps": [],
                    "rationale": "Empty table is complete.",
                },
                elapsed_seconds=0.0,
                token_usage={},
            )
        return LLMJsonResult(data=None, elapsed_seconds=0.0, token_usage={})

    cache = tmp_path / "global_procedure_context.json"
    resolution = resolve_global_context(
        source_text="No shared atmosphere is stated.",
        tbox_contract="VesselEnvironment may inherit when explicit.",
        model="judge",
        invoke=fake_invoke,
        cache_path=cache,
    )

    assert resolution["contexts"] == []
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert "fail_open" not in payload


def test_audit_extra_keys_are_stripped_and_accepted(tmp_path: Path) -> None:
    candidate = _resolution()

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        if "completeness auditor" in prompt:
            return LLMJsonResult(
                data={
                    "schema_version": AUDIT_SCHEMA_VERSION,
                    "accepted": True,
                    "gaps": [],
                    "rationale": "Complete and scoped.",
                    "extra": "bad",
                },
                elapsed_seconds=0.0,
                token_usage={},
            )
        return LLMJsonResult(
            data=copy.deepcopy(candidate),
            elapsed_seconds=0.0,
            token_usage={},
        )

    cache = tmp_path / "global_procedure_context.json"
    resolution = resolve_global_context(
        source_text="Every procedure in family Q used the controlled context.",
        tbox_contract="Context is explicitly scoped and inheritable.",
        model="judge",
        invoke=fake_invoke,
        cache_path=cache,
    )
    assert resolution["contexts"][0]["context_id"] == "G001"
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert "fail_open" not in payload


def test_unresolved_references_fail_open_and_keep_resolved_contexts(
    tmp_path: Path,
) -> None:
    candidate = _resolution()
    candidate["unresolved_references"] = [
        "Gas Adsorption Measurements: glove box atmosphere unnamed; "
        "characterization only, not guessed."
    ]

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        if "completeness auditor" in prompt:
            raise AssertionError("audit must not run after unresolved fail-open")
        return LLMJsonResult(
            data=copy.deepcopy(candidate),
            elapsed_seconds=0.0,
            token_usage={},
        )

    cache = tmp_path / "global_procedure_context.json"
    resolution = resolve_global_context(
        source_text="Samples were loaded in the glove box for BET.",
        tbox_contract="VesselEnvironment inherits only a named atmosphere.",
        model="judge",
        invoke=fake_invoke,
        cache_path=cache,
    )

    assert resolution["contexts"][0]["context_id"] == "G001"
    assert resolution["unresolved_references"] == candidate["unresolved_references"]
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["fail_open"]["kind"] == "unresolved_references"
    assert "glove box" in payload["fail_open"]["message"]


def test_unresolved_characterization_atmosphere_does_not_abort(
    tmp_path: Path,
) -> None:
    candidate = {
        "schema_version": SCHEMA_VERSION,
        "contexts": [],
        "unresolved_references": [
            "The Cu_X-bdc samples were loaded into the glove box, but no "
            "nitrogen/argon is named."
        ],
        "rationale": "Flagged rather than guessed as an inert atmosphere.",
    }

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        if "completeness auditor" in prompt:
            raise AssertionError("audit must not run after unresolved fail-open")
        return LLMJsonResult(
            data=copy.deepcopy(candidate),
            elapsed_seconds=0.0,
            token_usage={},
        )

    resolution = resolve_global_context(
        source_text="Brought into the glove box and degassed for BET.",
        tbox_contract="VesselEnvironment may inherit only when explicit.",
        model="judge",
        invoke=fake_invoke,
        cache_path=tmp_path / "global_procedure_context.json",
    )

    assert resolution["contexts"] == []
    assert resolution["unresolved_references"]
    brief = render_global_context_brief(resolution)
    assert "glove box" in brief


def test_semantic_audit_exhaustion_fails_open_to_empty_table(tmp_path: Path) -> None:
    bogus = {
        "schema_version": SCHEMA_VERSION,
        "contexts": [
            {
                "context_id": "G001",
                "context_class_iri": "https://example.com/VesselEnvironment",
                "target_property_iri": "https://example.com/hasVesselEnvironment",
                "canonical_value": "N/A",
                "source_evidence": "No atmosphere is stated.",
                "declared_scope": "all syntheses",
                "scope_kind": "document",
                "inheritance_rule": INHERITANCE_RULE,
                "exceptions": [],
            }
        ],
        "unresolved_references": [],
        "rationale": "Silence treated as a shared N/A atmosphere.",
    }

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        if "completeness auditor" in prompt:
            return LLMJsonResult(
                data={
                    "schema_version": AUDIT_SCHEMA_VERSION,
                    "accepted": False,
                    "gaps": [
                        "Absence of atmosphere is not a VesselEnvironment context."
                    ],
                    "rationale": "The correct output is an empty candidate.",
                },
                elapsed_seconds=0.0,
                token_usage={},
            )
        return LLMJsonResult(
            data=copy.deepcopy(bogus),
            elapsed_seconds=0.0,
            token_usage={},
        )

    cache = tmp_path / "global_procedure_context.json"
    resolution = resolve_global_context(
        source_text="No shared atmosphere is stated.",
        tbox_contract="VesselEnvironment may inherit only when explicit.",
        model="judge",
        invoke=fake_invoke,
        cache_path=cache,
    )

    assert resolution["contexts"] == []
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["fail_open"]["kind"] == "semantic_audit_exhausted"
    assert "VesselEnvironment" in payload["fail_open"]["message"]


def test_empty_context_is_still_injected_as_authoritative_ledger(tmp_path: Path) -> None:
    resolution = {
        "schema_version": SCHEMA_VERSION,
        "contexts": [],
        "unresolved_references": [],
        "rationale": "No qualifying shared context.",
    }
    brief = render_global_context_brief(resolution)
    assert '"contexts": []' in brief
    cache = tmp_path / "global_procedure_context.json"
    cache.write_text(
        json.dumps({"cache_key": "unused", "resolution": resolution}),
        encoding="utf-8",
    )
    assert BRIEF_BEGIN in load_global_context_brief(cache)


def test_ontosynthesis_tbox_declares_scoped_environment_inheritance() -> None:
    text = Path("data/ontologies/ontosynthesis.ttl").read_text(encoding="utf-8")
    assert "GLOBAL-CONTEXT RESOLUTION AND INHERITANCE" in text
    assert "source dependency of every covered target" in text
    assert "step-local hasVesselEnvironment" in text


def test_every_extraction_and_kg_pipeline_wires_the_cached_context_brief() -> None:
    expected_calls = {
        "src/pipelines/top_entity_extraction/extract.py": (
            "resolve_global_context(",
            "inject_global_context_brief(",
        ),
        "src/pipelines/top_entity_kg_building/build.py": (
            "load_global_context_brief(",
            "inject_global_context_brief(",
        ),
        "src/pipelines/main_ontology_extractions/extract.py": (
            "resolve_global_context(",
            "inject_global_context_brief(",
        ),
        "src/pipelines/main_kg_building/build.py": (
            "load_global_context_brief(",
            "inject_global_context_brief(",
        ),
    }
    for path, calls in expected_calls.items():
        source = Path(path).read_text(encoding="utf-8")
        for call in calls:
            assert call in source, f"{path} does not wire {call}"


def test_extract_json_object_recovers_body_without_outer_braces() -> None:
    payload = extract_json_object(
        '  "schema_version": "global-procedure-context-audit.v1",\n'
        '  "accepted": true,\n'
        '  "gaps": [],\n'
        '  "rationale": ""\n'
    )
    assert payload["schema_version"] == "global-procedure-context-audit.v1"
    assert payload["accepted"] is True
    assert payload["gaps"] == []


def test_planner_invoke_transport_error_fails_open(tmp_path: Path) -> None:
    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        raise RuntimeError(
            "LLM did not return a JSON object (len=109 preview="
            "'  \"schema_version\": \"global-procedure-context-audit.v1\"')"
        )

    cache = tmp_path / "global_procedure_context.json"
    resolution = resolve_global_context(
        source_text="Samples were loaded in the glove box for BET.",
        tbox_contract="VesselEnvironment inherits only a named atmosphere.",
        model="judge",
        invoke=fake_invoke,
        cache_path=cache,
    )

    assert resolution["contexts"] == []
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["fail_open"]["kind"] == "planner_schema_exhausted"
