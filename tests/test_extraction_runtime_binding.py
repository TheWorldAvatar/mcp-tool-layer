import json
from pathlib import Path

import pytest
import src.pipelines.main_ontology_extractions.extract as extraction_module

from src.pipelines.main_ontology_extractions.extract import (
    _build_closed_ledger_audit_prompt,
    _build_closed_ledger_format_repair_prompt,
    _build_type_selection_judge_prompt,
    _build_tbox_contract_audit_prompt,
    _bounded_sidecar_path,
    _compose_downstream_iteration_source,
    _format_closed_ledger_feedback_history,
    _kg_revision_relation_errors,
    _load_accumulated_prior_hints,
    _load_reused_pre_extraction,
    _merge_structured_hint_payloads,
    _merge_structured_hint_text,
    _parse_closed_ledger_audit,
    _parse_type_selection_judgement,
    _parse_tbox_contract_audit,
    _closed_ledger_feedback_fingerprint,
    _pre_extraction_retry_wait_seconds,
    _should_stop_closed_ledger_retry,
    _prune_untyped_closed_ledger_evidence,
    _run_closed_ledger_audit_panel,
    _run_operation_projection_panel,
    _run_type_selection_judge,
    _safe_name,
    _semantic_audit_sidecar_path,
    _append_semantic_hint_output_boundary,
    _validate_closed_ledger_shape,
    _write_declared_sub_iteration_file,
    bind_runtime_context,
    load_prompt,
    run_extraction,
    run_pre_extraction,
)
from src.pipelines.structured_extraction import validate_hint_payload


def test_closed_ledger_warning_sidecar_shortens_long_windows_path(tmp_path: Path) -> None:
    responses = tmp_path / "responses"
    safe = "Synthesis_of_" + "very_long_entity_" * 20

    sidecar = _bounded_sidecar_path(
        str(responses),
        safe,
        ".closed_ledger_warning.json",
        max_path_chars=240,
    )

    assert len(str(sidecar.resolve())) <= 240
    assert sidecar.name.endswith(".closed_ledger_warning.json")
    assert "--" in sidecar.name


def test_semantic_audit_sidecar_shortens_long_windows_path(tmp_path: Path) -> None:
    hints = tmp_path / ("iter3_hints_" + "very_long_entity_" * 20 + ".txt")

    sidecar = Path(_semantic_audit_sidecar_path(str(hints)))

    assert sidecar.parent == tmp_path
    assert sidecar.name.startswith("iter3_semantic_audit--")
    assert len(str(sidecar.absolute())) < len(
        str(Path(f"{hints}.semantic_audit.json").absolute())
    )


def test_entity_safe_name_caps_long_labels_with_stable_hash() -> None:
    label = "long synthesis label " * 30

    first = _safe_name(label)
    second = _safe_name(label)

    assert first == second
    assert len(first) <= 62
    assert "--" in first


def _type_judgement_payload(
    *,
    evidence_id: str = "E001",
    selected: list[str] | None = None,
    corrected: list[str] | None = None,
    verdict: str = "misclassified",
) -> tuple[str, str]:
    selected_types = list(selected or ["ClassA"])
    corrected_types = list(corrected if corrected is not None else selected_types)
    candidate = {
        "evidence": [
            {
                "evidence_id": evidence_id,
                "candidate_types": selected_types,
            }
        ]
    }
    judgement = {
        "type_checks": [
            {
                "candidate_evidence_id": evidence_id,
                "selected_types": selected_types,
                "verdict": verdict,
                "corrected_types": corrected_types,
                "source_evidence": "named materials were introduced together",
                "reason": "type-set encoding check",
            }
        ]
    }
    return json.dumps(judgement), json.dumps(candidate)


def test_type_judge_drops_repeated_class_encoding() -> None:
    judgement, candidate = _type_judgement_payload(
        selected=["ClassA"],
        corrected=["ClassA", "ClassA", "ClassA"],
    )

    assert _parse_type_selection_judgement(judgement, candidate_text=candidate) == []


def test_type_judge_drops_noop_misclassified_same_set() -> None:
    judgement, candidate = _type_judgement_payload(
        selected=["ClassA"],
        corrected=["ClassA"],
    )

    assert _parse_type_selection_judgement(judgement, candidate_text=candidate) == []


def test_type_judge_keeps_distinct_class_replacement() -> None:
    judgement, candidate = _type_judgement_payload(
        selected=["ClassA"],
        corrected=["ClassB"],
    )

    feedback = _parse_type_selection_judgement(judgement, candidate_text=candidate)
    assert len(feedback) == 1
    assert "replace candidate_types ['ClassA'] with ['ClassB']" in feedback[0]


def test_type_judge_dedupes_before_emitting_distinct_set() -> None:
    judgement, candidate = _type_judgement_payload(
        selected=["ClassA"],
        corrected=["ClassA", "ClassB", "ClassA"],
    )

    feedback = _parse_type_selection_judgement(judgement, candidate_text=candidate)
    assert len(feedback) == 1
    assert "replace candidate_types ['ClassA'] with ['ClassA', 'ClassB']" in feedback[0]


def test_type_judge_same_class_set_different_order_is_not_a_type_error() -> None:
    judgement, candidate = _type_judgement_payload(
        selected=["ClassA", "ClassB"],
        corrected=["ClassB", "ClassA"],
    )

    assert _parse_type_selection_judgement(judgement, candidate_text=candidate) == []


def test_closed_ledger_feedback_fingerprint_ignores_wording() -> None:
    first = _closed_ledger_feedback_fingerprint(
        [
            "TYPE_SELECTION_MISCLASSIFIED `E001`: replace candidate_types ['ClassA'] "
            "with ['ClassB']. wording one [source: alpha]",
            "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom "
            "for source `alpha`",
        ]
    )
    second = _closed_ledger_feedback_fingerprint(
        [
            "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom "
            "for source `beta`",
            "TYPE_SELECTION_MISCLASSIFIED `E001`: replace candidate_types ['ClassA'] "
            "with ['ClassC']. wording two [source: beta]",
        ]
    )

    assert first == second


def test_pre_extraction_retry_waits_only_for_transport_errors() -> None:
    assert _pre_extraction_retry_wait_seconds(ValueError("Closed-ledger semantic audit rejected the draft"), 0) == 0.0
    assert _pre_extraction_retry_wait_seconds(ValueError("Connection error."), 0) == 5.0
    assert _pre_extraction_retry_wait_seconds(ConnectionError("reset"), 2) == 15.0


