"""Closed-ledger and operation-projection judges from the official extract runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import logging
import re

from src.extraction_runtime.llm import response_text, strip_code_fences

logger = logging.getLogger("extraction_runtime.judges.closed_ledger")


def bounded_sidecar_path(directory: str, stem: str, suffix: str) -> Path:
    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:12]
    path = Path(directory) / f"{digest}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_MIN_EXTRACTION_CHARS = 20
_CLOSED_LEDGER_RETRY_SPAN_PRESERVATION = (
    "SPAN PRESERVATION: If a failure asks you to split or rewrite one evidence "
    "atom, every other in-scope operation in that atom's verbatim quote must "
    "remain its own evidence atom. A correction that only splits Adds and drops "
    "the rest of the same quote is invalid. Rebuild the complete ledger from the "
    "full target evidence, carrying those remaining quote-local operations "
    "forward."
)

def _prune_untyped_closed_ledger_evidence(candidate_text: str) -> str:
    """Drop context-only atoms that cannot satisfy the typed evidence contract."""
    try:
        payload = json.loads(strip_code_fences(candidate_text))
    except Exception:
        return candidate_text
    evidence = payload.get("evidence") if isinstance(payload, dict) else None
    if not isinstance(evidence, list):
        return candidate_text
    typed = [
        item
        for item in evidence
        if isinstance(item, dict)
        and isinstance(item.get("candidate_types"), list)
        and any(str(value or "").strip() for value in item["candidate_types"])
    ]
    if len(typed) == len(evidence):
        return candidate_text
    for index, item in enumerate(typed, start=1):
        item["evidence_id"] = f"E{index:03d}"
        item["source_order"] = index
    payload["evidence"] = typed
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _validate_closed_ledger_shape(candidate_text: str, source_text: str) -> list[str]:
    """Validate JSON/schema invariants only; LLM judges all content semantics."""
    del source_text
    try:
        payload = json.loads(strip_code_fences(candidate_text))
    except Exception as exc:
        return [f"closed-ledger output is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return ["closed-ledger output must be one JSON object"]

    scope = payload.get("scope_resolution")
    evidence = payload.get("evidence")
    if not isinstance(scope, dict):
        return ["missing object `scope_resolution`"]
    if not isinstance(evidence, list):
        return ["missing array `evidence`"]

    errors: list[str] = []
    attestation = scope.get("completion_attestation")
    required_attestations = (
        "target_located",
        "all_references_resolved",
        "all_modifications_applied",
        "effective_workflow_complete",
    )
    if not isinstance(attestation, dict):
        errors.append("missing object `scope_resolution.completion_attestation`")
    else:
        for key in required_attestations:
            if attestation.get(key) is not True:
                errors.append(f"completion attestation `{key}` must be true")

    for index, item in enumerate(evidence, start=1):
        path = f"evidence[{index - 1}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        expected_id = f"E{index:03d}"
        if item.get("evidence_id") != expected_id:
            errors.append(f"{path}.evidence_id must be `{expected_id}`")
        if item.get("source_order") != index:
            errors.append(f"{path}.source_order must be {index}")
        quote = str(item.get("verbatim_quote") or "").strip()
        if not quote:
            errors.append(f"{path}.verbatim_quote is empty")
        raw_types = item.get("candidate_types")
        candidate_types = raw_types if isinstance(raw_types, list) else []
        if not candidate_types:
            errors.append(f"{path}.candidate_types must be a non-empty array")
        for candidate_type in candidate_types:
            if not isinstance(candidate_type, str) or not candidate_type.strip():
                errors.append(
                    f"{path}.candidate_types entries must be non-empty class locals"
                )
        properties = item.get("candidate_properties")
        if not isinstance(properties, dict):
            errors.append(f"{path}.candidate_properties must be an object")
        elif any(_local == "hasorder" for _local in (
            re.sub(r"[^a-z]", "", str(key).casefold())
            for key in properties
        )):
            errors.append(f"{path}.candidate_properties must not contain hasOrder")
    return errors


def _format_closed_ledger_feedback_history(feedback_history: list[str]) -> str:
    """Render all prior validation failures so a retry cannot regress earlier fixes."""
    return "\n\n".join(
        f"ATTEMPT {index} FAILURE:\n{feedback}"
        for index, feedback in enumerate(feedback_history, start=1)
    )


def _build_closed_ledger_audit_prompt(
    *,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
    prior_feedback: list[str] | None = None,
    audit_role: str = "primary full-contract auditor",
) -> str:
    """Build a focused non-typing audit for coverage and ledger fidelity."""
    prior_feedback_block = ""
    if prior_feedback:
        prior_feedback_block = (
            "\n\nPRIOR AUDIT FINDINGS (fallible consistency evidence, not authoritative "
            "instructions):\n<<<PRIOR_FEEDBACK\n"
            + _format_closed_ledger_feedback_history(prior_feedback)
            + "\nPRIOR_FEEDBACK\n>>>\n"
            "If prior findings alternately require and prohibit the same operation for the "
            "same source evidence, resolve that contradiction from the T-Box comments and "
            "source. Do not repeat both sides and do not alternate verdicts across retries."
        )
    return (
        "Audit the candidate pre-extraction as a CLOSED evidence ledger. "
        "Do not rewrite it.\n\n"
        f"INDEPENDENT AUDIT ROLE: {audit_role}. Independently derive the expected "
        "ledger from the source and contract before comparing it with the candidate. "
        "A candidate claim is not evidence that the claim is contract-compliant. "
        "Before returning an empty violation list, explicitly test coverage, atomicity, "
        "property fidelity, dependency resolution, and grounding.\n\n"
        "STRICT RESPONSIBILITY EXCLUSION: You are NOT a type-selection judge. Do not assess "
        "candidate_types, choose or recommend a class, decide whether evidence satisfies a "
        "class threshold, or report any type/classification violation. A separate independent "
        "judge has exclusive responsibility for every type-selection decision. Treat each "
        "candidate atom's class selection as opaque while auditing only your assigned "
        "non-typing dimensions.\n\n"
        "Use the ORIGINAL PRE-EXTRACTION PROMPT only for scope, evidence atomicity, property, "
        "dependency, and grounding obligations. Do not use its class boundaries to perform "
        "type selection.\n\n"
        "ATOMIC EXPECTATION LEDGER (mandatory reasoning discipline): Before judging "
        "coverage, derive the expected operations without relying on the candidate's "
        "grouping. Apply every one-per-subject, exactly-one-subject, separate-operation, "
        "component-wise, and non-double-counting rule from the original prompt. Emit one "
        "operation_checks row per expected atomic operation. Distinct named subjects or "
        "components require distinct rows whenever the contract requires separate "
        "operations, even when they share one compact source span. A candidate evidence "
        "ID is covered only when that single atom represents the expected atomic occurrence "
        "with the correct attached subject/properties, without judging its selected type. "
        "A broad quote spanning "
        "several expected operations does not make one composite atom cover them all. "
        "After deriving the atomic expectation ledger, perform a one-to-one comparison "
        "against candidate atoms and report merged, split, omitted, or misclassified "
        "occurrences as non-type violations.\n\n"
        "Return JSON only with exactly these keys:\n"
        "{\n"
        '  "operation_checks": [\n'
        "    {\n"
        '      "source_evidence": "exact verbatim source quote",\n'
        '      "operation": "short source-grounded operation description",\n'
        '      "status": "covered|missing",\n'
        '      "candidate_evidence_id": "E001 or null",\n'
        '      "reason": "short reason"\n'
        "    }\n"
        "  ],\n"
        '  "non_type_violations": [\n'
        "    {\n"
        '      "candidate_evidence_id": "E001",\n'
        '      "dimension": "atomicity|property_fidelity|dependency|grounding",\n'
        '      "code": "short non-type violation code or OTHER",\n'
        '      "is_violation": true,\n'
        '      "source_evidence": "exact verbatim source quote",\n'
        '      "message": "specific correction"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "A covered check must identify an existing candidate evidence ID whose quote "
        "covers that source occurrence. Use missing when an independently in-scope occurrence "
        "has no evidence atom. Do not silently "
        "skip a trigger because another operation occurs in the same sentence. "
        "Judge quote grounding semantically against the complete source, including OCR "
        "line breaks, tables, compact tuples, and inherited procedure clauses. Multiple "
        "evidence atoms may cite the same complete source span. When one compact expression "
        "supports several interpreted component values, require the shared source span as "
        "evidence and keep interpreted per-component values in candidate_properties; do not "
        "demand fabricated standalone value phrases. "
        "non_type_violations is ONLY for actual non-typing errors in the candidate: every row "
        "must set is_violation=true. Never report a type choice, a correct atom, a passed check, "
        "or a hypothetical error as a violation; "
        "leave the array empty when there is no actual violation.\n\n"
        f"ORIGINAL PRE-EXTRACTION PROMPT:\n<<<PROMPT\n{original_prompt}\nPROMPT\n>>>\n\n"
        f"ORIGINAL SOURCE TEXT:\n<<<SOURCE\n{source_text}\nSOURCE\n>>>\n\n"
        f"CANDIDATE LEDGER:\n<<<CANDIDATE\n{candidate_text}\nCANDIDATE\n>>>\n"
        f"{prior_feedback_block}"
    )


def _parse_closed_ledger_audit(
    audit_text: str,
    *,
    source_text: str,
    candidate_text: str,
) -> list[str]:
    """Validate the LLM audit schema and return its semantic retry feedback."""
    del source_text
    try:
        audit = json.loads(strip_code_fences(audit_text))
        candidate = json.loads(strip_code_fences(candidate_text))
    except Exception as exc:
        raise ValueError(f"closed-ledger audit is not valid JSON: {exc}") from exc
    if not isinstance(audit, dict):
        raise ValueError("closed-ledger audit must be one JSON object")
    checks = audit.get("operation_checks")
    violations = audit.get("non_type_violations")
    if not isinstance(checks, list) or not isinstance(violations, list):
        raise ValueError(
            "closed-ledger audit requires operation_checks and non_type_violations arrays"
        )

    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in (candidate.get("evidence") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    feedback: list[str] = []
    allowed_statuses = {"covered", "missing"}
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"operation_checks[{index}] must be an object")
        required = {
            "source_evidence",
            "operation",
            "status",
            "candidate_evidence_id",
            "reason",
        }
        if set(check) != required:
            raise ValueError(
                f"operation_checks[{index}] keys differ from the required schema"
            )
        quote = str(check.get("source_evidence") or "").strip()
        status = str(check.get("status") or "").strip().casefold()
        evidence_id = str(check.get("candidate_evidence_id") or "").strip()
        reason = str(check.get("reason") or "").strip()
        if not quote or not reason or status not in allowed_statuses:
            raise ValueError(f"operation_checks[{index}] has invalid required values")
        if status == "covered":
            if evidence_id not in evidence_by_id:
                raise ValueError(
                    f"operation_checks[{index}] covered row references unknown evidence"
                )
        elif status == "missing":
            feedback.append(
                "MISSING_EVIDENCE_ATOM: emit a distinct source-grounded evidence atom "
                f"for source `{quote}`"
            )

    for index, violation in enumerate(violations):
        if not isinstance(violation, dict):
            raise ValueError(f"classification_violations[{index}] must be an object")
        required = {
            "candidate_evidence_id",
            "dimension",
            "code",
            "is_violation",
            "source_evidence",
            "message",
        }
        if set(violation) != required or violation.get("is_violation") is not True:
            raise ValueError(
                f"classification_violations[{index}] must be a schema-valid actual violation"
            )
        evidence_id = str(violation.get("candidate_evidence_id") or "").strip()
        quote = str(violation.get("source_evidence") or "").strip()
        message = str(violation.get("message") or "").strip()
        code = str(violation.get("code") or "OTHER").strip()
        dimension = str(violation.get("dimension") or "").strip()
        if dimension not in {"atomicity", "property_fidelity", "dependency", "grounding"}:
            raise ValueError(
                f"non_type_violations[{index}].dimension is invalid"
            )
        if evidence_id not in evidence_by_id or not quote or not message or not code:
            raise ValueError(
                f"non_type_violations[{index}] has invalid required values"
            )
        feedback.append(
            f"LEDGER_{dimension.upper()}[{code}] `{evidence_id}`: {message} "
            f"[source: {quote}]"
        )
    return list(dict.fromkeys(feedback))


def _type_selection_contract_projection(original_prompt: str) -> str:
    """Remove extraction-output instructions from the type judge's contract."""
    start_marker = "Candidate types — CLOSED ENUMERATION"
    end_marker = "Evidence accounting protocol"
    start = original_prompt.find(start_marker)
    end = original_prompt.find(end_marker, start + len(start_marker))
    if start >= 0 and end > start:
        return original_prompt[start:end].strip()
    return original_prompt.strip()


