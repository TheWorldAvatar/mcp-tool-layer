"""Split-completeness gate for the top-class omission judge.

``extract.py`` lazy-imports ``run_split_complete_omission`` from this module.
"""

from __future__ import annotations

import re
from typing import Any

from src.pipelines.top_entity_extraction.extract import (
    _LISTING_WRAPPER,
    _evidence_has_grounded_quote,
    _extract_json_object,
    _split_outcome_reminder,
    _top_candidate_identity_key,
)


def _is_bounded_suffix(listed_bare: str, outcome_bare: str) -> bool:
    """True when outcome is listed identity plus a leading wrapper only."""
    listed = listed_bare.strip()
    outcome = outcome_bare.strip()
    if not listed or not outcome:
        return False
    listed_cf = listed.casefold()
    outcome_cf = outcome.casefold()
    if not outcome_cf.endswith(listed_cf):
        return False
    if len(outcome_cf) == len(listed_cf):
        return True
    previous = outcome_cf[len(outcome_cf) - len(listed_cf) - 1]
    return not previous.isalnum()


def _has_compact_parenthetical_alias(listed_bare: str, outcome_bare: str) -> bool:
    """True when outcome restates the listed identity inside compact parentheses."""
    listed_key = _top_candidate_identity_key(listed_bare)
    if not listed_key:
        return False
    for inner in re.findall(r"\(([^()]*)\)", outcome_bare):
        alias = inner.strip()
        if not alias or any(character.isspace() for character in alias):
            continue
        if _top_candidate_identity_key(alias) == listed_key:
            return True
    return False


def listed_covers_named_outcome(listed_label: str, named_outcome: str) -> bool:
    """Return True when the listed line already names the same identity.

    A longer listed label covers a shorter wording of that same identity.
    A shorter listed label covers a longer wording only when the extra text
    restates the same identity (wrapper or compact parenthetical alias).
    A shorter parent or family stem never covers a more specific named outcome.
    """
    listed_key = _top_candidate_identity_key(listed_label)
    outcome_key = _top_candidate_identity_key(named_outcome)
    if not listed_key or not outcome_key:
        return False
    if listed_key == outcome_key:
        return True
    if outcome_key in listed_key:
        return True
    listed_bare = unwrap_listing_label(listed_label)
    outcome_bare = unwrap_listing_label(named_outcome)
    return _is_bounded_suffix(listed_bare, outcome_bare) or (
        _has_compact_parenthetical_alias(listed_bare, outcome_bare)
    )


def unwrap_listing_label(value: str) -> str:
    text = str(value or "").strip()
    wrapped = _LISTING_WRAPPER.fullmatch(text)
    return wrapped.group(1) if wrapped else text


def renumber_candidate_lines(lines: list[str], line_prefix: str) -> list[str]:
    return [
        f"{line_prefix}-{index} [{unwrap_listing_label(line)}]"
        for index, line in enumerate(lines, start=1)
    ]