def test_closed_ledger_retry_stops_on_repeated_fingerprint() -> None:
    fingerprint = frozenset({"TYPE_SELECTION_MISCLASSIFIED|E001"})
    assert _should_stop_closed_ledger_retry(
        current_fingerprint=fingerprint,
        previous_fingerprint=None,
        attempt=0,
        max_retries=8,
        nonblocking=True,
    ) is False
    assert _should_stop_closed_ledger_retry(
        current_fingerprint=fingerprint,
        previous_fingerprint=fingerprint,
        attempt=1,
        max_retries=8,
        nonblocking=True,
    ) is True
    assert _should_stop_closed_ledger_retry(
        current_fingerprint=frozenset({"LEDGER_ATOMICITY[OTHER]|E002"}),
        previous_fingerprint=fingerprint,
        attempt=1,
        max_retries=8,
        nonblocking=True,
    ) is False


def test_closed_ledger_retry_feedback_preserves_all_prior_failures() -> None:
    rendered = _format_closed_ledger_feedback_history(
        [
            "washing solvent was misclassified as Add",
            "Separate operation is missing",
            "candidate_types must be non-empty",
        ]
    )

    assert "ATTEMPT 1 FAILURE" in rendered
    assert "washing solvent was misclassified as Add" in rendered
    assert "ATTEMPT 2 FAILURE" in rendered
    assert "Separate operation is missing" in rendered
    assert "ATTEMPT 3 FAILURE" in rendered
    assert "candidate_types must be non-empty" in rendered


def test_closed_ledger_prunes_context_only_untyped_atoms_and_renumbers() -> None:
    candidate = {
        "scope_resolution": {},
        "evidence": [
            {
                "evidence_id": "E001",
                "source_order": 1,
                "candidate_types": ["Add"],
            },
            {
                "evidence_id": "E002",
                "source_order": 2,
                "candidate_types": [],
            },
            {
                "evidence_id": "E003",
                "source_order": 3,
                "candidate_types": ["Dry"],
            },
        ],
    }

    repaired = json.loads(
        _prune_untyped_closed_ledger_evidence(json.dumps(candidate))
    )

    assert [item["evidence_id"] for item in repaired["evidence"]] == [
        "E001",
        "E002",
    ]
    assert [item["source_order"] for item in repaired["evidence"]] == [1, 2]
    assert repaired["evidence"][1]["candidate_types"] == ["Dry"]


def test_closed_ledger_auditor_receives_prior_contradictions() -> None:
    prompt = _build_closed_ledger_audit_prompt(
        original_prompt="T-Box says explicit agitation only.",
        source_text="The materials were mixed.",
        candidate_text='{"evidence":[]}',
        prior_feedback=[
            "MISSING_OPERATION: emit Stir for the materials were mixed",
            "MISCLASSIFIED_OPERATION: mixed is not explicit Stir",
        ],
    )

    assert "PRIOR AUDIT FINDINGS" in prompt
    assert "ATTEMPT 1 FAILURE" in prompt
    assert "ATTEMPT 2 FAILURE" in prompt
    assert "do not alternate verdicts" in prompt


@pytest.mark.asyncio
async def test_closed_ledger_audit_panel_requires_unanimous_pass() -> None:
    candidate = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "source operation",
                    "candidate_types": ["Add"],
                    "candidate_properties": {},
                }
            ]
        }
    )
    passed_audit = json.dumps(
        {"operation_checks": [], "non_type_violations": []}
    )
    rejecting_audit = json.dumps(
        {
            "operation_checks": [],
            "non_type_violations": [
                {
                    "candidate_evidence_id": "E001",
                    "dimension": "property_fidelity",
                    "code": "CONTRACT_RULE_VIOLATION",
                    "is_violation": True,
                    "source_evidence": "source operation",
                    "message": "The candidate conflicts with the injected contract.",
                }
            ],
        }
    )

    class FakeAuditLlm:
        def __init__(self) -> None:
            self.responses = iter([passed_audit, rejecting_audit, passed_audit])
            self.prompts: list[str] = []

        async def ainvoke(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return next(self.responses)

    llm = FakeAuditLlm()
    feedback = await _run_closed_ledger_audit_panel(
        audit_llm=llm,
        original_prompt="contract",
        source_text="source operation",
        candidate_text=candidate,
        prior_feedback=[],
        vote_count=3,
    )

    assert len(llm.prompts) == 3
    assert "LEDGER_PROPERTY_FIDELITY[CONTRACT_RULE_VIOLATION]" in feedback[0]
    assert "coverage, dependency, and atomicity" in llm.prompts[1]
    assert "property-fidelity and dependency" in llm.prompts[2]
    assert all("NOT a type-selection judge" in prompt for prompt in llm.prompts)


@pytest.mark.asyncio
async def test_closed_ledger_audit_panel_short_circuits_on_primary_rejection() -> None:
    candidate = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "source operation",
                    "candidate_types": ["Add"],
                    "candidate_properties": {},
                }
            ]
        }
    )
    rejecting_audit = json.dumps(
        {
            "operation_checks": [
                {
                    "source_evidence": "source operation",
                    "operation": "missing operation",
                    "status": "missing",
                    "candidate_evidence_id": None,
                    "reason": "No matching evidence atom exists.",
                }
            ],
            "non_type_violations": [],
        }
    )

    class FakeAuditLlm:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _prompt: str) -> str:
            self.calls += 1
            return rejecting_audit

    llm = FakeAuditLlm()
    feedback = await _run_closed_ledger_audit_panel(
        audit_llm=llm,
        original_prompt="contract",
        source_text="source operation",
        candidate_text=candidate,
        prior_feedback=[],
        vote_count=3,
    )

    assert llm.calls == 1
    assert feedback == [
        "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom "
        "for source `source operation`"
    ]


