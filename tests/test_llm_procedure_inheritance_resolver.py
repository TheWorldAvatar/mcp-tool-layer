import copy
import json
import re
import threading
from pathlib import Path

import pytest

import src.pipelines.main_ontology_extractions.extract as extraction_module
from src.agents.scripts_and_prompts_generation.level1_code_repair import LLMJsonResult
from src.agents.scripts_and_prompts_generation.llm_procedure_inheritance_resolver import (
    BRIEF_BEGIN,
    RECONCILIATION_SCHEMA_VERSION,
    _invoke_vote,
    aggregate_inheritance_votes,
    build_inheritance_vote_prompt,
    render_procedure_inheritance_brief,
    resolve_procedure_inheritance,
    validate_inheritance_vote,
)
from src.pipelines.main_ontology_extractions.extract import (
    _inject_procedure_inheritance_brief,
    _run_inheritance_micro_audit_panel,
    run_extraction,
    run_pre_extraction,
)


TARGET = "urn:procedure:target"
BASE = "urn:procedure:base"


def test_inheritance_prompt_excludes_referenced_input_creation_history() -> None:
    prompt = build_inheritance_vote_prompt(
        source_text="Target uses a pre-existing input whose creation is described elsewhere.",
        target_procedure_ref=TARGET,
        target_procedure_label="Target procedure",
        tbox_contract="Generic contract",
    )

    assert "does not instruct the target to reuse that workflow" in prompt
    assert "is not procedure inheritance" in prompt
    assert "following that method" in prompt


def test_schema_repair_uses_higher_temperature_after_first_failure() -> None:
    valid_vote = _vote(inheritance_present=False)
    calls: list[float] = []

    def fake_invoke(
        _model: str,
        _prompt: str,
        **kwargs: object,
    ) -> LLMJsonResult:
        calls.append(float(kwargs["temperature"]))
        data = copy.deepcopy(valid_vote)
        if len(calls) == 1:
            data["schema_version"] = "invalid"
        return LLMJsonResult(data=data, elapsed_seconds=0.0, token_usage={})

    vote, _ = _invoke_vote(
        invoke=fake_invoke,
        model="judge",
        prompt="prompt",
        target_procedure_ref=TARGET,
    )

    assert vote["inheritance_present"] is False
    assert calls == [0.0, 0.3]


def _atom(
    atom_id: str,
    order: int,
    operation: str,
    *,
    origin: str = BASE,
    material_identity: str | None = None,
    amount: str | None = None,
    role: str | None = None,
    mixture_group_id: str | None = None,
    qualifiers: list[dict] | None = None,
    modifications: list[str] | None = None,
) -> dict:
    return {
        "atom_id": atom_id,
        "order": order,
        "owner_ref": TARGET,
        "operation": operation,
        "source_evidence": f"semantic evidence for {atom_id}",
        "origin_ref": origin,
        "occurrence_payload": {
            "occurrence_id": atom_id,
            "material_identity": material_identity,
            "amount": amount,
            "role": role,
            "qualifiers": qualifiers or [],
            "mixture_group_id": mixture_group_id,
        },
        "applied_modification_ids": modifications or [],
    }


def _vote(
    *,
    effective: list[dict] | None = None,
    modifications: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    base_workflows: list[dict] | None = None,
    unresolved: list[str] | None = None,
    inheritance_present: bool = True,
) -> dict:
    return {
        "schema_version": "procedure-inheritance-v2",
        "target": {
            "procedure_ref": TARGET,
            "source_evidence": "target procedure evidence",
        },
        "inheritance_present": inheritance_present,
        "dependencies": dependencies
        if dependencies is not None
        else (
            [
                {
                    "dependency_id": "D001",
                    "referencing_procedure_ref": TARGET,
                    "base_procedure_ref": BASE,
                    "source_evidence": "target refers to base",
                }
            ]
            if inheritance_present
            else []
        ),
        "base_workflows": base_workflows
        if base_workflows is not None
        else (
            [
                {
                    "base_procedure_ref": BASE,
                    "atoms": [_atom("B001", 1, "base operation")],
                }
            ]
            if inheritance_present
            else []
        ),
        "modifications": modifications or [],
        "effective_workflow": effective
        if effective is not None
        else ([_atom("W001", 1, "base operation")] if inheritance_present else []),
        "unresolved_references": unresolved or [],
        "rationale": "independent semantic resolution",
    }