def _build_type_selection_judge_prompt(
    *,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
) -> str:
    """Build the only audit prompt authorized to judge candidate type choices."""
    del source_text
    type_contract = _type_selection_contract_projection(original_prompt)
    return (
        "You are the independent TYPE-SELECTION JUDGE. You have exclusive responsibility "
        "for deciding whether the ONE supplied closed-ledger evidence atom selected the correct "
        "class set. "
        "Do not audit coverage, missing evidence, property values, dependency resolution, "
        "serialization, ordering, or atomicity; other judges own those dimensions.\n\n"
        "Compare the atom's verbatim source evidence with every applicable rule in the supplied "
        "TYPE CONTRACT. The contract is a read-only class boundary projection; it contains no "
        "output instructions. Comments containing `【Warning】` mark high-risk choice boundaries: inspect the "
        "complete marked comment and all applicable warning-marked alternatives before voting. "
        "The marker adds attention only and supplies no semantics beyond the T-Box text.\n\n"
        "Choose exactly one verdict for this atom:\n"
        "- pass: the selected candidate_types set is exactly source-supported;\n"
        "- misclassified: the atom represents an in-scope occurrence but its class set must be "
        "replaced by corrected_types;\n"
        "- excluded: the quoted text does not independently instantiate any permitted candidate "
        "class, so corrected_types must be empty and the atom must be removed.\n\n"
        "Do not create a missing atom and do not repair properties. Return JSON only:\n"
        '{"type_checks":[{"candidate_evidence_id":"E001","selected_types":["ClassLocal"],'
        '"verdict":"pass|misclassified|excluded","corrected_types":["ClassLocal"],'
        '"source_evidence":"exact quote","reason":"specific T-Box-derived reason"}]}\n'
        "Return exactly one row for the supplied atom. For pass, "
        "corrected_types must exactly equal selected_types. For misclassified, corrected_types "
        "must be a non-empty list of class locals permitted by the original prompt. For excluded, "
        "corrected_types must be empty. Never add domain knowledge absent from the T-Box.\n\n"
        f"TYPE CONTRACT:\n<<<TYPE_CONTRACT\n{type_contract}\nTYPE_CONTRACT\n>>>\n\n"
        f"ONE CANDIDATE EVIDENCE ATOM:\n<<<CANDIDATE\n{candidate_text}\nCANDIDATE\n>>>"
    )