@pytest.mark.asyncio
async def test_closed_ledger_audit_format_retry_does_not_change_candidate_budget(
    tmp_path: Path,
) -> None:
    candidate = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "source operation",
                    "candidate_types": ["Add"],
                    "candidate_properties": {},
                }
            ]
        }
    )
    malformed_audit = json.dumps(
        {
            "operation_checks": [],
            "non_type_violations": [{"candidate_evidence_id": "E999"}],
        }
    )
    passed_audit = json.dumps(
        {"operation_checks": [], "non_type_violations": []}
    )

    class FakeLlm:
        def __init__(self, responses: list[str]) -> None:
            self.responses = iter(responses)
            self.prompts: list[str] = []

        async def ainvoke(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return next(self.responses)

    audit_llm = FakeLlm([malformed_audit])
    format_llm = FakeLlm([passed_audit])
    feedback = await _run_closed_ledger_audit_panel(
        audit_llm=audit_llm,
        format_llm=format_llm,
        original_prompt="contract",
        source_text="source operation",
        candidate_text=candidate,
        prior_feedback=[],
        vote_count=1,
        format_retries=2,
        format_trace_dir=str(tmp_path),
        format_trace_stem="Target_" + ("very_long_entity_name_" * 20),
    )

    assert feedback == []
    assert len(audit_llm.prompts) == 1
    assert len(format_llm.prompts) == 1
    assert "JSON schema normalization processor" in format_llm.prompts[0]
    assert "ORIGINAL PRE-EXTRACTION PROMPT" not in format_llm.prompts[0]
    assert '"E001"' in format_llm.prompts[0]
    trace_files = list(tmp_path.glob("clf_*_v1_f0.json"))
    assert len(trace_files) == 1
    assert len(trace_files[0].name) < 40
    trace = json.loads(trace_files[0].read_text(encoding="utf-8"))
    assert trace["invalid_response"] == malformed_audit
    assert trace["trace_stem"].startswith("Target_very_long_entity_name_")


def test_closed_ledger_format_prompt_is_domain_agnostic() -> None:
    prompt = _build_closed_ledger_format_repair_prompt(
        invalid_audit_text='{"non_type_violations":[]}',
        validation_error="missing operation_checks",
        candidate_text=json.dumps({"evidence": [{"evidence_id": "E001"}]}),
    )

    assert "not a semantic auditor" in prompt
    assert "candidate_evidence_id=null" in prompt
    assert "VALID CANDIDATE EVIDENCE IDS" in prompt
    assert "Never create a new operation_checks row" in prompt
    assert "Never assess, choose, correct, or mention candidate types/classes" in prompt
    assert "solvent" not in prompt
    assert "ChemicalInput" not in prompt
    assert "Add" not in prompt


@pytest.mark.asyncio
async def test_pre_extraction_semantic_exhaustion_preserves_final_valid_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.dumps(
        {
            "scope_resolution": {
                "completion_attestation": {
                    "target_located": True,
                    "all_references_resolved": True,
                    "all_modifications_applied": True,
                    "effective_workflow_complete": True,
                }
            },
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "Material A was added.",
                    "candidate_types": ["Add"],
                    "candidate_properties": {"hasAddedChemicalInput": "Material A"},
                }
            ],
        }
    )

    class FakeLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _prompt: str) -> str:
            self.calls += 1
            return candidate

    extraction_llm = FakeLLM()

    class FakeCreator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def setup_llm(self) -> FakeLLM:
            return extraction_llm

    audit_calls = 0

    async def reject_candidate(**_kwargs: object) -> list[str]:
        nonlocal audit_calls
        audit_calls += 1
        return [
            "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom for source "
            "`Material B was added.`"
        ]

    async def accept_type_selection(**_kwargs: object) -> list[str]:
        return []

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(extraction_module, "LLMCreator", FakeCreator)
    monkeypatch.setattr(
        extraction_module,
        "get_extraction_model",
        lambda _key: "test-model",
    )
    monkeypatch.setattr(
        extraction_module,
        "_run_closed_ledger_audit_panel",
        reject_candidate,
    )
    monkeypatch.setattr(
        extraction_module,
        "_run_type_selection_judge",
        accept_type_selection,
    )
    monkeypatch.setattr(extraction_module.asyncio, "sleep", no_sleep)

    result = await run_pre_extraction(
        doi_hash="case",
        entity_label="Target",
        entity_uri="urn:target",
        paper_content="Material A was added. Material B was added.",
        prompt_template="Return a closed ledger.",
        model_key="advanced_model",
        iter_num=3,
        data_dir=str(tmp_path),
        pre_extraction_validation={
            "closed_ledger": {
                "enabled": True,
                "nonblocking_after_semantic_exhaustion": True,
            }
        },
        max_retries=2,
    )

    assert json.loads(result) == json.loads(candidate)
    assert extraction_llm.calls == 2
    assert audit_calls == 2
    warning_path = (
        tmp_path
        / "case"
        / "responses"
        / "iter3_pre_extraction"
        / "Target.closed_ledger_warning.json"
    )
    warning = json.loads(warning_path.read_text(encoding="utf-8"))
    assert warning["kind"] == "semantic_audit_exhausted"
    assert warning["semantic_attempt_budget"] == 2


def test_semantic_hint_boundary_is_format_light_and_domain_neutral() -> None:
    rendered = _append_semantic_hint_output_boundary("Extract the workflow.")

    assert "SEMANTIC_HINTS_V1" in rendered
    assert "Do not output JSON" in rendered
    assert "active T-Box comments" in rendered
    assert "unresolved" in rendered
    assert "Copy lookup values only when the tool actually returned them" in rendered
    assert "ChemicalInput" not in rendered
    assert "washing/separation solvent" not in rendered


@pytest.mark.asyncio
async def test_semantic_extraction_uses_llm_audit_without_json_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLLM:
        async def ainvoke(self, prompt: str) -> str:
            if "independent evidence-coverage projection voter" in prompt:
                return json.dumps(
                    {
                        "operation_checks": [
                            {
                                "evidence_id": "E001",
                                "status": "complete",
                                "candidate_occurrences": ["Add DMF"],
                                "reason": "The introduction remains one Add occurrence.",
                            }
                        ]
                    }
                )
            return (
                "SEMANTIC_HINTS_V1\n"
                "1. Add DMF (4 mL) as a step-local process solvent."
            )

    class FakeCreator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def setup_llm(self) -> FakeLLM:
            return FakeLLM()

    monkeypatch.setattr(extraction_module, "LLMCreator", FakeCreator)
    monkeypatch.setattr(
        extraction_module,
        "get_extraction_model",
        lambda _key: "test-model",
    )
    monkeypatch.setattr(
        extraction_module,
        "judge_extraction_semantics",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("semantic-text MAIN must not call the broad judge")
        ),
    )
    hints_file = tmp_path / "runtime" / "doi" / "mcp_run" / "iter3_hints_x.txt"
    source_ledger = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "4 mL DMF was added.",
                    "candidate_types": ["Add"],
                    "candidate_properties": {"hasAmount": "4 mL"},
                }
            ]
        }
    )

    result = await run_extraction(
        doi_hash="doi",
        entity_label="example",
        entity_uri="https://example.test/synthesis",
        source_text=source_ledger,
        prompt_template="Extract {paper_content} for {entity_label}.",
        model_key="iter3_hints",
        hints_file=str(hints_file),
        iter_num=3,
        extraction_validation={
            "llm_tbox_contract_feedback": {"enabled": False}
        },
        enforce_closed_ledger_projection=True,
        hint_representation="semantic-text.v1",
    )

    assert result.startswith("SEMANTIC_HINTS_V1")
    assert json.loads(
        Path(f"{hints_file}.semantic_audit.json").read_text(encoding="utf-8")
    )["acceptance"]["accepted"]
    report = json.loads(
        Path(f"{hints_file}.semantic_audit.json").read_text(encoding="utf-8")
    )
    assert report["schema_version"] == (
        "closed-ledger-evidence-coverage-panel.v2"
    )
    assert report["vote_count"] == 3
    assert report["unresolved"] == []