def _modification(
    modification_id: str,
    kind: str,
    target_ids: list[str],
    replacements: list[dict],
) -> dict:
    return {
        "modification_id": modification_id,
        "kind": kind,
        "target_atom_ids": target_ids,
        "replacement_atoms": replacements,
        "source_evidence": f"semantic modification evidence {modification_id}",
    }


def test_replacement_atom_orders_are_normalized_per_modification() -> None:
    vote = _vote(
        modifications=[
            _modification(
                "M001",
                "replace",
                ["B001"],
                [
                    _atom("R001", 7, "first replacement", origin=TARGET),
                    _atom("R002", 11, "second replacement", origin=TARGET),
                ],
            )
        ]
    )

    def fake_invoke(
        _model: str,
        _prompt: str,
        **_kwargs: object,
    ) -> LLMJsonResult:
        return LLMJsonResult(
            data=copy.deepcopy(vote),
            elapsed_seconds=0.0,
            token_usage={},
        )

    normalized, _ = _invoke_vote(
        invoke=fake_invoke,
        model="judge",
        prompt="prompt",
        target_procedure_ref=TARGET,
    )

    assert [
        atom["order"]
        for atom in normalized["modifications"][0]["replacement_atoms"]
    ] == [1, 2]
    assert vote["modifications"][0]["replacement_atoms"][0]["order"] == 7


@pytest.mark.parametrize(
    ("fixture_name", "vote"),
    [
        (
            "complete_inheritance",
            _vote(
                effective=[
                    _atom("W001", 1, "base operation"),
                    _atom("W002", 2, "second base operation"),
                ],
                base_workflows=[
                    {
                        "base_procedure_ref": BASE,
                        "atoms": [
                            _atom("B001", 1, "base operation"),
                            _atom("B002", 2, "second base operation"),
                        ],
                    }
                ],
            ),
        ),
        (
            "single_replacement",
            _vote(
                effective=[
                    _atom(
                        "W001",
                        1,
                        "replacement operation",
                        origin=TARGET,
                        modifications=["M001"],
                    )
                ],
                modifications=[
                    _modification(
                        "M001",
                        "replace",
                        ["B001"],
                        [_atom("R001", 1, "replacement operation", origin=TARGET)],
                    )
                ],
            ),
        ),
        (
            "insert_and_delete",
            _vote(
                effective=[
                    _atom(
                        "W001",
                        1,
                        "inserted operation",
                        origin=TARGET,
                        modifications=["M001"],
                    )
                ],
                modifications=[
                    _modification(
                        "M001",
                        "insert",
                        [],
                        [_atom("I001", 1, "inserted operation", origin=TARGET)],
                    ),
                    _modification("M002", "delete", ["B001"], []),
                ],
            ),
        ),
        (
            "explicit_mixture",
            _vote(
                effective=[
                    _atom(
                        "W001",
                        1,
                        "Add",
                        material_identity="DMF",
                        amount="4 mL",
                        role="process solvent",
                        mixture_group_id="MIX001",
                    ),
                    _atom(
                        "W002",
                        2,
                        "Add",
                        material_identity="MeOH",
                        amount="6 mL",
                        role="process solvent",
                        mixture_group_id="MIX001",
                    ),
                ]
            ),
        ),
        (
            "multi_layer_reference",
            _vote(
                dependencies=[
                    {
                        "dependency_id": "D001",
                        "referencing_procedure_ref": TARGET,
                        "base_procedure_ref": BASE,
                        "source_evidence": "target refers to base",
                    },
                    {
                        "dependency_id": "D002",
                        "referencing_procedure_ref": TARGET,
                        "base_procedure_ref": "urn:procedure:ancestor",
                        "source_evidence": "recursive dependency evidence",
                    },
                ],
                base_workflows=[
                    {
                        "base_procedure_ref": BASE,
                        "atoms": [_atom("B001", 1, "base operation")],
                    },
                    {
                        "base_procedure_ref": "urn:procedure:ancestor",
                        "atoms": [
                            _atom(
                                "A001",
                                1,
                                "ancestor operation",
                                origin="urn:procedure:ancestor",
                            )
                        ],
                    },
                ],
                effective=[
                    _atom(
                        "W001",
                        1,
                        "ancestor operation",
                        origin="urn:procedure:ancestor",
                    ),
                    _atom("W002", 2, "base operation"),
                ],
            ),
        ),
    ],
)
def test_generic_inheritance_fixtures_resolve_by_exact_panel_consensus(
    fixture_name: str,
    vote: dict,
) -> None:
    resolution = aggregate_inheritance_votes(
        [copy.deepcopy(vote) for _ in range(3)],
        target_procedure_ref=TARGET,
        target_procedure_label=fixture_name,
    )

    assert resolution["status"] == "resolved"
    assert resolution["effective_workflow"] == vote["effective_workflow"]
    assert BRIEF_BEGIN in render_procedure_inheritance_brief(resolution)