_FEEDBACK_CODE_RE = re.compile(r"^(?P<code>[A-Z][A-Z0-9_]+(?:\[[^\]]+\])?)")
_FEEDBACK_ATOM_ID_RE = re.compile(r"`(E\d+)`")


def _dedupe_type_locals(values: list[Any]) -> list[str]:
    """Preserve first-seen class locals and drop blank or repeated entries."""
    cleaned = [str(value).strip() for value in values]
    return list(dict.fromkeys(item for item in cleaned if item))


def _closed_ledger_feedback_fingerprint(feedback: list[str]) -> frozenset[str]:
    """Domain-independent retry identity: violation code plus atom id only."""
    items: list[str] = []
    for raw in feedback:
        text = str(raw).split("[source:", 1)[0].strip()
        code_match = _FEEDBACK_CODE_RE.match(text)
        code = code_match.group("code") if code_match else text[:64]
        atom_match = _FEEDBACK_ATOM_ID_RE.search(text)
        atom_id = atom_match.group(1) if atom_match else ""
        items.append(f"{code}|{atom_id}")
    return frozenset(items)


def is_llm_transport_error(error: BaseException) -> bool:
    """True for provider/gateway/network failures, not semantic errors."""
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    text = str(error or "").casefold()
    markers = (
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "429",
        "502",
        "503",
        "504",
        "temporarily unavailable",
        "gateway",
    )
    return any(marker in text for marker in markers)


def _is_transport_pre_extraction_error(error: BaseException) -> bool:
    """Return True only for connectivity or rate-limit failures."""
    return is_llm_transport_error(error)


def _pre_extraction_retry_wait_seconds(error: BaseException, attempt: int) -> float:
    """Backoff only after transport failures; semantic rejections retry immediately."""
    if not _is_transport_pre_extraction_error(error):
        return 0.0
    return float(5 * (attempt + 1))


def _should_stop_closed_ledger_retry(
    *,
    current_fingerprint: frozenset[str],
    previous_fingerprint: frozenset[str] | None,
    attempt: int,
    max_retries: int,
    nonblocking: bool,
) -> bool:
    """Stop when the budget is gone or two consecutive filtered fingerprints match."""
    if not nonblocking or not current_fingerprint:
        return False
    if attempt >= max_retries - 1:
        return True
    return (
        previous_fingerprint is not None
        and current_fingerprint == previous_fingerprint
    )