@pytest.mark.asyncio
async def test_operation_projection_panel_blocks_only_unanimous_defect() -> None:
    source = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "The existing solution was moved.",
                    "candidate_types": ["Transfer"],
                    "candidate_properties": {},
                }
            ]
        }
    )
    missing_vote = json.dumps(
        {
            "operation_checks": [
                {
                    "evidence_id": "E001",
                    "status": "missing",
                    "candidate_occurrences": [],
                    "reason": "No candidate occurrence preserves this movement.",
                }
            ]
        }
    )

    class FakeAuditLlm:
        async def ainvoke(self, _prompt: str) -> str:
            return missing_vote

    report = await _run_operation_projection_panel(
        audit_llm=FakeAuditLlm(),
        original_prompt="Use the active T-Box.",
        source_text=source,
        candidate_text="SEMANTIC_HINTS_V1\nNo movement.",
    )

    assert report["acceptance"]["accepted"] is False
    assert report["blocking"][0]["consensus_status"] == "missing"
    assert report["unresolved"] == []


@pytest.mark.asyncio
async def test_operation_projection_disagreement_is_nonblocking_and_recorded() -> None:
    source = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "The material was introduced.",
                    "candidate_types": ["Add"],
                    "candidate_properties": {},
                }
            ]
        }
    )
    statuses = iter(["complete", "missing", "complete"])

    class FakeAuditLlm:
        async def ainvoke(self, _prompt: str) -> str:
            status = next(statuses)
            return json.dumps(
                {
                    "operation_checks": [
                        {
                            "evidence_id": "E001",
                            "status": status,
                            "candidate_occurrences": (
                                ["Add occurrence"] if status == "complete" else []
                            ),
                            "reason": f"Independent semantic vote: {status}.",
                        }
                    ]
                }
            )

    report = await _run_operation_projection_panel(
        audit_llm=FakeAuditLlm(),
        original_prompt="Use the active T-Box.",
        source_text=source,
        candidate_text="SEMANTIC_HINTS_V1\nOne Add occurrence.",
    )

    assert report["acceptance"]["accepted"] is True
    assert report["blocking"] == []
    assert report["unresolved"][0]["consensus_status"] == "unresolved"
    assert report["unresolved"][0]["vote_statuses"] == [
        "complete",
        "missing",
        "complete",
    ]


@pytest.mark.asyncio
async def test_operation_projection_allows_one_evidence_atom_to_map_to_many_occurrences() -> None:
    source = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "Several independently owned facts share this span.",
                    "candidate_types": ["OpaqueType"],
                    "candidate_properties": {},
                }
            ]
        }
    )

    class FakeAuditLlm:
        async def ainvoke(self, prompt: str) -> str:
            assert "not an authoritative declaration of output-occurrence cardinality" in prompt
            assert "Duplication is outside this judge's responsibility" in prompt
            return json.dumps(
                {
                    "operation_checks": [
                        {
                            "evidence_id": "E001",
                            "status": "complete",
                            "candidate_occurrences": [
                                "first supported occurrence",
                                "second supported occurrence",
                                "third supported occurrence",
                            ],
                            "reason": (
                                "The occurrences collectively preserve all facts in the "
                                "evidence atom."
                            ),
                        }
                    ]
                }
            )

    report = await _run_operation_projection_panel(
        audit_llm=FakeAuditLlm(),
        original_prompt="Use the active contract.",
        source_text=source,
        candidate_text="SEMANTIC_HINTS_V1\nThree supported occurrences.",
    )

    assert report["acceptance"]["accepted"] is True
    assert report["operation_checks"][0]["consensus_status"] == "complete"
    assert report["schema_version"] == "closed-ledger-evidence-coverage-panel.v2"


def test_kg_revision_rejects_retained_exact_invalid_relation() -> None:
    feedback = json.dumps(
        {
            "violations": [
                {
                    "subject_ref": "filter-1",
                    "property": "hasWashingSolvent",
                    "object_ref": "add-3",
                }
            ]
        }
    )
    invalid = json.dumps(
        {
            "entities": [],
            "relations": [
                {
                    "subject_ref": "filter-1",
                    "property": "hasWashingSolvent",
                    "object_ref": "add-3",
                }
            ],
        }
    )
    corrected = '{"entities": [], "relations": []}'

    assert _kg_revision_relation_errors(invalid, feedback)
    assert _kg_revision_relation_errors(corrected, feedback) == []


def test_declared_sub_iteration_file_is_written(tmp_path: Path) -> None:
    content = '{"entities": [], "relations": []}'
    written = _write_declared_sub_iteration_file(
        sub_outputs={
            "file_path": (
                "extracted_data/{entity_safe}/synthesis_steps_enriched.json"
            )
        },
        merged_hint_text=content,
        doi_hash="178ef569",
        entity_safe="Synthesis_of_MOP-MIA",
        data_dir=str(tmp_path),
    )

    expected = (
        tmp_path
        / "178ef569"
        / "extracted_data"
        / "Synthesis_of_MOP-MIA"
        / "synthesis_steps_enriched.json"
    )
    assert written is not None
    assert Path(written).resolve() == expected.resolve()
    assert expected.read_text(encoding="utf-8") == content


def test_downstream_source_uses_scoped_pre_extraction_and_iteration_input(
    tmp_path: Path,
) -> None:
    pre_dir = tmp_path / "hash" / "pre_extraction"
    pre_dir.mkdir(parents=True)
    (pre_dir / "entity_text_Target.txt").write_text(
        "scoped target evidence",
        encoding="utf-8",
    )

    source = _compose_downstream_iteration_source(
        data_dir=str(tmp_path),
        doi_hash="hash",
        entity_label="Target",
        iteration_input='{"entities": []}',
        fallback_source="full multi-entity paper",
    )

    assert "scoped target evidence" in source
    assert '{"entities": []}' in source
    assert "full multi-entity paper" not in source


def test_source_uses_scoped_pre_extraction_without_iteration_input(
    tmp_path: Path,
) -> None:
    pre_dir = tmp_path / "hash" / "pre_extraction"
    pre_dir.mkdir(parents=True)
    (pre_dir / "entity_text_Target.txt").write_text(
        "scoped target evidence",
        encoding="utf-8",
    )

    source = _compose_downstream_iteration_source(
        data_dir=str(tmp_path),
        doi_hash="hash",
        entity_label="Target",
        iteration_input="",
        fallback_source="full multi-entity paper",
    )

    assert source == "scoped target evidence"