def test_vote_schema_rejects_extra_fields() -> None:
    vote = _vote()
    vote["content_keyword_guess"] = True

    with pytest.raises(ValueError, match="keys differ"):
        validate_inheritance_vote(vote, target_procedure_ref=TARGET)


def test_occurrence_payload_requires_identity_amount_role_co_location() -> None:
    vote = _vote(
        effective=[
            _atom(
                "W001",
                1,
                "Add",
                material_identity="DMF",
                amount="4 mL",
                role="process solvent",
            )
        ]
    )
    validated = validate_inheritance_vote(vote, target_procedure_ref=TARGET)
    occurrence = validated["effective_workflow"][0]["occurrence_payload"]
    assert occurrence == {
        "occurrence_id": "W001",
        "material_identity": "DMF",
        "amount": "4 mL",
        "role": "process solvent",
        "qualifiers": [],
        "mixture_group_id": None,
    }

    invalid = copy.deepcopy(vote)
    invalid["effective_workflow"][0]["occurrence_payload"].pop("amount")
    with pytest.raises(ValueError, match="occurrence_payload keys"):
        validate_inheritance_vote(invalid, target_procedure_ref=TARGET)


def test_material_amount_requires_material_identity() -> None:
    invalid = _vote()
    invalid["effective_workflow"][0]["occurrence_payload"]["amount"] = "30 min"

    with pytest.raises(ValueError, match="amount requires material_identity"):
        validate_inheritance_vote(invalid, target_procedure_ref=TARGET)


def test_effective_atoms_are_owned_by_exact_target() -> None:
    invalid = _vote()
    invalid["effective_workflow"][0]["owner_ref"] = BASE

    with pytest.raises(ValueError, match="owned by target_procedure_ref"):
        validate_inheritance_vote(invalid, target_procedure_ref=TARGET)


def test_insert_modification_has_no_fabricated_anchor() -> None:
    invalid = _vote(
        modifications=[
            _modification(
                "M001",
                "insert",
                ["B001"],
                [_atom("R001", 1, "inserted operation", origin=TARGET)],
            )
        ]
    )

    with pytest.raises(ValueError, match="insert requires no target_atom_ids"):
        validate_inheritance_vote(invalid, target_procedure_ref=TARGET)


def test_explicit_mixture_uses_separate_occurrence_atoms() -> None:
    atoms = [
        _atom(
            "W001",
            1,
            "Add",
            material_identity="DMF",
            amount="4 mL",
            role="process solvent",
            mixture_group_id="MIX001",
        ),
        _atom(
            "W002",
            2,
            "Add",
            material_identity="MeOH",
            amount="6 mL",
            role="process solvent",
            mixture_group_id="MIX001",
        ),
    ]
    resolution = aggregate_inheritance_votes(
        [_vote(effective=copy.deepcopy(atoms)) for _ in range(3)],
        target_procedure_ref=TARGET,
    )

    assert [atom["occurrence_payload"]["material_identity"] for atom in atoms] == [
        "DMF",
        "MeOH",
    ]
    assert [atom["occurrence_payload"]["amount"] for atom in atoms] == [
        "4 mL",
        "6 mL",
    ]
    assert len(resolution["effective_workflow"]) == 2


def test_no_inheritance_is_noop() -> None:
    vote = _vote(inheritance_present=False)
    resolution = aggregate_inheritance_votes(
        [copy.deepcopy(vote) for _ in range(3)],
        target_procedure_ref=TARGET,
    )

    assert resolution["status"] == "no_inheritance"
    assert render_procedure_inheritance_brief(resolution) == ""


def test_true_inheritance_requires_procedure_dependency() -> None:
    vote = _vote(
        inheritance_present=True,
        dependencies=[],
        base_workflows=[],
        modifications=[],
        effective=[],
    )

    with pytest.raises(ValueError, match="procedure-to-procedure dependency"):
        validate_inheritance_vote(vote, target_procedure_ref=TARGET)