def _parse_type_selection_judgement(
    judgement_text: str,
    *,
    candidate_text: str,
) -> list[str]:
    """Validate one dedicated type judgement and render retry feedback."""
    try:
        judgement = json.loads(strip_code_fences(judgement_text))
        candidate = json.loads(strip_code_fences(candidate_text))
    except Exception as exc:
        raise ValueError(f"type-selection judgement is not valid JSON: {exc}") from exc
    if not isinstance(judgement, dict) or set(judgement) != {"type_checks"}:
        raise ValueError("type-selection judgement requires only type_checks")
    evidence = [
        item
        for item in (candidate.get("evidence") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    checks = judgement["type_checks"]
    if not isinstance(checks, list) or len(checks) != len(evidence):
        raise ValueError(
            "type-selection judgement must cover every candidate evidence atom exactly once"
        )
    required = {
        "candidate_evidence_id",
        "selected_types",
        "verdict",
        "corrected_types",
        "source_evidence",
        "reason",
    }
    feedback: list[str] = []
    for index, (check, item) in enumerate(zip(checks, evidence, strict=True)):
        if not isinstance(check, dict) or set(check) != required:
            raise ValueError(f"type_checks[{index}] keys differ from the required schema")
        evidence_id = str(item["evidence_id"])
        if check["candidate_evidence_id"] != evidence_id:
            raise ValueError(f"type_checks[{index}] does not preserve candidate order")
        selected = check["selected_types"]
        corrected = check["corrected_types"]
        expected_selected = item.get("candidate_types")
        verdict = str(check["verdict"])
        quote = str(check["source_evidence"] or "").strip()
        reason = str(check["reason"] or "").strip()
        if (
            not isinstance(selected, list)
            or selected != expected_selected
            or any(not isinstance(value, str) or not value.strip() for value in selected)
        ):
            raise ValueError(f"type_checks[{index}].selected_types is invalid")
        if (
            not isinstance(corrected, list)
            or any(not isinstance(value, str) or not value.strip() for value in corrected)
            or verdict not in {"pass", "misclassified", "excluded"}
            or not quote
            or not reason
        ):
            raise ValueError(f"type_checks[{index}] has invalid required values")
        unique_selected = _dedupe_type_locals(selected)
        unique_corrected = _dedupe_type_locals(corrected)
        if verdict == "pass" and frozenset(unique_corrected) != frozenset(
            unique_selected
        ):
            raise ValueError(f"type_checks[{index}] pass must preserve selected_types")
        # Normalize only the verdict label from the judge's explicit corrected_types
        # payload. This is schema repair, not an independent type decision.
        if verdict == "misclassified" and not unique_corrected:
            verdict = "excluded"
        elif verdict == "excluded" and unique_corrected:
            verdict = "misclassified"
        if verdict == "misclassified":
            if frozenset(unique_corrected) == frozenset(unique_selected):
                continue
            feedback.append(
                f"TYPE_SELECTION_MISCLASSIFIED `{evidence_id}`: replace candidate_types "
                f"{selected!r} with {unique_corrected!r}. {reason} [source: {quote}]"
            )
        elif verdict == "excluded":
            feedback.append(
                f"TYPE_SELECTION_EXCLUDED `{evidence_id}`: remove this evidence atom. "
                f"{reason} [source: {quote}]"
            )
    return feedback


async def _run_type_selection_judge(
    *,
    audit_llm: Any,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
    format_retries: int = 3,
    trace_dir: str = "",
    trace_stem: str = "",
) -> list[str]:
    """Judge each evidence atom independently and in parallel."""
    candidate = json.loads(strip_code_fences(candidate_text))
    evidence = [
        item
        for item in (candidate.get("evidence") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    attempt_limit = max(1, int(format_retries))

    async def judge_one(item: dict[str, Any]) -> list[str]:
        evidence_id = str(item["evidence_id"])
        one_candidate = json.dumps({"evidence": [item]}, ensure_ascii=False)
        base_prompt = _build_type_selection_judge_prompt(
            original_prompt=original_prompt,
            source_text=source_text,
            candidate_text=one_candidate,
        )
        current_prompt = base_prompt
        last_error = ""
        for attempt in range(1, attempt_limit + 1):
            result = await audit_llm.ainvoke(current_prompt)
            raw = response_text(result)
            try:
                feedback = _parse_type_selection_judgement(
                    raw,
                    candidate_text=one_candidate,
                )
                validation_error = ""
            except ValueError as exc:
                feedback = []
                validation_error = str(exc)
                last_error = validation_error
            if trace_dir:
                Path(trace_dir).mkdir(parents=True, exist_ok=True)
                response_metadata = getattr(result, "response_metadata", {}) or {}
                usage_metadata = getattr(result, "usage_metadata", {}) or {}
                trace_path = bounded_sidecar_path(
                    trace_dir,
                    f"{trace_stem}.type_{evidence_id}.attempt_{attempt}",
                    ".json",
                )
                trace_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "type-selection-call.v1",
                            "evidence_id": evidence_id,
                            "attempt": attempt,
                            "ok": not validation_error,
                            "validation_error": validation_error,
                            "raw_response": raw,
                            "finish_reason": response_metadata.get("finish_reason"),
                            "usage": usage_metadata,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            if not validation_error:
                return feedback
            current_prompt = (
                f"{base_prompt}\n\nYour previous response violated the required type_checks "
                f"schema: {validation_error}. Return a corrected JSON object for {evidence_id}. "
                "Do not copy any extraction schema or add any top-level key other than type_checks."
            )
        raise ValueError(
            f"type-selection judgement for {evidence_id} remained invalid after "
            f"{attempt_limit} actual attempts: {last_error}"
        )

    per_atom_feedback = await asyncio.gather(
        *(judge_one(item) for item in evidence)
    )
    return list(dict.fromkeys(item for rows in per_atom_feedback for item in rows))


def _build_closed_ledger_format_repair_prompt(
    *,
    invalid_audit_text: str,
    validation_error: str,
    candidate_text: str,
) -> str:
    """Build a domain-agnostic JSON normalization task for one audit response."""
    try:
        candidate = json.loads(strip_code_fences(candidate_text))
    except Exception:
        candidate = {}
    valid_evidence_ids = [
        str(item.get("evidence_id"))
        for item in (candidate.get("evidence") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    return (
        "You are a JSON schema normalization processor, not a semantic auditor. "
        "Repair only the supplied audit response. Do not inspect source science, derive "
        "new operations, change a valid semantic finding, or add a hypothetical finding.\n\n"
        "Return one JSON object with exactly two array-valued keys and no other text: "
        "operation_checks and non_type_violations.\n"
        "Every operation_checks object must have exactly these keys: source_evidence "
        "(non-empty string), operation (non-empty string), status (covered or missing), "
        "candidate_evidence_id (a valid evidence-ID string or JSON null), and "
        "reason (non-empty string).\n"
        "Every non_type_violations object must have exactly these keys: "
        "candidate_evidence_id (valid evidence-ID string), dimension (exactly one of "
        "atomicity, property_fidelity, dependency, grounding), code (non-empty string), "
        "is_violation (the JSON boolean true), source_evidence (non-empty string), and "
        "message (non-empty string).\n\n"
        "Normalization rules:\n"
        "- Preserve all schema-valid rows and their meanings.\n"
        "- A missing operation belongs only in operation_checks with status=missing and "
        "candidate_evidence_id=null; it must not also appear as a non-type violation.\n"
        "- Never create a new operation_checks row from a malformed non-type violation row. "
        "Only normalize operation_checks rows already present in the invalid response. If a "
        "malformed violation row has no valid candidate ID and no corresponding existing "
        "operation_check, remove that row; do not invent an operation, reason, candidate ID, "
        "or placeholder.\n"
        "- Every non_type_violations row must describe an actual non-typing defect in an existing "
        "candidate atom, set is_violation=true, and reference exactly one VALID CANDIDATE "
        "EVIDENCE ID listed below. Never invent or repair an evidence ID by guessing.\n"
        "- Never assess, choose, correct, or mention candidate types/classes; a separate judge "
        "has exclusive responsibility for type selection.\n"
        "- Remove correctness rows, hypothetical violations, duplicates, and malformed non-type "
        "rows that cannot reference an existing candidate atom. Preserve the "
        "corresponding missing operation_check when one exists.\n"
        "- Use JSON null, not the string \"null\", when no candidate evidence exists.\n\n"
        f"VALID CANDIDATE EVIDENCE IDS:\n{json.dumps(valid_evidence_ids)}\n\n"
        f"VALIDATION ERROR:\n{validation_error}\n\n"
        "INVALID AUDIT RESPONSE:\n<<<INVALID_AUDIT\n"
        f"{invalid_audit_text}\n"
        "INVALID_AUDIT\n>>>"
    )


def _closed_ledger_operation_projection(source_text: str) -> list[dict[str, Any]]:
    """Read the mechanically declared operation surface from a closed ledger."""
    try:
        payload = json.loads(strip_code_fences(source_text))
    except Exception as exc:
        raise ValueError(
            f"operation projection source is not a closed-ledger JSON object: {exc}"
        ) from exc
    evidence = payload.get("evidence") if isinstance(payload, dict) else None
    if not isinstance(evidence, list):
        raise ValueError("operation projection source requires an evidence array")

    operations: list[dict[str, Any]] = []
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"closed-ledger evidence[{index - 1}] must be an object")
        evidence_id = str(item.get("evidence_id") or "").strip()
        expected_id = f"E{index:03d}"
        if evidence_id != expected_id:
            raise ValueError(
                f"closed-ledger evidence[{index - 1}].evidence_id must be {expected_id}"
            )
        candidate_types = item.get("candidate_types")
        if not isinstance(candidate_types, list) or not candidate_types:
            raise ValueError(
                f"closed-ledger evidence[{index - 1}] requires candidate_types"
            )
        operations.append(
            {
                "evidence_id": evidence_id,
                "source_order": item.get("source_order"),
                "verbatim_quote": str(item.get("verbatim_quote") or ""),
                "candidate_types": list(candidate_types),
                "candidate_properties": dict(item.get("candidate_properties") or {}),
            }
        )
    return operations


def _build_operation_projection_vote_prompt(
    *,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
    operations: list[dict[str, Any]],
) -> str:
    """Build one narrow, cardinality-neutral evidence-coverage vote."""
    del original_prompt, source_text
    return (
        "You are one independent evidence-coverage projection voter. Judge only whether "
        "the source-grounded semantic content of each closed-ledger evidence atom is "
        "preserved in the MAIN semantic-text extraction. "
        "Do not perform a broad extraction-quality review and do not add obligations that "
        "are absent from the supplied projection.\n\n"
        "AUTHORITY BOUNDARY: A closed-ledger evidence atom is authoritative for its "
        "source-grounded facts and relative source position. It is an evidence unit, not "
        "an authoritative declaration of output-occurrence cardinality or decomposition. "
        "One or more MAIN occurrences may collectively preserve one evidence atom. Shared "
        "verbatim evidence, a shared verb, or multiple MAIN occurrences mapped to one "
        "evidence_id does not by itself establish duplication. Duplication is outside this "
        "judge's responsibility.\n\n"
        "STRICT RESPONSIBILITY EXCLUSION: You are NOT a type-selection judge. The supplied "
        "candidate_types are opaque labels already reviewed by a separate independent judge. "
        "Do not decide whether a type is correct, propose another type, apply class thresholds, "
        "or report misclassification. Judge only evidence coverage using identity, amount, "
        "role, and operation-local qualifiers.\n\n"
        "For each supplied evidence_id choose exactly one status:\n"
        "- complete: one or more MAIN occurrences collectively preserve every "
        "source-grounded fact in the evidence atom;\n"
        "- partial: MAIN preserves some but not all source-grounded facts in the evidence atom;\n"
        "- missing: no MAIN occurrence preserves the evidence atom.\n\n"
        "Make content decisions semantically. Do not use keywords, regular expressions, "
        "string similarity, or literal substring matching. Serialization, schema coverage, "
        "and vote consensus are checked mechanically outside this vote. You cannot see any "
        "other voter's response.\n\n"
        "Return JSON only with exactly this schema:\n"
        '{"operation_checks":[{"evidence_id":"E001","status":"complete|partial|missing",'
        '"candidate_occurrences":["short semantic locator"],'
        '"reason":"specific semantic reason"}]}\n'
        "Return exactly one row for every supplied operation and no other rows.\n\n"
        f"CLOSED-LEDGER OPERATIONS:\n{json.dumps(operations, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE MAIN SEMANTIC LEDGER:\n<<<CANDIDATE\n{candidate_text}\nCANDIDATE\n>>>\n"
    )


def _parse_operation_projection_vote(
    vote_text: str,
    *,
    operation_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Validate one vote's schema and exact operation coverage."""
    try:
        payload = json.loads(strip_code_fences(vote_text))
    except Exception as exc:
        raise ValueError(f"operation projection vote is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"operation_checks"}:
        raise ValueError("operation projection vote requires only operation_checks")
    checks = payload["operation_checks"]
    if not isinstance(checks, list) or len(checks) != len(operation_ids):
        raise ValueError("operation projection vote must cover every operation exactly once")

    expected = set(operation_ids)
    indexed: dict[str, dict[str, Any]] = {}
    required = {"evidence_id", "status", "candidate_occurrences", "reason"}
    allowed_statuses = {"complete", "partial", "missing"}
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != required:
            raise ValueError(
                f"operation_checks[{index}] keys differ from the required schema"
            )
        evidence_id = str(check["evidence_id"])
        if evidence_id not in expected or evidence_id in indexed:
            raise ValueError(
                f"operation_checks[{index}] has unknown or duplicate evidence_id"
            )
        status = str(check["status"])
        occurrences = check["candidate_occurrences"]
        reason = check["reason"]
        if status not in allowed_statuses:
            raise ValueError(f"operation_checks[{index}].status is invalid")
        if not isinstance(occurrences, list) or len(occurrences) > 100:
            raise ValueError(
                f"operation_checks[{index}].candidate_occurrences must be a bounded array"
            )
        if any(not isinstance(value, str) or not value.strip() for value in occurrences):
            raise ValueError(
                f"operation_checks[{index}].candidate_occurrences entries are invalid"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"operation_checks[{index}].reason is empty")
        indexed[evidence_id] = dict(check)
    if set(indexed) != expected:
        raise ValueError("operation projection vote coverage differs from the ledger")
    return indexed


async def _run_operation_projection_panel(
    *,
    audit_llm: Any,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
) -> dict[str, Any]:
    """Block only defects receiving the same verdict from all three votes."""
    operations = _closed_ledger_operation_projection(source_text)
    operation_ids = [item["evidence_id"] for item in operations]
    prompt = _build_operation_projection_vote_prompt(
        original_prompt=original_prompt,
        source_text=source_text,
        candidate_text=candidate_text,
        operations=operations,
    )

    async def run_vote() -> dict[str, dict[str, Any]]:
        current_prompt = prompt
        errors: list[str] = []
        for _ in range(3):
            result = await audit_llm.ainvoke(current_prompt)
            try:
                return _parse_operation_projection_vote(
                    response_text(result),
                    operation_ids=operation_ids,
                )
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
                current_prompt = (
                    prompt
                    + "\n\nYour prior response failed mechanical schema validation: "
                    + str(exc)
                    + "\nReturn corrected JSON for the same narrow operation projection."
                )
        raise ValueError(
            "operation projection vote schema remained invalid: " + "; ".join(errors)
        )

    results = await asyncio.gather(*(run_vote() for _ in range(3)), return_exceptions=True)
    votes: list[dict[str, dict[str, Any]]] = []
    for index, result in enumerate(results, start=1):
        if isinstance(result, BaseException):
            raise ValueError(
                f"operation projection vote {index}/3 failed: {result}"
            ) from result
        votes.append(result)

    consensus_rows: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for evidence_id in operation_ids:
        statuses = [vote[evidence_id]["status"] for vote in votes]
        unanimous = len(set(statuses)) == 1
        row = {
            "evidence_id": evidence_id,
            "vote_statuses": statuses,
            "consensus_status": statuses[0] if unanimous else "unresolved",
            "unanimous": unanimous,
            "reasons": [vote[evidence_id]["reason"] for vote in votes],
        }
        consensus_rows.append(row)
        if not unanimous:
            unresolved.append(row)
        elif statuses[0] != "complete":
            blocking.append(row)

    feedback = [
        f"OPERATION_PROJECTION_{row['consensus_status'].upper()} "
        f"`{row['evidence_id']}`: {row['reasons'][0]}"
        for row in blocking
    ]
    return {
        "schema_version": "closed-ledger-evidence-coverage-panel.v2",
        "vote_count": 3,
        "acceptance": {
            "accepted": not blocking,
            "policy": "block_only_on_three_identical_defect_votes",
        },
        "operation_checks": consensus_rows,
        "blocking": blocking,
        "unresolved": unresolved,
        "feedback": feedback,
    }


_INHERITANCE_AUDIT_DIMENSIONS = (
    "base_preservation",
    "modification_application",
    "mixture_atomization",
    "occurrence_coherence",
    "target_ownership",
)


def _inheritance_brief_payload(brief: str) -> dict[str, Any]:
    """Decode the structured brief envelope without interpreting its content."""
    start = brief.find("{")
    end = brief.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("procedure inheritance brief has no JSON object")
    payload = json.loads(brief[start : end + 1])
    if not isinstance(payload, dict) or not isinstance(
        payload.get("effective_workflow"), list
    ):
        raise ValueError("procedure inheritance brief has invalid structured payload")
    return payload


def _build_inheritance_micro_audit_prompt(
    *,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
    inheritance_brief: str,
) -> str:
    """Build the narrow per-atom semantic inheritance audit."""
    return (
        "You are one independent inheritance micro-auditor. You cannot see any other "
        "vote. Judge every effective-workflow atom semantically against the source, "
        "active T-Box contract in the original prompt, and candidate ledger. Do not use "
        "keywords, regular expressions, string similarity, or literal substring matching "
        "to decide content.\n\n"
        "For every atom, independently check exactly five dimensions: "
        "base_preservation (inherited base meaning remains represented), "
        "modification_application (insert/delete/replace/refine effects are correctly "
        "applied), mixture_atomization (each explicit group member that is an introduction "
        "occurrence has its own distinct candidate introduction atom; a component array or one "
        "composite atom is a gap), occurrence_coherence (the atom's identity, "
        "amount, role, and source-supported qualifiers remain attached to that same "
        "candidate occurrence without cross-occurrence swapping or loss), and "
        "target_ownership (the atom belongs to "
        "the exact target rather than a base, sibling, or neighboring procedure). "
        "Use status satisfied, gap, or not_applicable. A gap requires concrete semantic "
        "evidence. Do not perform any other extraction audit.\n\n"
        "Return JSON only with exactly this schema:\n"
        '{"atom_checks":[{"atom_id":"","dimension":"base_preservation|'
        'modification_application|mixture_atomization|occurrence_coherence|'
        'target_ownership",'
        '"status":"satisfied|gap|not_applicable","candidate_evidence_ids":["E001"],'
        '"source_evidence":"","reason":""}]}\n'
        "Return exactly one row for every effective atom and every dimension.\n\n"
        f"PROCEDURE INHERITANCE BRIEF:\n{inheritance_brief}\n\n"
        f"ORIGINAL PRE-EXTRACTION PROMPT:\n<<<PROMPT\n{original_prompt}\nPROMPT\n>>>\n\n"
        f"ORIGINAL SOURCE:\n<<<SOURCE\n{source_text}\nSOURCE\n>>>\n\n"
        f"CANDIDATE LEDGER:\n<<<CANDIDATE\n{candidate_text}\nCANDIDATE\n>>>\n"
    )


def _parse_inheritance_micro_audit(
    audit_text: str,
    *,
    inheritance_brief: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Validate audit shape and index semantic verdicts without content overrides."""
    try:
        audit = json.loads(strip_code_fences(audit_text))
    except Exception as exc:
        raise ValueError(f"inheritance micro-audit is not valid JSON: {exc}") from exc
    if not isinstance(audit, dict) or set(audit) != {"atom_checks"}:
        raise ValueError("inheritance micro-audit keys differ from required schema")
    checks = audit["atom_checks"]
    if not isinstance(checks, list) or len(checks) > 4000:
        raise ValueError("inheritance atom_checks must be a bounded array")

    brief_payload = _inheritance_brief_payload(inheritance_brief)
    atoms = brief_payload["effective_workflow"]
    atoms_by_id = {
        str(atom.get("atom_id") or ""): atom
        for atom in atoms
        if isinstance(atom, dict)
    }
    atom_ids = [
        str(atom.get("atom_id") or "")
        for atom in atoms
        if isinstance(atom, dict)
    ]
    if not atom_ids or any(not atom_id for atom_id in atom_ids):
        raise ValueError("inheritance brief effective atoms require atom_id")
    expected = {
        (atom_id, dimension)
        for atom_id in atom_ids
        for dimension in _INHERITANCE_AUDIT_DIMENSIONS
    }
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    required = {
        "atom_id",
        "dimension",
        "status",
        "candidate_evidence_ids",
        "source_evidence",
        "reason",
    }
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != required:
            raise ValueError(f"atom_checks[{index}] keys differ from required schema")
        key = (str(check["atom_id"]), str(check["dimension"]))
        if key not in expected or key in indexed:
            raise ValueError(f"atom_checks[{index}] has unknown or duplicate atom/dimension")
        if check["status"] not in {"satisfied", "gap", "not_applicable"}:
            raise ValueError(f"atom_checks[{index}].status is invalid")
        atom = atoms_by_id[key[0]]
        if (
            key[1] == "base_preservation"
            and atom.get("applied_modification_ids")
            and check["status"] == "gap"
        ):
            raise ValueError(
                f"atom_checks[{index}] base_preservation cannot be a gap for an "
                "explicit modification atom; use not_applicable"
            )
        if (
            key[1] == "modification_application"
            and not atom.get("applied_modification_ids")
            and check["status"] == "gap"
        ):
            raise ValueError(
                f"atom_checks[{index}] modification_application cannot be a gap for "
                "an unmodified inherited atom; use not_applicable"
            )
        evidence_ids = check["candidate_evidence_ids"]
        if not isinstance(evidence_ids, list) or len(evidence_ids) > 1000:
            raise ValueError(
                f"atom_checks[{index}].candidate_evidence_ids must be a bounded array"
            )
        if any(not isinstance(value, str) or not value.strip() for value in evidence_ids):
            raise ValueError(
                f"atom_checks[{index}].candidate_evidence_ids entries are invalid"
            )
        if not isinstance(check["source_evidence"], str) or not isinstance(
            check["reason"], str
        ):
            raise ValueError(f"atom_checks[{index}] evidence and reason must be strings")
        if not str(check["reason"]).strip():
            raise ValueError(f"atom_checks[{index}].reason is empty")
        indexed[key] = dict(check)
    if set(indexed) != expected:
        raise ValueError("inheritance micro-audit does not cover every atom and dimension")
    return indexed


async def _run_inheritance_micro_audit_panel(
    *,
    audit_llm: Any,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
    inheritance_brief: str,
) -> list[str]:
    """Block gaps independently reported by a three-vote semantic majority."""
    prompt = _build_inheritance_micro_audit_prompt(
        original_prompt=original_prompt,
        source_text=source_text,
        candidate_text=candidate_text,
        inheritance_brief=inheritance_brief,
    )

    async def run_vote() -> dict[tuple[str, str], dict[str, Any]]:
        current_prompt = prompt
        errors: list[str] = []
        for _ in range(3):
            result = await audit_llm.ainvoke(current_prompt)
            try:
                return _parse_inheritance_micro_audit(
                    response_text(result),
                    inheritance_brief=inheritance_brief,
                )
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
                current_prompt = (
                    prompt
                    + "\n\nYour prior response failed mechanical schema validation: "
                    + str(exc)
                    + "\nReturn corrected JSON for the same atom-by-atom semantic audit. "
                    "Return only the required atom_checks object and no other audit schema."
                )
        raise ValueError(
            "inheritance micro-audit schema remained invalid: "
            + "; ".join(errors)
        )

    results = await asyncio.gather(*(run_vote() for _ in range(3)), return_exceptions=True)
    votes: list[dict[tuple[str, str], dict[str, Any]]] = []
    for index, result in enumerate(results, start=1):
        if isinstance(result, BaseException):
            raise ValueError(
                f"inheritance micro-audit vote {index}/3 failed: {result}"
            ) from result
        votes.append(result)

    majority_gaps = {
        key
        for key in votes[0]
        if sum(vote[key]["status"] == "gap" for vote in votes) >= 2
    }
    feedback: list[str] = []
    for atom_id, dimension in sorted(majority_gaps):
        row = votes[0][(atom_id, dimension)]
        feedback.append(
            f"INHERITANCE_GAP[{dimension}] `{atom_id}`: {row['reason']} "
            f"[source: {row['source_evidence']}]"
        )
    return feedback


async def _run_closed_ledger_audit_panel(
    *,
    audit_llm: Any,
    format_llm: Any | None = None,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
    prior_feedback: list[str],
    vote_count: int = 3,
    format_retries: int = 3,
    inheritance_brief: str = "",
    format_trace_dir: str = "",
    format_trace_stem: str = "",
) -> list[str]:
    """Require unanimous semantic approval, expanding to a panel only after a pass."""
    vote_count = max(1, int(vote_count))
    roles = [
        (
            "primary non-typing coverage and grounding auditor; derive a source-first "
            "occurrence ledger and compare it one-to-one without assessing any class choice"
        ),
        (
            "independent coverage, dependency, and atomicity auditor; temporarily ignore "
            "candidate types, enumerate every contract-required atomic subject and occurrence "
            "from the source, then reject unsupported many-to-one or one-to-many mappings"
        ),
        (
            "independent property-fidelity and dependency auditor; treat every class choice as "
            "opaque and verify only grounding, property ownership, and dependency application"
        ),
    ]

    async def run_vote(index: int) -> list[str]:
        role = roles[index] if index < len(roles) else f"independent auditor {index + 1}"
        audit_prompt = _build_closed_ledger_audit_prompt(
                original_prompt=original_prompt,
                source_text=source_text,
                candidate_text=candidate_text,
                prior_feedback=prior_feedback,
                audit_role=role,
            )
        format_attempt_limit = max(1, int(format_retries))
        formatter = format_llm or audit_llm

        def persist_invalid(
            *, audit_text: str, error: ValueError, format_attempt: int
        ) -> None:
            if not format_trace_dir:
                return
            stem = format_trace_stem or "closed_ledger"
            stem_token = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:12]
            trace_path = os.path.join(
                format_trace_dir,
                f"clf_{stem_token}_v{index + 1}_f{format_attempt}.json",
            )
            try:
                os.makedirs(format_trace_dir, exist_ok=True)
                with open(trace_path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "schema_version": "closed-ledger-format-failure.v1",
                            "trace_stem": stem,
                            "vote": index + 1,
                            "format_attempt": format_attempt,
                            "validation_error": str(error),
                            "invalid_response": audit_text,
                        },
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
            except OSError as trace_error:
                logger.warning(
                    "    ⚠️  Could not persist non-blocking closed-ledger format "
                    "diagnostic '%s': %s",
                    trace_path,
                    trace_error,
                )

        result = await audit_llm.ainvoke(audit_prompt)
        audit_text = response_text(result)
        try:
            return _parse_closed_ledger_audit(
                audit_text,
                source_text=source_text,
                candidate_text=candidate_text,
            )
        except ValueError as exc:
            last_error = exc
            persist_invalid(audit_text=audit_text, error=exc, format_attempt=0)

        for format_attempt in range(1, format_attempt_limit + 1):
            logger.warning(
                "    ⚠️  Closed-ledger audit vote %s returned invalid format; "
                "sending it to dedicated formatter (format retry %s/%s): %s",
                index + 1,
                format_attempt,
                format_attempt_limit,
                last_error,
            )
            format_prompt = _build_closed_ledger_format_repair_prompt(
                invalid_audit_text=audit_text,
                validation_error=str(last_error),
                candidate_text=candidate_text,
            )
            result = await formatter.ainvoke(format_prompt)
            audit_text = response_text(result)
            try:
                return _parse_closed_ledger_audit(
                    audit_text,
                    source_text=source_text,
                    candidate_text=candidate_text,
                )
            except ValueError as exc:
                last_error = exc
                persist_invalid(
                    audit_text=audit_text,
                    error=exc,
                    format_attempt=format_attempt,
                )
        raise ValueError(
            f"closed-ledger audit vote {index + 1} remained schema-invalid after "
            f"{format_attempt_limit} format attempts: {last_error}"
        )

    first_feedback = await run_vote(0)
    if first_feedback:
        return first_feedback

    combined_feedback: list[str] = []
    if vote_count > 1:
        confirmation_results = await asyncio.gather(
            *(run_vote(index) for index in range(1, vote_count)),
            return_exceptions=True,
        )
        for index, result in enumerate(confirmation_results, start=2):
            if isinstance(result, BaseException):
                raise ValueError(
                    f"closed-ledger semantic audit vote {index}/{vote_count} failed: {result}"
                ) from result
            combined_feedback.extend(result)
    if inheritance_brief:
        combined_feedback.extend(
            await _run_inheritance_micro_audit_panel(
                audit_llm=audit_llm,
                original_prompt=original_prompt,
                source_text=source_text,
                candidate_text=candidate_text,
                inheritance_brief=inheritance_brief,
            )
        )
    return list(dict.fromkeys(combined_feedback))