def test_hint_validation_rejects_unresolved_and_lexical_self_relations() -> None:
    content = json.dumps(
        {
            "entities": [
                {
                    "ref": "Step-1",
                    "class": "HeatChill",
                    "label": "Heat",
                    "datatype_properties": {"hasTargetTemperature": "90 degC"},
                }
            ],
            "relations": [
                {
                    "subject_ref": "Step-1",
                    "property": "hasTargetTemperature",
                    "object_ref": "Step-1",
                },
                {
                    "subject_ref": "Step-1",
                    "property": "usesEquipment",
                    "object_ref": "Equipment-1",
                },
            ],
        }
    )

    ok, errors = validate_hint_payload(content)

    assert not ok
    assert any("self-link" in error for error in errors)
    assert any("unresolved ref: Equipment-1" in error for error in errors)


def test_ref_entity_hint_validation_uses_json_parser_not_prefix_detection() -> None:
    fenced = """```json
{"entities": [], "relations": []}
```"""

    with pytest.raises(ValueError, match="Invalid JSON extraction payload"):
        validate_hint_payload(
            fenced,
            expected_schema="ref-entity-relations.v1",
        )


def test_hint_validation_resolves_refs_from_nested_prior_registry() -> None:
    content = json.dumps(
        {
            "entities": [
                {
                    "ref": "step-1",
                    "class": "Add",
                    "label": "Add input",
                    "datatype_properties": {},
                }
            ],
            "relations": [
                {
                    "subject_ref": "step-1",
                    "property": "hasInput",
                    "object_ref": "input-1",
                }
            ],
        }
    )
    registry = json.dumps(
        {
            "schema_version": "enrichment-identity-registry.v1",
            "prior_iteration_registry": {
                "iterations": [
                    {
                        "payload": {
                            "entities": [
                                {
                                    "ref": "input-1",
                                    "class": "Input",
                                    "label": "Input",
                                    "datatype_properties": {},
                                }
                            ],
                            "relations": [],
                        }
                    }
                ]
            },
        }
    )

    ok, errors = validate_hint_payload(
        content,
        accumulated_hints=registry,
        expected_schema="ref-entity-relations.v1",
    )

    assert ok
    assert errors == []


def test_hint_validation_rejects_fabricated_absolute_entity_ref() -> None:
    content = json.dumps(
        {
            "entities": [
                {
                    "ref": "https://example.com/invented/input-1",
                    "class": "Input",
                    "label": "Input",
                    "datatype_properties": {},
                }
            ],
            "relations": [],
        }
    )

    ok, errors = validate_hint_payload(
        content,
        expected_schema="ref-entity-relations.v1",
    )

    assert not ok
    assert any("opaque ref" in error for error in errors)