def test_global_context_cannot_self_activate_inheritance() -> None:
    vote = _vote(
        dependencies=[
            {
                "dependency_id": "D001",
                "referencing_procedure_ref": TARGET,
                "base_procedure_ref": TARGET,
                "source_evidence": (
                    "All procedures were performed under an inert atmosphere."
                ),
            }
        ],
    )

    with pytest.raises(ValueError, match="distinct referenced procedure"):
        validate_inheritance_vote(vote, target_procedure_ref=TARGET)


def test_ambiguous_reference_disagreement_is_fail_closed() -> None:
    first = _vote()
    second = _vote(
        dependencies=[
            {
                "dependency_id": "D001",
                "referencing_procedure_ref": TARGET,
                "base_procedure_ref": "urn:procedure:other",
                "source_evidence": "ambiguous semantic reference",
            }
        ]
    )
    resolution = aggregate_inheritance_votes(
        [first, copy.deepcopy(first), second],
        target_procedure_ref=TARGET,
    )

    assert resolution["status"] == "unresolved"
    assert resolution["effective_workflow"] == []
    assert render_procedure_inheritance_brief(resolution) == ""


def test_mechanical_aggregation_does_not_compare_evidence_text() -> None:
    first = _vote()
    paraphrased = copy.deepcopy(first)
    paraphrased["dependencies"][0]["source_evidence"] = (
        "different characters with equivalent-looking meaning"
    )
    paraphrased["effective_workflow"][0]["source_evidence"] = (
        "independently quoted evidence wording"
    )
    paraphrased["base_workflows"][0]["atoms"][0]["source_evidence"] = (
        "another evidence rendering"
    )

    resolution = aggregate_inheritance_votes(
        [first, copy.deepcopy(first), paraphrased],
        target_procedure_ref=TARGET,
    )

    assert resolution["status"] == "resolved"
    assert resolution["dependencies"] == first["dependencies"]


def test_structured_occurrence_disagreement_is_fail_closed() -> None:
    first = _vote(
        effective=[
            _atom(
                "W001",
                1,
                "Add",
                material_identity="DMF",
                amount="4 mL",
                role="process solvent",
            )
        ]
    )
    disagreement = copy.deepcopy(first)
    disagreement["effective_workflow"][0]["occurrence_payload"]["amount"] = "6 mL"

    resolution = aggregate_inheritance_votes(
        [first, copy.deepcopy(first), disagreement],
        target_procedure_ref=TARGET,
    )

    assert resolution["status"] == "unresolved"
    assert resolution["effective_workflow"] == []


def test_resolution_cache_is_idempotent(tmp_path: Path) -> None:
    vote = _vote()
    call_count = 0
    lock = threading.Lock()
    prompts: list[str] = []

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        nonlocal call_count
        with lock:
            call_count += 1
            prompts.append(prompt)
        return LLMJsonResult(
            data=copy.deepcopy(vote),
            elapsed_seconds=0.0,
            token_usage={},
        )

    kwargs = {
        "source_text": "domain-neutral source",
        "target_procedure_ref": TARGET,
        "target_procedure_label": "target",
        "tbox_contract": {"classes": []},
        "model": "judge",
        "target_identity_dossier": {"scope_index": 2, "uri": TARGET},
        "top_entity_manifest": [{"label": "target", "uri": TARGET}],
        "invoke": fake_invoke,
        "cache_path": tmp_path / "inheritance.json",
    }
    first = resolve_procedure_inheritance(**kwargs)
    second = resolve_procedure_inheritance(**kwargs)

    assert first == second
    assert call_count == 3
    assert all("AUTHORITATIVE TARGET IDENTITY DOSSIER" in prompt for prompt in prompts)
    assert all('"scope_index": 2' in prompt for prompt in prompts)
    assert all("AUTHORITATIVE TOP-ENTITY MANIFEST" in prompt for prompt in prompts)

    changed = dict(kwargs)
    changed["target_identity_dossier"] = {"scope_index": 3, "uri": TARGET}
    resolve_procedure_inheritance(**changed)
    assert call_count == 6

    changed_manifest = dict(kwargs)
    changed_manifest["top_entity_manifest"] = [
        {"label": "target", "uri": TARGET},
        {"label": "sibling", "uri": "urn:procedure:sibling"},
    ]
    resolve_procedure_inheritance(**changed_manifest)
    assert call_count == 9


