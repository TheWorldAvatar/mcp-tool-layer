"""Top-entity membership, omission, and T-Box revision judges."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, List, Optional

import logging

from src.extraction_runtime.llm import response_text

logger = logging.getLogger("extraction_runtime.judges.top_entity")
from src.extraction_runtime.names import local_name
from src.extraction_runtime.steps.top_entity.membership import (
    normalize_top_entity_output,
    validate_top_entity_lines,
)
from src.utils.source_text_sanitize import sanitize_source_markdown


def _format_top_entity_feedback_history(feedback_history: list[str]) -> str:
    if not feedback_history:
        return ""
    return (
        "\n\nVALIDATION FEEDBACK FROM ALL PREVIOUS ATTEMPTS "
        "(oldest to newest):\n"
        + "\n\n".join(
            f"ATTEMPT {index}:\n{feedback}"
            for index, feedback in enumerate(feedback_history, start=1)
        )
        + "\n\nReturn a complete corrected top-entity list. Fix every "
        "recorded issue; do not regress facts accepted earlier."
    )


def _split_outcome_reminder() -> str:
    """Remind judges that a shared-prefix split keeps the named outcomes, not the parent."""
    return """SPLIT OUTCOMES (mandatory)
- If one continuous source passage shares a prefix and then names independently executed outcomes, extract ONLY those named outcomes.
- Do not extract the parent, family, heading, or unsplit identity for that passage. The parent is not a member.
- A listed parent or family label does not cover the named outcomes. If the named outcomes are absent, they are missing and must be recalled; do not leave the parent in their place.
- If both the parent and the named outcomes are present, keep only the named outcomes. Never prefer the parent or family label.
"""


def _append_conservative_top_class_gate(prompt: str) -> str:
    """Add a domain-neutral eligibility gate to top-entity extraction."""
    return (
        str(prompt or "").rstrip()
        + """

TOP-CLASS ELIGIBILITY GATE (mandatory)
- A source-supported name, heading, outcome, or executable procedure is not by itself
  evidence that the candidate belongs to the selected top class.
- For every candidate, first identify positive source evidence for the defining
  characteristics of the selected top class, then test the candidate against every
  applicable exclusion and boundary in the supplied class contract.
- Do not infer class membership from nearby context, document topic, naming
  similarity, or the existence of a procedure.
- When the source leaves any defining characteristic unresolved, or supports an
  excluded/contradictory category, omit the candidate.
- Optimize conservatively: an unsupported false positive is worse than omitting an
  ambiguous candidate. Return only candidates that clearly pass the full class
  definition.

"""
        + _split_outcome_reminder()
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse one JSON object from a strict or fenced LLM response."""
    value = str(text or "").strip()
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Top-class membership judge returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Top-class membership judge response must be one JSON object")
    return payload


def _normalized_contains(haystack: str, needle: str) -> bool:
    punctuation_map = str.maketrans(
        {
            "‐": "-",
            "‑": "-",
            "‒": "-",
            "–": "-",
            "—": "-",
            "−": "-",
            "‘": "'",
            "’": "'",
            "“": '"',
            "”": '"',
        }
    )
    normalized_haystack = " ".join(
        str(haystack or "").translate(punctuation_map).split()
    )
    normalized_needle = " ".join(
        str(needle or "")
        .translate(punctuation_map)
        .strip()
        .strip("`'\"")
        .split()
    )
    return bool(normalized_needle and normalized_needle in normalized_haystack)


def _evidence_has_grounded_quote(haystack: str, evidence: str) -> bool:
    """Accept a multi-quote field when at least one substantive quote is exact."""
    if _normalized_contains(haystack, evidence):
        return True
    fragments = [
        fragment.strip()
        for line in str(evidence or "").splitlines()
        for fragment in re.split(r"\.{3}|…|(?<=[.!?])\s+", line)
        if fragment.strip()
    ]
    return any(
        len(" ".join(fragment.split())) >= 12
        and _normalized_contains(haystack, fragment)
        for fragment in fragments
    )