def test_load_prompt_composes_deterministic_materializable_component(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "EXTRACTION_ITER_3.md"
    component = tmp_path / "EXTRACTION_ITER_3.materializable.inc"
    prompt.write_text("LLM-authored extraction instructions.\n", encoding="utf-8")
    component.write_text(
        "Materializable Hint Contract:\n- compiled row\n",
        encoding="utf-8",
    )

    rendered = load_prompt(str(prompt))

    assert rendered == (
        "LLM-authored extraction instructions.\n\n"
        "Materializable Hint Contract:\n- compiled row\n"
    )


def test_load_prompt_does_not_double_append_pre_tbox_splice(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "PRE_EXTRACTION_ITER_3.md"
    component = tmp_path / "PRE_EXTRACTION_ITER_3.materializable.inc"
    contract = "Scoped T-Box Contract (mechanically injected):\n- compiled row"
    prompt.write_text(
        "PRE ledger instructions.\n\n"
        "----- DETERMINISTIC T-BOX CONTRACT (mechanically spliced; do not edit) -----\n"
        f"{contract}\n"
        "----- END DETERMINISTIC T-BOX CONTRACT -----\n",
        encoding="utf-8",
    )
    component.write_text(contract + "\n", encoding="utf-8")

    rendered = load_prompt(str(prompt))

    assert rendered.count("compiled row") == 1


def test_structured_enrichment_merge_deduplicates_exact_relations() -> None:
    relation = {
        "subject_ref": "Step-1",
        "property": "hasInput",
        "object_ref": "Input-1",
    }

    merged_text = _merge_structured_hint_text(
        json.dumps({"relations": [relation]}),
        json.dumps({"relations": [dict(relation)]}),
        identity_keys=["ref"],
    )
    assert merged_text is not None
    merged = json.loads(merged_text)

    assert merged["relations"] == [relation]


def test_extraction_pipeline_appends_undeclared_source_channel() -> None:
    rendered = bind_runtime_context(
        "Extract T-Box-constrained facts.",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Source text.",
    )

    assert "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----" in rendered
    assert "Source text." in rendered
    assert "Document DOI/hash: paper-doi" in rendered
    assert "Current entity label: route" in rendered
    assert "Current entity exact URI: urn:route" in rendered


def test_extraction_pipeline_replaces_declared_channels_without_duplication() -> None:
    rendered = bind_runtime_context(
        "{entity_label} {entity_uri}\n{paper_content}\n{iteration_input}",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Source text.",
        iteration_input="Previous hints.",
    )

    assert rendered == "route urn:route\nSource text.\nPrevious hints."
    assert "PIPELINE-INJECTED" not in rendered


def test_extraction_pipeline_replaces_doi_and_avoids_identity_duplication() -> None:
    rendered = bind_runtime_context(
        "{doi} {entity_label} {entity_uri}\n{context}",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Source text.",
    )

    assert rendered == "paper-doi route urn:route\nSource text."
    assert "PIPELINE-INJECTED" not in rendered


def test_extraction_pipeline_injects_authoritative_identity_dossier() -> None:
    rendered = bind_runtime_context(
        "Extract this exact scope.",
        entity_label="route-specific synthesis",
        entity_uri="urn:route:1",
        source_text="Source text.",
        identity_dossier={
            "scope_index": 2,
            "source_anchor": "TopEntity-2 [route-specific synthesis]",
            "explicit_iteration_1_facts": [],
        },
    )

    assert "ENTITY IDENTITY DOSSIER: BEGIN" in rendered
    assert '"scope_index": 2' in rendered
    assert "do not infer missing identity facts" in rendered
    assert "Do not substitute, merge, or redirect" in rendered


def test_enrichment_style_binding_includes_exact_uri_and_inputs_once() -> None:
    rendered = bind_runtime_context(
        "Enrich the current entity.",
        doi_hash="paper-doi",
        entity_label="route",
        entity_uri="urn:route",
        source_text="Entity source.",
        iteration_input="Base hints.",
    )

    assert rendered.count("urn:route") == 1
    assert rendered.count("Entity source.") == 1
    assert rendered.count("Base hints.") == 1


def test_extraction_pipeline_injects_prior_hints_as_identity_registry() -> None:
    rendered = bind_runtime_context(
        "Extract this stage.",
        entity_label="CS-1",
        entity_uri="https://example.com/cs-1",
        source_text="Source text.",
        accumulated_hints='{"ChemicalOutput":{"label":"MOP-Alpha"}}',
    )

    assert "ACCUMULATED PRIOR HINTS: BEGIN" in rendered
    assert "read-only semantic identity registry" in rendered
    assert "Do not re-emit an entity" in rendered
    assert rendered.count('"label":"MOP-Alpha"') == 1


def test_prior_hint_loader_reads_only_earlier_iterations(tmp_path: Path) -> None:
    hints_dir = tmp_path / "case" / "mcp_run"
    hints_dir.mkdir(parents=True)
    (hints_dir / "iter2_hints_CS-1.txt").write_text(
        '{"ChemicalOutput":{"label":"MOP-Alpha"}}',
        encoding="utf-8",
    )
    (hints_dir / "iter3_hints_CS-1.txt").write_text(
        '{"Add":[{"label":"Add-1"}]}',
        encoding="utf-8",
    )
    iterations = [
        {
            "iteration_number": 2,
            "outputs": {
                "hints_file": "mcp_run/iter2_hints_{entity_safe}.txt"
            },
        },
        {
            "iteration_number": 3,
            "outputs": {
                "hints_file": "mcp_run/iter3_hints_{entity_safe}.txt"
            },
        },
    ]

    content, paths = _load_accumulated_prior_hints(
        iterations=iterations,
        current_iteration=3,
        doi_hash="case",
        entity_safe="CS-1",
        data_dir=str(tmp_path),
    )

    registry = json.loads(content)
    assert registry["schema_version"] == "accumulated-prior-hints.v1"
    assert registry["iterations"] == [
        {
            "iteration_number": 2,
            "payload": {"ChemicalOutput": {"label": "MOP-Alpha"}},
        }
    ]
    assert [Path(path) for path in paths] == [
        hints_dir / "iter2_hints_CS-1.txt"
    ]


def test_prior_hint_loader_carries_semantic_ledger_forward(
    tmp_path: Path,
) -> None:
    hints_dir = tmp_path / "case" / "mcp_run"
    hints_dir.mkdir(parents=True)
    semantic = "SEMANTIC_HINTS_V1\n1. Add DMF as process solvent."
    (hints_dir / "iter3_hints_CS-1.txt").write_text(
        semantic, encoding="utf-8"
    )

    content, _ = _load_accumulated_prior_hints(
        iterations=[
            {
                "iteration_number": 3,
                "hint_representation": "semantic-text.v1",
                "outputs": {
                    "hints_file": "mcp_run/iter3_hints_{entity_safe}.txt"
                },
            }
        ],
        current_iteration=4,
        doi_hash="case",
        entity_safe="CS-1",
        data_dir=str(tmp_path),
    )

    payload = json.loads(content)["iterations"][0]["payload"]
    assert payload["hint_representation"] == "semantic-text.v1"
    assert payload["semantic_ledger"] == semantic


def test_tbox_contract_audit_prompt_is_domain_independent() -> None:
    prompt = _build_tbox_contract_audit_prompt(
        original_prompt=(
            "- Stage-owned classes: Container, WaitAction\n"
            "- `Container` -> `create_Container` accepts fields: `label`\n"
            "- `WaitAction` -> `create_WaitAction` accepts fields: `label`, `isWait`"
        ),
        source_text="The operation waits.",
        candidate_text='{"Container":{"label":"hold","isWait":true}}',
    )

    assert "The operation waits." in prompt
    assert '"isWait":true' in prompt
    assert "`Container` -> `create_Container`" in prompt
    assert "PASS A — accepted numeric scalar-value coverage" in prompt
    assert "PASS B — mechanical field placement" in prompt
    assert "isWait" in prompt


def test_tbox_contract_audit_parser_normalizes_evidenced_violation() -> None:
    source = "Widget W uses 3 × 4 units."
    contract = (
        "- Entity class `Widget` -> `datatype_properties` accepts: `count`\n"
        "  - Field `count` semantic contract: Records the exact repeated unit count.\n"
        "- Entity class `Action` -> `datatype_properties` accepts: `isWait`\n"
    )
    candidate = '{"Widget":{"label":"W","isWait":true}}'
    accepted, feedback = _parse_tbox_contract_audit(
        json.dumps(
            {
                "coverage_checks": [
                    {
                        "class": "Widget",
                        "field": "count",
                        "entity": "W",
                        "value": "3 × 4 units",
                        "source_evidence": "Widget W uses 3 × 4 units.",
                        "contract_evidence": "Records the exact repeated unit count.",
                        "present_in_candidate": False,
                    }
                ],
                "field_violations": [
                    {
                        "class": "Widget",
                        "field": "isWait",
                        "owner_class": "Action",
                        "contract_evidence": "Action accepts isWait.",
                    }
                ],
            }
        ),
        source_text=source,
        contract_text=contract,
        candidate_text=candidate,
    )

    assert accepted is False
    assert feedback == [
        "MISSING_ACCEPTED_SOURCE_VALUE: `Widget.count` for `W` "
        "must preserve `3 × 4 units` [source: Widget W uses 3 × 4 units.] "
        "[contract: Records the exact repeated unit count.]",
        "FIELD_NOT_ACCEPTED_BY_CLASS: `Widget.isWait` is not accepted; "
        "move it to `Action` [contract: Action accepts isWait.]",
    ]


def test_tbox_contract_audit_trusts_llm_coverage_verdict_without_substring_filter() -> None:
    accepted, feedback = _parse_tbox_contract_audit(
        json.dumps(
            {
                "coverage_checks": [
                    {
                        "class": "ChemicalSynthesis",
                        "field": "hasYield",
                        "entity": "route-1",
                        "value": "3 mL",
                        "source_evidence": "The input was mixed with 3 mL solvent.",
                        "contract_evidence": (
                            "Entity class `ChemicalSynthesis` -> "
                            "`datatype_properties` accepts: `hasYield`"
                        ),
                        "present_in_candidate": False,
                    }
                ],
                "field_violations": [],
            }
        ),
        source_text="The input was mixed with 3 mL solvent.",
        contract_text=(
            "- Entity class `ChemicalSynthesis` -> `datatype_properties` accepts: "
            "`hasYield`\n"
            "  - Field `hasYield` semantic contract: Links a synthesis to the "
            "explicitly reported yield value.\n"
        ),
        candidate_text=(
            '{"entities":[{"ref":"route-1","class":"ChemicalSynthesis",'
            '"label":"route-1"}]}'
        ),
    )

    assert accepted is False
    assert feedback[0].startswith("MISSING_ACCEPTED_SOURCE_VALUE:")


def test_tbox_contract_audit_trusts_llm_field_violation_without_keyword_filter() -> None:
    accepted, feedback = _parse_tbox_contract_audit(
        json.dumps(
            {
                "coverage_checks": [],
                "field_violations": [
                    {
                        "class": "Supplier",
                        "field": "label",
                        "owner_class": "",
                        "contract_evidence": (
                            "Entity class `Supplier` -> "
                            "`datatype_properties` accepts: none"
                        ),
                    }
                ],
            }
        ),
        contract_text=(
            "- Entity class `Supplier` -> `datatype_properties` accepts: none"
        ),
        candidate_text=(
            '{"entities":[{"ref":"supplier-1","class":"Supplier",'
            '"label":"Acme","datatype_properties":{}}]}'
        ),
    )

    assert accepted is False
    assert feedback == [
        "FIELD_NOT_ACCEPTED_BY_CLASS: `Supplier.label` is not accepted "
        "[contract: Entity class `Supplier` -> `datatype_properties` accepts: none]"
    ]


def test_tbox_contract_audit_rejects_schema_invalid_empty_evidence() -> None:
    with pytest.raises(ValueError, match="empty required values"):
        _parse_tbox_contract_audit(
            json.dumps(
                {
                    "coverage_checks": [
                        {
                            "class": "Widget",
                            "field": "count",
                            "entity": "W",
                            "value": "3 units",
                            "source_evidence": "",
                            "contract_evidence": "Widget accepts count.",
                            "present_in_candidate": False,
                        }
                    ],
                    "field_violations": [],
                }
            )
        )


def test_tbox_contract_audit_does_not_override_llm_scope_with_text_matching() -> None:
    accepted, feedback = _parse_tbox_contract_audit(
        json.dumps(
            {
                "coverage_checks": [
                    {
                        "class": "ChemicalSynthesis",
                        "field": "hasYield",
                        "entity": "Other synthesis",
                        "value": "47%",
                        "source_evidence": "Other synthesis gave a yield of 47%.",
                        "contract_evidence": "ChemicalSynthesis accepts hasYield.",
                        "present_in_candidate": False,
                    }
                ],
                "field_violations": [],
            }
        ),
        source_text="Other synthesis gave a yield of 47%.",
        contract_text=(
            "Materializable Hint Contract:\n"
            "- Entity class `ChemicalInput` -> `datatype_properties` accepts: "
            "`hasAmount`\n"
            "- Relation `hasChemicalInput`: `subject_ref` class "
            "`ChemicalSynthesis` -> `object_ref` class `ChemicalInput`"
        ),
        candidate_text='{"entities": []}',
    )

    assert accepted is False
    assert feedback[0].startswith("MISSING_ACCEPTED_SOURCE_VALUE:")


def test_tbox_contract_audit_does_not_match_candidate_entities_lexically() -> None:
    accepted, feedback = _parse_tbox_contract_audit(
        json.dumps(
            {
                "coverage_checks": [
                    {
                        "class": "ChemicalInput",
                        "field": "hasAmount",
                        "entity": "input from another synthesis",
                        "value": "0.35 mmol",
                        "source_evidence": "input from another synthesis (0.35 mmol)",
                        "contract_evidence": "ChemicalInput accepts hasAmount.",
                        "present_in_candidate": False,
                    }
                ],
                "field_violations": [],
            }
        ),
        source_text="input from another synthesis (0.35 mmol)",
        contract_text=(
            "- Entity class `ChemicalInput` -> `datatype_properties` accepts: "
            "`hasAmount`"
        ),
        candidate_text=json.dumps(
            {
                "entities": [
                    {
                        "ref": "input-1",
                        "class": "ChemicalInput",
                        "label": "current input",
                        "datatype_properties": {},
                    }
                ],
                "relations": [],
            }
        ),
    )

    assert accepted is False
    assert feedback[0].startswith("MISSING_ACCEPTED_SOURCE_VALUE:")


def test_structured_hint_merge_infers_identity_without_domain_keys() -> None:
    base = {
        "records": [
            {"slot": 1, "kind": "A", "value": "old"},
            {"slot": 2, "kind": "B", "value": "keep"},
        ]
    }
    update = {
        "records": [
            {"slot": 1, "kind": "A", "value": "new"},
            {"slot": 3, "kind": "C", "value": "append"},
        ]
    }

    assert _merge_structured_hint_payloads(
        base,
        update,
        identity_keys=["slot"],
    ) == {
        "records": [
            {"slot": 1, "kind": "A", "value": "new"},
            {"slot": 2, "kind": "B", "value": "keep"},
            {"slot": 3, "kind": "C", "value": "append"},
        ]
    }


def test_structured_hint_merge_appends_when_no_identity_is_inferable() -> None:
    assert _merge_structured_hint_payloads(
        [{"kind": "same"}],
        [{"kind": "same"}],
    ) == [{"kind": "same"}, {"kind": "same"}]


def test_frozen_pre_extraction_checkpoint_is_loaded_verbatim(
    tmp_path: Path,
) -> None:
    checkpoint = (
        tmp_path
        / "case"
        / "pre_extraction"
        / "entity_text_CS-1.txt"
    )
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("Frozen evidence.\n", encoding="utf-8")

    loaded = _load_reused_pre_extraction(
        data_dir=str(tmp_path),
        doi_hash="case",
        entity_label="CS-1",
    )

    assert loaded == "Frozen evidence.\n"


def test_frozen_pre_extraction_checkpoint_is_required(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="checkpoint is missing"):
        _load_reused_pre_extraction(
            data_dir=str(tmp_path),
            doi_hash="case",
            entity_label="CS-1",
        )


def test_closed_ledger_shape_requires_verbatim_sequential_evidence() -> None:
    source = "The solution was heated to 120 degC and cooled to room temperature."
    candidate = {
        "scope_resolution": {
            "completion_attestation": {
                "target_located": True,
                "all_references_resolved": True,
                "all_modifications_applied": True,
                "effective_workflow_complete": True,
            }
        },
        "evidence": [
            {
                "evidence_id": "E001",
                "source_order": 1,
                "ordering_cue": "",
                "verbatim_quote": "The solution was heated to 120 degC",
                "candidate_types": ["HeatChill"],
                "candidate_properties": {},
            }
        ],
    }

    assert _validate_closed_ledger_shape(json.dumps(candidate), source) == []
    candidate["evidence"][0]["source_order"] = 2
    assert "source_order must be 1" in "\n".join(
        _validate_closed_ledger_shape(json.dumps(candidate), source)
    )


def test_closed_ledger_audit_returns_grounded_missing_operation_feedback() -> None:
    source = "The solution was heated to 120 degC and cooled to room temperature."
    candidate = json.dumps(
        {
            "scope_resolution": {},
            "evidence": [
                {
                    "evidence_id": "E001",
                    "source_order": 1,
                    "verbatim_quote": "heated to 120 degC",
                    "candidate_types": ["HeatChill"],
                    "candidate_properties": {},
                }
            ],
        }
    )
    audit = json.dumps(
        {
            "operation_checks": [
                {
                    "source_evidence": "heated to 120 degC",
                    "operation": "heating",
                    "status": "covered",
                    "candidate_evidence_id": "E001",
                    "reason": "represented by E001",
                },
                {
                    "source_evidence": "cooled to room temperature",
                    "operation": "cooling",
                    "status": "missing",
                    "candidate_evidence_id": None,
                    "reason": "no cooling atom",
                },
            ],
            "non_type_violations": [],
        }
    )

    feedback = _parse_closed_ledger_audit(
        audit,
        source_text=source,
        candidate_text=candidate,
    )

    assert feedback == [
        "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom "
        "for source `cooled to room temperature`"
    ]
    prompt = _build_closed_ledger_audit_prompt(
        original_prompt="closed ledger",
        source_text=source,
        candidate_text=candidate,
    )
    assert "NOT a type-selection judge" in prompt
    assert "separate independent" in prompt
    assert "ATOMIC EXPECTATION LEDGER" in prompt
    assert "one-to-one comparison" in prompt


def test_closed_ledger_audit_does_not_override_llm_with_substring_matching() -> None:
    candidate = json.dumps({"evidence": []})
    audit = json.dumps(
        {
            "operation_checks": [
                {
                    "source_evidence": "OCR-equivalent semantic span",
                    "operation": "operation identified by the LLM",
                    "status": "missing",
                    "candidate_evidence_id": None,
                    "reason": "No corresponding evidence atom exists.",
                }
            ],
            "non_type_violations": [],
        }
    )

    assert _parse_closed_ledger_audit(
        audit,
        source_text="different literal characters",
        candidate_text=candidate,
    ) == [
        "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom "
        "for source `OCR-equivalent semantic span`"
    ]


def test_closed_ledger_audit_rejects_schema_invalid_ambiguous_rows() -> None:
    source = "The crystals were washed with DMF."
    candidate = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "verbatim_quote": source,
                    "candidate_types": ["Filter"],
                }
            ]
        }
    )
    audit = json.dumps(
        {
            "operation_checks": [
                {
                    "source_evidence": source,
                    "expected_type": "Separate|Filter",
                    "status": "missing",
                },
                {
                    "source_evidence": source,
                    "expected_type": "Stir",
                    "status": "missing",
                    "reason": "Only if this wording were interpreted as stirring.",
                },
            ],
            "non_type_violations": [
                {
                    "candidate_evidence_id": "E001",
                    "dimension": "property_fidelity",
                    "code": "WASHING_AS_ADD",
                    "is_violation": False,
                    "source_evidence": source,
                    "message": "Filter is correctly used; no violation.",
                },
                {
                    "candidate_evidence_id": "E001",
                    "dimension": "property_fidelity",
                    "code": "WASHING_AS_ADD",
                    "source_evidence": source,
                    "message": "Hypothetical error without the required assertion.",
                },
                {
                    "candidate_evidence_id": "E001",
                    "dimension": "property_fidelity",
                    "code": "WASHING_AS_ADD",
                    "is_violation": True,
                    "source_evidence": source,
                    "message": "Filter is correct; no violation is present.",
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="keys differ from the required schema"):
        _parse_closed_ledger_audit(
            audit,
            source_text=source,
            candidate_text=candidate,
        )


def test_closed_ledger_shape_does_not_make_lexical_grounding_decisions() -> None:
    candidate = {
        "scope_resolution": {
            "completion_attestation": {
                "target_located": True,
                "all_references_resolved": True,
                "all_modifications_applied": True,
                "effective_workflow_complete": True,
            }
        },
        "evidence": [
            {
                "evidence_id": "E001",
                "source_order": 1,
                "verbatim_quote": "semantically judged quote",
                "candidate_types": ["Add"],
                "candidate_properties": {},
            }
        ],
    }

    assert _validate_closed_ledger_shape(
        json.dumps(candidate),
        "different source wording",
    ) == []


def test_closed_ledger_audit_trusts_structured_non_type_violation() -> None:
    source = "Blue crystals grew within 3-5 days."
    candidate = json.dumps(
        {
            "evidence": [
                {
                    "evidence_id": "E003",
                    "verbatim_quote": source,
                    "candidate_types": ["Stir"],
                }
            ]
        }
    )
    audit = json.dumps(
        {
            "operation_checks": [
                {
                    "source_evidence": source,
                    "operation": "crystal growth",
                    "status": "covered",
                    "candidate_evidence_id": "E003",
                    "reason": "The candidate attempted to represent this operation.",
                }
            ],
            "non_type_violations": [
                {
                    "candidate_evidence_id": "E003",
                    "dimension": "grounding",
                    "code": "UNSUPPORTED_CLAIM",
                    "is_violation": True,
                    "source_evidence": source,
                    "message": "The candidate adds a claim not grounded by the quote.",
                }
            ],
        }
    )

    assert _parse_closed_ledger_audit(
        audit,
        source_text=source,
        candidate_text=candidate,
    ) == [
        "LEDGER_GROUNDING[UNSUPPORTED_CLAIM] `E003`: "
        "The candidate adds a claim not grounded by the quote. "
        "[source: Blue crystals grew within 3-5 days.]"
    ]


def test_closed_ledger_audit_prompt_assigns_compact_tuple_grounding_to_llm() -> None:
    prompt = _build_closed_ledger_audit_prompt(
        original_prompt="closed ledger",
        source_text="A/B/C (1:2:3 mL)",
        candidate_text='{"evidence":[]}',
    )

    assert "Multiple evidence atoms may cite the same complete source span" in prompt
    assert "keep interpreted per-component values in candidate_properties" in prompt