def _clean_labels(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for raw in values:
        label = str(raw or "").strip()
        key = _top_candidate_identity_key(label)
        if not label or "\n" in label or not key or key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _candidate_lines(candidate_text: str) -> list[str]:
    return [line.strip() for line in str(candidate_text or "").splitlines() if line.strip()]


def _missing_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    missing = payload.get("missing_candidates")
    if not isinstance(missing, list):
        return []
    return [item for item in missing if isinstance(item, dict)]


def collect_claimed_split_groups(payload: dict[str, Any]) -> list[list[str]]:
    """Collect declared split groups, including virtual groups from siblings."""
    groups: list[list[str]] = []
    raw_groups = payload.get("split_groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            named = _clean_labels(group.get("named_outcomes"))
            if len(named) >= 2:
                groups.append(named)
    for item in _missing_items(payload):
        siblings = _clean_labels(item.get("sibling_outcomes_same_passage"))
        label = str(item.get("candidate_label") or "").strip()
        cluster = _clean_labels([label, *siblings])
        if len(cluster) >= 2:
            groups.append(cluster)
    return groups


def evaluate_claimed_outcomes(
    candidate_text: str,
    claimed_outcomes: list[str],
    missing_labels: list[str],
) -> dict[str, list[str]]:
    """Recompute listed coverage. A parent on the current list counts as zero."""
    listed = _candidate_lines(candidate_text)
    already_listed: list[str] = []
    still_missing: list[str] = []
    unemitted: list[str] = []
    for outcome in _clean_labels(claimed_outcomes):
        if any(listed_covers_named_outcome(line, outcome) for line in listed):
            already_listed.append(outcome)
            continue
        still_missing.append(outcome)
        if not any(
            listed_covers_named_outcome(label, outcome)
            or listed_covers_named_outcome(outcome, label)
            for label in missing_labels
        ):
            unemitted.append(outcome)
    return {
        "already_listed": already_listed,
        "still_missing": still_missing,
        "unemitted_still_missing": unemitted,
    }


def assess_split_completeness(
    candidate_text: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a machine check that claimed split siblings were fully emitted."""
    issues: list[str] = []
    if "split_groups" not in payload:
        issues.append("missing_split_groups_field")
    if "missing_candidates" not in payload or not isinstance(
        payload.get("missing_candidates"), list
    ):
        issues.append("missing_candidates_not_list")
        return {
            "complete": False,
            "issues": issues,
            "claimed_outcomes": [],
            "already_listed": [],
            "still_missing": [],
            "unemitted_still_missing": [],
        }

    groups = collect_claimed_split_groups(payload)
    claimed: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for outcome in group:
            key = _top_candidate_identity_key(outcome)
            if key in seen:
                continue
            seen.add(key)
            claimed.append(outcome)

    missing_labels = [
        str(item.get("candidate_label") or "").strip()
        for item in _missing_items(payload)
        if str(item.get("candidate_label") or "").strip()
    ]
    coverage = evaluate_claimed_outcomes(candidate_text, claimed, missing_labels)
    if coverage["unemitted_still_missing"]:
        issues.append(
            "unemitted_still_missing: "
            + "; ".join(coverage["unemitted_still_missing"])
        )
    return {
        "complete": not issues,
        "issues": issues,
        "claimed_outcomes": claimed,
        "already_listed": coverage["already_listed"],
        "still_missing": coverage["still_missing"],
        "unemitted_still_missing": coverage["unemitted_still_missing"],
        "split_group_count": len(groups),
    }


def build_split_complete_omission_prompt(
    *,
    top_class_iri: str,
    top_class_comment: str,
    candidate_text: str,
    source_text: str,
    previous_issues: list[str] | None = None,
) -> str:
    """Build the standalone omission prompt that requires complete sibling recall."""
    retry_block = ""
    if previous_issues:
        retry_block = (
            "\nPREVIOUS ATTEMPT WAS INCOMPLETE AND WAS DISCARDED. "
            "Do not apply a subset. In this response, missing_candidates "
            "must contain every still-missing sibling as its own object. "
            "Issues:\n- "
            + "\n- ".join(previous_issues)
            + "\n"
        )
    return f"""You are an independent top-class omission judge.

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
   family label counts as zero named outcomes present. If they are absent, report
   each named outcome as missing. Do not report the unsplit parent as missing.

SPLIT COMPLETENESS (mandatory)
- First declare every split group you identify.
- named_outcomes must list every independently executed named outcome of that
  passage, not a subset.
- A listed parent, family, heading, or unsplit identity never covers those
  named outcomes.
- missing_candidates must include every named outcome that is not already
  present as that same specific identity or as a more specific identity.
- Emitting only one sibling of a claimed split is invalid.
- For each missing candidate that belongs to a split, sibling_outcomes_same_passage
  must list the other independently executed named outcomes of that passage.

{_split_outcome_reminder()}
{retry_block}
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
  "split_groups": [
    {{
      "shared_passage": "short description of the shared prefix",
      "named_outcomes": ["named outcome A", "named outcome B"],
      "source_evidence": "exact source quote proving the independently executed split"
    }}
  ],
  "missing_candidates": [
    {{
      "candidate_label": "concise source-grounded identity label",
      "source_evidence": "exact source quote proving membership and distinct identity",
      "class_contract_evidence": "exact class-contract quote governing eligibility",
      "exclusion_status": "cleared" or "triggered" or "unresolved",
      "ambiguity_status": "resolved" or "unresolved",
      "sibling_outcomes_same_passage": ["other named outcomes of the same passage"],
      "reason": "brief contrastive explanation of why this omitted item qualifies"
    }}
  ]
}}
Use an empty split_groups array when the source has no independently executed split.
Return an empty missing_candidates array when the current list is complete.
No markdown or extra text."""


def _accept_missing_item(
    *,
    item: dict[str, Any],
    source_text: str,
    seen: set[str],
) -> bool:
    label = str(item.get("candidate_label") or "").strip()
    source_evidence = str(item.get("source_evidence") or "").strip()
    exclusion_status = str(item.get("exclusion_status") or "").strip().lower()
    ambiguity_status = str(item.get("ambiguity_status") or "").strip().lower()
    reason = str(item.get("reason") or "").strip()
    identity_key = _top_candidate_identity_key(label)
    return bool(
        label
        and "\n" not in label
        and identity_key
        and identity_key not in seen
        and _evidence_has_grounded_quote(source_text, source_evidence)
        and exclusion_status == "cleared"
        and ambiguity_status == "resolved"
        and reason
    )


def apply_complete_omission(
    *,
    candidate_text: str,
    payload: dict[str, Any],
    source_text: str,
    line_prefix: str = "Entity",
) -> tuple[str, dict[str, Any]]:
    """Append grounded missing candidates after a complete split judgement."""
    existing_lines = _candidate_lines(candidate_text)
    seen = {_top_candidate_identity_key(line) for line in existing_lines}
    accepted = list(existing_lines)
    checks: list[dict[str, Any]] = []
    for item in _missing_items(payload):
        label = str(item.get("candidate_label") or "").strip()
        already_listed = any(
            listed_covers_named_outcome(line, label) for line in accepted
        )
        keep = (
            not already_listed
            and _accept_missing_item(item=item, source_text=source_text, seen=seen)
        )
        if keep:
            seen.add(_top_candidate_identity_key(label))
            accepted.append(f"{line_prefix}-{len(accepted) + 1} [{label}]")
        checks.append(
            {
                "candidate_label": label,
                "effective_decision": "add" if keep else "reject",
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    numbered = renumber_candidate_lines(accepted, line_prefix)
    augmented = "\n".join(numbered).strip() + ("\n" if numbered else "")
    return augmented, {
        "added_count": sum(item["effective_decision"] == "add" for item in checks),
        "candidate_checks": checks,
    }


async def run_split_complete_omission(
    *,
    llm: Any,
    candidate_text: str,
    source_text: str,
    top_class_iri: str,
    top_class_comment: str,
    line_prefix: str = "Entity",
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Run the standalone omission judge with split-completeness retries."""
    history: list[dict[str, Any]] = []
    previous_issues: list[str] = []
    last_payload: dict[str, Any] = {}
    last_assessment: dict[str, Any] = {
        "complete": False,
        "issues": ["no_attempt"],
    }
    for attempt in range(1, max(1, max_attempts) + 1):
        prompt = build_split_complete_omission_prompt(
            top_class_iri=top_class_iri,
            top_class_comment=top_class_comment,
            candidate_text=candidate_text,
            source_text=source_text,
            previous_issues=previous_issues or None,
        )
        result = await llm.ainvoke(prompt)
        raw = result.content if hasattr(result, "content") else str(result)
        payload = _extract_json_object(raw)
        assessment = assess_split_completeness(candidate_text, payload)
        history.append(
            {
                "attempt": attempt,
                "assessment": assessment,
                "payload": payload,
            }
        )
        last_payload = payload
        last_assessment = assessment
        if assessment["complete"]:
            augmented, applied = apply_complete_omission(
                candidate_text=candidate_text,
                payload=payload,
                source_text=source_text,
                line_prefix=line_prefix,
            )
            return {
                "ok": True,
                "attempts": attempt,
                "candidate_text_in": candidate_text,
                "candidate_text_out": augmented,
                "assessment": assessment,
                "applied": applied,
                "final_payload": payload,
                "history": history,
            }
        previous_issues = list(assessment.get("issues") or [])
        still_missing = list(assessment.get("still_missing") or [])
        if still_missing:
            previous_issues.append(
                "required_missing_candidates: " + "; ".join(still_missing)
            )
    return {
        "ok": False,
        "attempts": len(history),
        "candidate_text_in": candidate_text,
        "candidate_text_out": candidate_text,
        "assessment": last_assessment,
        "applied": {"added_count": 0, "candidate_checks": []},
        "final_payload": last_payload,
        "history": history,
    }