def _format_membership_candidate_set(candidate_lines: list[str]) -> str:
    """Render the full current candidate list for every membership call."""
    return "\n".join(
        f"candidate_{index}: {line}"
        for index, line in enumerate(candidate_lines, start=1)
    )


def _build_top_class_membership_prompt(
    *,
    top_class_iri: str,
    top_class_comment: str,
    candidate_id: str,
    candidate_line: str,
    candidate_set: str,
    source_text: str,
) -> str:
    """Build one per-candidate membership prompt with the full current set."""
    reminder = _split_outcome_reminder()
    return f"""You are an independent top-class membership judge.

For THE ONE candidate below, decide whether the source positively proves that it satisfies
the COMPLETE selected top-class contract. This is classification, not merely
groundedness checking.

Generic decision policy:
1. A heading, named outcome, or detailed procedure proves only that something was
   described; it does not prove membership in the selected class.
2. KEEP requires positive source evidence for the class-defining characteristics.
3. Compare each candidate with every relevant exclusion and boundary in the class
   contract. Explicit contradictory evidence requires REMOVE.
4. Do not infer missing characteristics from document topic, nearby entities,
   naming similarity, or common domain expectations.
5. If eligibility remains ambiguous or incompletely established, REMOVE.
6. False positives are more harmful than conservative omissions.
7. Read beyond the procedure paragraph: classifications, dimensionality, structure,
   identity, uncertainty, and explicit negative findings elsewhere in the source
   can determine eligibility.
8. A KEEP reason that would apply unchanged to any detailed procedure
   is invalid. If the reason merely says that inputs, conditions, finishing steps, or a
   named outcome are present, REMOVE.
9. Before deciding, identify the contract's discriminating class characteristics
   and explain which exact source quote proves those characteristics for this
   candidate rather than merely proving that the procedure exists.
10. The current candidate set is context only. Use it to test whether THIS candidate
    is an unsplit prefix or family label of other already-listed members. Do not
    keep or remove any other candidate in this call.
11. REMOVE this candidate only when both are true: (a) the current set already
    contains more specific named-outcome identities for the same source passage;
    and (b) this candidate's identity is only the unsplit prefix or family label of
    those already-listed outcomes.
12. If those more specific named outcomes are not already on the list, do not
    remove a sole prefix or family identity. KEEP it when it otherwise satisfies
    the class contract, so the set is not emptied.
13. When the source names independently executed outcomes of one shared passage,
    those named outcomes are the members. Never prefer the unsplit parent or
    family label for that passage.

{reminder}
Selected top class IRI:
{top_class_iri}

Top-class contract:
<<<TBOX
{top_class_comment}
TBOX
>>>

Candidate under review:
<<<CANDIDATE
{candidate_id}: {candidate_line}
CANDIDATE
>>>

Current candidate set:
<<<CANDIDATE_SET
{candidate_set}
CANDIDATE_SET
>>>

Source:
<<<SOURCE
{source_text}
SOURCE
>>>

Return exactly one JSON object:
{{
  "candidate_checks": [
    {{
      "candidate_id": "{candidate_id}",
      "decision": "keep" or "remove",
      "source_evidence": "exact source quote that proves or contradicts membership",
      "class_contract_evidence": "exact class-contract quote governing the decision",
      "exclusion_status": "cleared" or "triggered" or "unresolved",
      "ambiguity_status": "resolved" or "unresolved",
      "reason": "brief contrastive explanation focused on class eligibility"
    }}
  ]
}}
Return exactly one check for {candidate_id}. No markdown or extra text."""