def _reconciliation_unresolved(target_ref: str, indices: set[int]) -> dict:
    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "target_ref": target_ref,
        "selected_candidate_index": None,
        "selected_status": "unresolved",
        "candidate_assessments": [
            {
                "candidate_index": index,
                "status": "incomplete",
                "semantic_gaps": ["panel disagreement"],
            }
            for index in sorted(indices)
        ],
        "rationale": "no complete candidate",
    }


def test_unresolved_panel_retries_then_fail_opens() -> None:
    present = _vote(inheritance_present=True)
    absent = _vote(inheritance_present=False)
    call_count = 0
    lock = threading.Lock()

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        nonlocal call_count
        with lock:
            call_count += 1
            n = call_count
        if RECONCILIATION_SCHEMA_VERSION in prompt:
            indices = {
                int(match)
                for match in re.findall(r'"candidate_index":\s*(\d+)', prompt)
            }
            return LLMJsonResult(
                data=_reconciliation_unresolved(TARGET, indices),
                elapsed_seconds=0.0,
                token_usage={},
            )
        return LLMJsonResult(
            data=copy.deepcopy(absent if n % 3 == 0 else present),
            elapsed_seconds=0.0,
            token_usage={},
        )

    resolution = resolve_procedure_inheritance(
        source_text="domain-neutral source",
        target_procedure_ref=TARGET,
        target_procedure_label="target",
        tbox_contract={"classes": []},
        model="judge",
        invoke=fake_invoke,
    )

    assert resolution["status"] == "unresolved"
    assert resolution["fail_open"] is True
    assert resolution["resolution_attempts"] == 3
    assert call_count == 18
    assert render_procedure_inheritance_brief(resolution) == ""


def test_unresolved_panel_can_resolve_on_later_attempt() -> None:
    present = _vote(inheritance_present=True)
    absent = _vote(inheritance_present=False)
    call_count = 0
    lock = threading.Lock()

    def fake_invoke(_model: str, prompt: str, **_kwargs: object) -> LLMJsonResult:
        nonlocal call_count
        with lock:
            call_count += 1
            n = call_count
        if RECONCILIATION_SCHEMA_VERSION in prompt:
            indices = {
                int(match)
                for match in re.findall(r'"candidate_index":\s*(\d+)', prompt)
            }
            return LLMJsonResult(
                data=_reconciliation_unresolved(TARGET, indices),
                elapsed_seconds=0.0,
                token_usage={},
            )
        vote = absent if n > 12 else (absent if n % 3 == 0 else present)
        return LLMJsonResult(
            data=copy.deepcopy(vote),
            elapsed_seconds=0.0,
            token_usage={},
        )

    resolution = resolve_procedure_inheritance(
        source_text="domain-neutral source",
        target_procedure_ref=TARGET,
        target_procedure_label="target",
        tbox_contract={"classes": []},
        model="judge",
        invoke=fake_invoke,
    )

    assert resolution["status"] == "no_inheritance"
    assert resolution.get("fail_open") is not True
    assert resolution["resolution_attempts"] == 3
    assert call_count == 15
    assert render_procedure_inheritance_brief(resolution) == ""


def test_prompt_injection_is_runtime_only_and_not_duplicated() -> None:
    brief = render_procedure_inheritance_brief(
        aggregate_inheritance_votes(
            [_vote(), _vote(), _vote()],
            target_procedure_ref=TARGET,
        )
    )
    static_prompt = "Static generated prompt."

    once = _inject_procedure_inheritance_brief(static_prompt, brief)
    twice = _inject_procedure_inheritance_brief(once, brief)

    assert static_prompt == "Static generated prompt."
    assert once == twice
    assert twice.count(BRIEF_BEGIN) == 1