def _apply_top_class_membership_checks(
    *,
    candidate_text: str,
    judge_payload: dict[str, Any],
    source_text: str,
    top_class_comment: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Keep candidates with a complete keep decision from the membership judge.

    Source-quote and class-contract quote grounding are recorded for audit
    only. Neither vetoes keep/remove: a paraphrase or near-miss quote must
    not override an explicit keep.
    """
    candidate_lines = [line.strip() for line in candidate_text.splitlines() if line.strip()]
    checks = judge_payload.get("candidate_checks")
    if not isinstance(checks, list):
        raise ValueError("Top-class membership judge requires candidate_checks")
    by_id = {
        str(item.get("candidate_id") or "").strip(): item
        for item in checks
        if isinstance(item, dict) and str(item.get("candidate_id") or "").strip()
    }
    accepted: list[str] = []
    normalized_checks: list[dict[str, Any]] = []
    for index, line in enumerate(candidate_lines, start=1):
        candidate_id = f"candidate_{index}"
        item = by_id.get(candidate_id) or {}
        decision = str(item.get("decision") or "").strip().lower()
        source_evidence = str(item.get("source_evidence") or "").strip()
        contract_evidence = str(item.get("class_contract_evidence") or "").strip()
        exclusion_status = str(item.get("exclusion_status") or "").strip().lower()
        ambiguity_status = str(item.get("ambiguity_status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        keep = bool(
            decision == "keep"
            and exclusion_status == "cleared"
            and ambiguity_status == "resolved"
            and reason
        )
        if keep:
            accepted.append(line)
        normalized_checks.append(
            {
                "candidate_id": candidate_id,
                "candidate_line": line,
                "requested_decision": decision or "missing",
                "effective_decision": "keep" if keep else "remove",
                "source_evidence_grounded": _evidence_has_grounded_quote(
                    source_text, source_evidence
                ),
                "class_contract_evidence_grounded": _evidence_has_grounded_quote(
                    top_class_comment, contract_evidence
                ),
                "exclusion_status": exclusion_status or "missing",
                "ambiguity_status": ambiguity_status or "missing",
                "reason": reason,
            }
        )
    return (
        "\n".join(accepted).strip() + ("\n" if accepted else ""),
        normalized_checks,
    )


async def _run_top_class_membership_judge(
    *,
    llm,
    candidate_text: str,
    source_text: str,
    top_class_iri: str,
    top_class_comment: str,
) -> tuple[str, dict[str, Any]]:
    """Independently adjudicate each candidate against the selected top class."""
    candidate_lines = [line.strip() for line in candidate_text.splitlines() if line.strip()]
    candidate_set = _format_membership_candidate_set(candidate_lines)

    async def judge_one_candidate(
        index: int, candidate_line: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        candidate_id = f"candidate_{index}"
        prompt = _build_top_class_membership_prompt(
            top_class_iri=top_class_iri,
            top_class_comment=top_class_comment,
            candidate_id=candidate_id,
            candidate_line=candidate_line,
            candidate_set=candidate_set,
            source_text=source_text,
        )
        current_prompt = prompt
        validation_error = ""
        for attempt in range(1, 4):
            result = await llm.ainvoke(current_prompt)
            raw = response_text(result)
            try:
                candidate_payload = _extract_json_object(raw)
            except ValueError as exc:
                candidate_payload = {}
                validation_error = str(exc)
            raw_checks = candidate_payload.get("candidate_checks")
            returned_ids = [
                str(item.get("candidate_id") or "").strip()
                for item in raw_checks
                if isinstance(item, dict)
            ] if isinstance(raw_checks, list) else []
            if (
                isinstance(raw_checks, list)
                and len(raw_checks) == 1
                and returned_ids == [candidate_id]
            ):
                response_metadata = getattr(result, "response_metadata", {}) or {}
                usage_metadata = getattr(result, "usage_metadata", {}) or {}
                return raw_checks[0], {
                    "candidate_id": candidate_id,
                    "attempts": attempt,
                    "finish_reason": response_metadata.get("finish_reason"),
                    "usage": usage_metadata,
                }
            if not validation_error:
                validation_error = (
                    "candidate_checks must contain exactly one check for "
                    f"{candidate_id}; received {returned_ids}"
                )
            current_prompt = (
                f"{prompt}\n\nYour previous response was structurally invalid: "
                f"{validation_error}. Return a corrected complete JSON object."
            )
        raise ValueError(
            f"Top-class membership judge remained invalid for {candidate_id} "
            f"after 3 attempts: {validation_error}"
        )

    candidate_results = await asyncio.gather(
        *(
            judge_one_candidate(index, line)
            for index, line in enumerate(candidate_lines, start=1)
        )
    )
    payload = {
        "candidate_checks": [result[0] for result in candidate_results],
    }
    filtered, checks = _apply_top_class_membership_checks(
        candidate_text=candidate_text,
        judge_payload=payload,
        source_text=source_text,
        top_class_comment=top_class_comment,
    )
    report = {
        "schema_version": "top-class-membership-judge.v1",
        "top_class_iri": top_class_iri,
        "candidate_checks": checks,
        "input_count": len(candidate_lines),
        "accepted_count": len([item for item in checks if item["effective_decision"] == "keep"]),
        "raw_judgement": payload,
        "per_candidate_calls": [result[1] for result in candidate_results],
    }
    return filtered, report


def _top_entity_semantic_audit_errors(
    report: dict[str, Any],
    *,
    top_class_comment: str,
) -> list[str]:
    """Keep only deductions grounded in an exact top-class contract quote."""
    contract = " ".join(str(top_class_comment or "").split())
    sources = list(report.get("judges") or [])
    adjudication = report.get("adjudication")
    if isinstance(adjudication, dict):
        sources = [adjudication]
    errors: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        for critical in source.get("critical_errors") or []:
            errors.append(f"critical: {critical}")
        for deduction in source.get("deductions") or []:
            if not isinstance(deduction, dict):
                continue
            amount = float(deduction.get("amount") or 0.0)
            if amount <= 0:
                continue
            evidence = " ".join(
                str(deduction.get("ontology_evidence") or "").strip().strip(
                    "`'\""
                ).split()
            )
            if not evidence or evidence not in contract:
                continue
            errors.append(
                f"{deduction.get('dimension') or 'semantic'}: "
                f"{deduction.get('reason') or deduction.get('obligation_kind') or evidence}"
            )
    return errors

_LISTING_WRAPPER = re.compile(r"^(?:[A-Za-z][\w.-]*-\d+\s+)\[(.*)\]\s*$")


def _top_candidate_identity_key(value: str) -> str:
    """Compare a listing line to a bare label, ignoring Prefix-N and punctuation.

    Unwrap only a complete outer listing wrapper ``Class-N [label]``. Nested
    brackets inside the label are part of the identity, not the wrapper.
    """
    text = str(value or "").strip()
    wrapped = _LISTING_WRAPPER.fullmatch(text)
    if wrapped:
        text = wrapped.group(1)
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


async def _run_legacy_top_class_omission_judge(
    *,
    llm,
    candidate_text: str,
    source_text: str,
    top_class_iri: str,
    top_class_comment: str,
    line_prefixes: list[str],
) -> tuple[str, dict[str, Any]]:
    """Add only grounded, unambiguous top-class members omitted by extraction."""
    prompt = f"""You are an independent top-class omission judge.

Search the COMPLETE source for members of the selected top class that are absent
from the current candidate list. This is an exhaustive recall audit, not a review
of whether existing candidates should be removed.

Decision policy:
1. Report a missing candidate only when exact source evidence positively proves
   the complete selected top-class contract.
2. Test every applicable exclusion and boundary in the class contract.
3. A heading, named outcome, identifier, or detailed procedure alone is
   insufficient unless it proves the defining class characteristics.
4. Do not infer eligibility from document topic, nearby candidates, naming
   similarity, or common domain expectations.
5. Do not report an existing candidate again under a paraphrase or alias.
6. If membership or distinct identity remains ambiguous, omit it.
7. Scan the entire source, including candidates and procedures appearing before,
   between, or after the currently listed candidates.
8. If one continuous passage shares a prefix then names independently executed
   outcomes, those named outcomes are the members to recall. A listed parent or
   family label does not cover them. If they are absent, report each named
   outcome as missing. Do not report the unsplit parent as a missing candidate.

{_split_outcome_reminder()}
Selected top class IRI:
{top_class_iri}

Top-class contract:
<<<TBOX
{top_class_comment}
TBOX
>>>

Current candidates:
<<<CANDIDATES
{candidate_text}
CANDIDATES
>>>

Complete source:
<<<SOURCE
{source_text}
SOURCE
>>>

Return exactly one JSON object:
{{
  "missing_candidates": [
    {{
      "candidate_label": "concise source-grounded identity label",
      "source_evidence": "exact source quote proving membership and distinct identity",
      "class_contract_evidence": "exact class-contract quote governing eligibility",
      "exclusion_status": "cleared" or "triggered" or "unresolved",
      "ambiguity_status": "resolved" or "unresolved",
      "reason": "brief contrastive explanation of why this omitted item qualifies"
    }}
  ]
}}
Return an empty missing_candidates array when the current list is complete.
No markdown or extra text."""
    result = await llm.ainvoke(prompt)
    raw = response_text(result)
    payload = _extract_json_object(raw)
    missing = payload.get("missing_candidates")
    if not isinstance(missing, list):
        raise ValueError("Top-class omission judge requires missing_candidates")

    existing_lines = [line.strip() for line in candidate_text.splitlines() if line.strip()]
    seen = {_top_candidate_identity_key(line) for line in existing_lines}
    accepted_lines = list(existing_lines)
    checks: list[dict[str, Any]] = []
    prefix = next((value for value in line_prefixes if str(value).strip()), "Entity")
    for item in missing:
        if not isinstance(item, dict):
            raise ValueError("Top-class omission judge entries must be objects")
        label = str(item.get("candidate_label") or "").strip()
        source_evidence = str(item.get("source_evidence") or "").strip()
        contract_evidence = str(item.get("class_contract_evidence") or "").strip()
        exclusion_status = str(item.get("exclusion_status") or "").strip().lower()
        ambiguity_status = str(item.get("ambiguity_status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        identity_key = _top_candidate_identity_key(label)
        source_grounded = _evidence_has_grounded_quote(source_text, source_evidence)
        contract_grounded = _evidence_has_grounded_quote(
            top_class_comment, contract_evidence
        )
        duplicate = not identity_key or identity_key in seen
        keep = bool(
            label
            and "\n" not in label
            and source_grounded
            and exclusion_status == "cleared"
            and ambiguity_status == "resolved"
            and reason
            and not duplicate
        )
        if keep:
            seen.add(identity_key)
            accepted_lines.append(f"{prefix}-{len(accepted_lines) + 1} [{label}]")
        checks.append(
            {
                "candidate_label": label,
                "effective_decision": "add" if keep else "reject",
                "source_evidence_grounded": source_grounded,
                "class_contract_evidence_grounded": contract_grounded,
                "exclusion_status": exclusion_status or "missing",
                "ambiguity_status": ambiguity_status or "missing",
                "duplicate_of_existing": duplicate,
                "reason": reason,
            }
        )
    augmented = "\n".join(accepted_lines).strip() + ("\n" if accepted_lines else "")
    return augmented, {
        "schema_version": "top-class-omission-judge.v1",
        "top_class_iri": top_class_iri,
        "input_count": len(existing_lines),
        "added_count": sum(item["effective_decision"] == "add" for item in checks),
        "candidate_checks": checks,
        "raw_judgement": payload,
    }


async def _run_top_class_omission_judge(
    *,
    llm,
    candidate_text: str,
    source_text: str,
    top_class_iri: str,
    top_class_comment: str,
    line_prefixes: list[str],
) -> tuple[str, dict[str, Any]]:
    """Run split-complete omission, falling back to the legacy judge if needed."""
    from src.extraction_runtime.steps.top_entity.omission_split_completeness import (
        run_split_complete_omission,
    )

    prefix = next((value for value in line_prefixes if str(value).strip()), "Entity")
    split_result = await run_split_complete_omission(
        llm=llm,
        candidate_text=candidate_text,
        source_text=source_text,
        top_class_iri=top_class_iri,
        top_class_comment=top_class_comment,
        line_prefix=prefix,
    )
    if split_result.get("ok"):
        existing_lines = [
            line.strip() for line in candidate_text.splitlines() if line.strip()
        ]
        applied = split_result.get("applied") or {}
        return split_result["candidate_text_out"], {
            "schema_version": "top-class-omission-judge.v2-split-complete",
            "top_class_iri": top_class_iri,
            "input_count": len(existing_lines),
            "added_count": applied.get("added_count", 0),
            "candidate_checks": applied.get("candidate_checks") or [],
            "split_completeness": split_result.get("assessment"),
            "ok": True,
            "attempts": split_result.get("attempts"),
            "raw_judgement": split_result.get("final_payload"),
            "history": split_result.get("history"),
            "fallback": None,
        }

    augmented, legacy_report = await _run_legacy_top_class_omission_judge(
        llm=llm,
        candidate_text=candidate_text,
        source_text=source_text,
        top_class_iri=top_class_iri,
        top_class_comment=top_class_comment,
        line_prefixes=line_prefixes,
    )
    legacy_report["split_completeness"] = split_result.get("assessment")
    legacy_report["split_complete_ok"] = False
    legacy_report["split_complete_attempts"] = split_result.get("attempts")
    legacy_report["split_complete_history"] = split_result.get("history")
    legacy_report["fallback"] = "legacy_omission_after_incomplete_split"
    return augmented, legacy_report

async def _revise_top_entities_against_tbox(
    *,
    llm,
    candidate_text: str,
    source_text: str,
    top_class_iri: str,
    top_class_comment: str,
    line_prefixes: List[str],
    identifier_code_regex: Optional[str],
) -> str:
    
    top_local = local_name(top_class_iri) or (line_prefixes[0] if line_prefixes else "Entity")
    top_class_comment = sanitize_source_markdown(top_class_comment or "")
    if not top_class_comment.strip():
        return candidate_text
    revision_prompt = f"""You are the validation agent for top-entity extraction.

Revise the candidate top-entity list using ONLY the T-Box class contract and source text below.

T-Box top class:
- IRI: {top_class_iri}
- Local name: {top_local}

T-Box class contract:
<<<TBOX
{top_class_comment}
TBOX
>>>

Validation rules:
- Keep a candidate only if it satisfies the T-Box class contract.
- If the T-Box excludes a candidate category, remove that candidate even if the source has a heading, title, table row, or procedure-like section for it.
- If a candidate is ambiguous under the T-Box class contract, remove it.
- Require positive evidence for the defining characteristics of the top class; evidence that only proves the candidate or procedure exists is insufficient.
- Compare each candidate against relevant exclusions using the entire source, including text outside its procedure paragraph.
- Do not infer eligibility from the document topic, nearby qualifying entities, or lexical similarity.
- Preserve the normalized output format exactly.
- If one continuous passage shares a prefix then names independently executed outcomes, keep exactly those named outcomes. Remove any unsplit parent or family label for that passage. If the list has only the parent, replace it with the named outcomes.
- Return only the corrected top-entity lines. No JSON, no markdown fences, no explanation.

{_split_outcome_reminder()}

Candidate top entities:
<<<CANDIDATES
{candidate_text}
CANDIDATES
>>>

Source text:
<<<SOURCE
{source_text}
SOURCE
>>>
"""
    result = await llm.ainvoke(revision_prompt)
    revised = response_text(result)
    revised = _normalize_top_entity_output(
        revised,
        line_prefixes=line_prefixes,
        identifier_code_regex=identifier_code_regex,
    )
    ok, errors = validate_top_entity_lines(revised, list(line_prefixes or []))
    if ok and revised.strip():
        return revised
    logger.warning(
        "⚠️  Top-entity validation agent returned unusable output; keeping original extraction: %s",
        "; ".join(errors[:3]),
    )
    return candidate_text