@pytest.mark.asyncio
async def test_pre_and_main_receive_the_same_runtime_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brief = render_procedure_inheritance_brief(
        aggregate_inheritance_votes(
            [_vote(), _vote(), _vote()],
            target_procedure_ref=TARGET,
        )
    )
    prompts: list[str] = []

    class FakeLlm:
        async def ainvoke(self, prompt: str) -> str:
            prompts.append(prompt)
            if "PRE static prompt" in prompt:
                return "complete pre-extraction content for the exact target procedure"
            return "SEMANTIC_HINTS_V1\n1. Complete effective workflow."

    class FakeCreator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def setup_llm(self) -> FakeLlm:
            return FakeLlm()

    monkeypatch.setattr(extraction_module, "LLMCreator", FakeCreator)
    monkeypatch.setattr(extraction_module, "get_extraction_model", lambda _key: "model")
    monkeypatch.setattr(
        extraction_module,
        "judge_extraction_semantics",
        lambda **_kwargs: {"acceptance": {"accepted": True}, "observations": []},
    )

    pre = await run_pre_extraction(
        doi_hash="case",
        entity_label="target",
        entity_uri=TARGET,
        paper_content="source",
        prompt_template="PRE static prompt",
        model_key="pre",
        iter_num=3,
        data_dir=str(tmp_path),
        procedure_inheritance_brief=brief,
    )
    await run_extraction(
        doi_hash="case",
        entity_label="target",
        entity_uri=TARGET,
        source_text=pre,
        prompt_template="MAIN static prompt",
        model_key="main",
        hints_file=str(tmp_path / "case" / "mcp_run" / "hints.txt"),
        iter_num=3,
        hint_representation="semantic-text.v1",
        procedure_inheritance_brief=brief,
    )

    assert len(prompts) == 2
    assert all(prompt.count(BRIEF_BEGIN) == 1 for prompt in prompts)
    assert "PRE static prompt" in prompts[0]
    assert prompts[0].count(
        "PIPELINE-INJECTED CLOSED-LEDGER OUTPUT BOUNDARY: BEGIN"
    ) == 1
    assert "one distinct evidence row for every workflow atom" in prompts[0]
    assert "MAIN static prompt" in prompts[1]


def _micro_audit_response(brief: str, gaps: set[tuple[str, str]]) -> str:
    payload_start = brief.find("{")
    payload_end = brief.rfind("}")
    payload = json.loads(brief[payload_start : payload_end + 1])
    rows = []
    for atom in payload["effective_workflow"]:
        for dimension in (
            "base_preservation",
            "modification_application",
            "mixture_atomization",
            "occurrence_coherence",
            "target_ownership",
        ):
            rows.append(
                {
                    "atom_id": atom["atom_id"],
                    "dimension": dimension,
                    "status": "gap"
                    if (atom["atom_id"], dimension) in gaps
                    else "satisfied",
                    "candidate_evidence_ids": [],
                    "source_evidence": "semantic panel evidence",
                    "reason": "semantic panel verdict",
                }
            )
    return json.dumps({"atom_checks": rows})


@pytest.mark.asyncio
async def test_inheritance_micro_audit_blocks_semantic_majority_atom_gap() -> None:
    resolution = aggregate_inheritance_votes(
        [_vote(), _vote(), _vote()],
        target_procedure_ref=TARGET,
    )
    brief = render_procedure_inheritance_brief(resolution)
    gap = {
        ("W001", "base_preservation"),
        ("W001", "occurrence_coherence"),
    }

    class FakeLlm:
        def __init__(self) -> None:
            self.responses = iter(
                [
                    _micro_audit_response(brief, gap),
                    _micro_audit_response(brief, gap),
                    _micro_audit_response(brief, set()),
                ]
            )

        async def ainvoke(self, _prompt: str) -> str:
            return next(self.responses)

    feedback = await _run_inheritance_micro_audit_panel(
        audit_llm=FakeLlm(),
        original_prompt="generic T-Box contract",
        source_text="source",
        candidate_text='{"evidence":[]}',
        inheritance_brief=brief,
    )
    assert feedback[0].startswith(
        "INHERITANCE_GAP[base_preservation] `W001`"
    )
    assert any(
        item.startswith("INHERITANCE_GAP[occurrence_coherence] `W001`")
        for item in feedback
    )

    class UnanimousLlm:
        async def ainvoke(self, _prompt: str) -> str:
            return _micro_audit_response(brief, gap)

    feedback = await _run_inheritance_micro_audit_panel(
        audit_llm=UnanimousLlm(),
        original_prompt="generic T-Box contract",
        source_text="source",
        candidate_text='{"evidence":[]}',
        inheritance_brief=brief,
    )
    assert feedback[0].startswith(
        "INHERITANCE_GAP[base_preservation] `W001`"
    )
    assert any(
        item.startswith("INHERITANCE_GAP[occurrence_coherence] `W001`")
        for item in feedback
    )
