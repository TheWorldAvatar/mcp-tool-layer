"""
Top entity extraction pipeline step.

This module extracts top-level entities from papers
using prompts defined in the main ontology configuration.
"""

import os
import sys
import json
import re
from pathlib import Path
from typing import Any, List, Optional
from rdflib import Graph, RDFS, URIRef
# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model
from src.pipelines.structured_extraction import validate_top_entity_lines
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_ontology_publish_contract,
)
from src.agents.scripts_and_prompts_generation.llm_extraction_judge import (
    judge_extraction_semantics,
)
from src.agents.scripts_and_prompts_generation.llm_global_context_resolver import (
    inject_global_context_brief,
    render_global_context_brief,
    resolve_global_context,
)
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
import asyncio

logger = get_logger("pipeline", "top_entity_extraction")


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
            raw = result.content if hasattr(result, "content") else str(result)
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


def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Generation in this repo typically writes to `ai_generated_contents_candidate/`,
    while older pipeline code may reference `ai_generated_contents/`.
    This resolver prefers candidate if available, then falls back.
    """
    path = (path or "").replace("\\", "/")
    candidates: list[str] = []
    override_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip().replace("\\", "/").rstrip("/")
    strict_root = os.environ.get("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT") == "1"
    if path.startswith("ai_generated_contents/"):
        if override_root:
            candidates.append(path.replace("ai_generated_contents", override_root, 1))
        if not strict_root:
            candidates.append(path.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1))
            candidates.append(path)
    elif path.startswith("ai_generated_contents_candidate/"):
        if override_root:
            candidates.append(path.replace("ai_generated_contents_candidate", override_root, 1))
        if not strict_root:
            candidates.append(path)
            candidates.append(path.replace("ai_generated_contents_candidate/", "ai_generated_contents/", 1))
    else:
        candidates.append(path)

    for p in candidates:
        if p and os.path.exists(p):
            return p
    if strict_root:
        raise FileNotFoundError(f"Required generated artifact is missing: {candidates[0]}")
    return candidates[0]


def load_meta_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    """Load the meta task configuration."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_extraction_prompt(ontology_name: str, iteration: int = 1) -> str:
    """
    Load extraction prompt from markdown file.
    
    Args:
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        iteration: Iteration number (default: 1)
        
    Returns:
        The prompt text
    """
    prompt_path = resolve_generated_file(
        f"ai_generated_contents/prompts/{ontology_name}/EXTRACTION_ITER_{iteration}.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(f"Extraction prompt not found: {prompt_path}")

    from src.utils.source_text_sanitize import sanitize_source_markdown

    with open(prompt_path, "r", encoding="utf-8") as f:
        # Sanitize template text so Greek / super-subscripts in prompt artifacts
        # cannot re-enter the extraction channel after source-MD cleaning.
        return sanitize_source_markdown(f.read())


def bind_paper_content(prompt_template: str, paper_content: str) -> str:
    """Bind pipeline-owned source text without requiring a template placeholder.

    Generated prompts may explicitly place ``{paper_content}`` beside their task
    instructions. For legacy or externally supplied prompt artifacts that omit
    that marker, the extraction pipeline still owns the source-text channel and
    appends an unambiguous runtime boundary. This keeps the prompt artifact
    immutable during execution while ensuring the model always receives the
    source it is expected to extract from.
    """
    if "{paper_content}" in prompt_template:
        return prompt_template.replace("{paper_content}", paper_content)
    return (
        prompt_template.rstrip()
        + "\n\n---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----\n"
        + paper_content
        + "\n---- PIPELINE-INJECTED SOURCE TEXT: END ----\n"
    )


def _top_entities_txt_is_stale(existing: str, invalidate_substrings: list) -> bool:
    """True if cached top_entities.txt should be discarded (wrong domain / placeholder)."""
    if not (existing or "").strip():
        return True
    low = existing.lower()
    for sub in invalidate_substrings or []:
        if sub and str(sub).lower() in low:
            return True
    return False


def _count_hint_lines(content: str, prefixes: Optional[List[str]]) -> List[str]:
    prefixes = tuple(p for p in (prefixes or []) if p)
    if not prefixes:
        prefixes = ("Entity",)
    out: List[str] = []
    for line in content.split("\n"):
        s = line.strip()
        if s and any(s.startswith(p) for p in prefixes):
            out.append(s)
    return out


def _normalize_top_entity_output(
    content: str,
    *,
    line_prefixes: Optional[List[str]] = None,
    identifier_code_regex: Optional[str] = None,
) -> str:
    """Normalize verbose top-entity lines to stable concise identifiers when possible."""
    text = str(content or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    if text[:1] in {"[", "{"}:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("entities"), list):
            items = payload["entities"]
        elif isinstance(payload, dict) and any(
            isinstance(value, list) for value in payload.values()
        ):
            items = [
                {"class": class_local, **item}
                for class_local, values in payload.items()
                if not line_prefixes or class_local in line_prefixes
                for item in (values if isinstance(values, list) else [])
                if isinstance(item, dict)
            ]
        else:
            items = payload if isinstance(payload, list) else [payload]
        structured_lines: list[str] = []
        default_prefix = next(
            iter(tuple(p for p in (line_prefixes or []) if p)), "Entity"
        )
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            prefix = str(
                item.get("class")
                or item.get("type")
                or item.get("entity_type")
                or default_prefix
            ).rsplit(":", 1)[-1]
            if line_prefixes and prefix not in line_prefixes:
                prefix = default_prefix
            label = str(
                item.get("entity_label")
                or item.get("label")
                or item.get("name")
                or item.get("identifier")
                or item.get("id")
                or ""
            ).strip()
            if label:
                structured_lines.append(f"{prefix}-{index} [{label}]")
        if structured_lines:
            text = "\n".join(structured_lines)
    normalized: list[str] = []
    seen: set[str] = set()
    prefixes = tuple(p for p in (line_prefixes or []) if p)
    if not prefixes:
        prefixes = ("Entity",)
    try:
        code_re = re.compile(identifier_code_regex or r"\b[A-Z][A-Z0-9]{1,}(?:[-_]\d+[A-Za-z0-9]*)\b")
    except re.error:
        code_re = re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:[-_]\d+[A-Za-z0-9]*)\b")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched_prefix = next((p for p in prefixes if line.startswith(p)), "")
        if not matched_prefix:
            normalized.append(line)
            continue
        candidates = code_re.findall(line)
        code = candidates[-1] if candidates else ""
        if code:
            key = code.upper()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(f"{matched_prefix}-{len(seen)} [{code}]")
        else:
            line_match = re.match(
                rf"^{re.escape(matched_prefix)}-(\d+)\s+(?!\[)(.+?)\s*$",
                line,
            )
            if line_match:
                label = line_match.group(2).strip()
                key = f"{matched_prefix}:{label}"
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(f"{matched_prefix}-{len(seen)} [{label}]")
                continue
            if line in seen:
                continue
            seen.add(line)
            normalized.append(line)
    return "\n".join(normalized).strip() + ("\n" if normalized else "")


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
    raw = result.content if hasattr(result, "content") else str(result)
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
    from src.pipelines.top_entity_extraction.omission_split_completeness import (
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


def _local_name(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _top_entity_line_prefixes(
    runtime_policies: dict[str, Any],
    top_class_iri: str,
) -> list[str]:
    """Resolve output prefixes from runtime policy or the active T-Box class."""
    extraction = runtime_policies.get("top_entity_extraction") or {}
    prefixes = [
        str(value).strip()
        for value in extraction.get("count_lines_starting_with") or []
        if str(value).strip()
    ]
    if prefixes:
        return prefixes
    iter1 = runtime_policies.get("iter1_top_entity_kg") or {}
    rules = iter1.get("prompt_rules") or {}
    configured = str(rules.get("top_level_entity_name") or "").strip()
    if configured:
        return [configured]
    derived = _local_name(top_class_iri)
    return [derived] if derived else []


def _load_top_entity_contract(
    meta_task_config_path: str, ontology_name: str
) -> tuple[str, str]:
    """Load the selected top-role IRI and its generic T-Box comment contract."""
    try:
        contract = build_ontology_publish_contract(
            meta_task_config_path=meta_task_config_path,
            ontology_name=ontology_name,
        )
    except Exception:
        return "", ""
    role = contract.get("top_role") or {}
    if str(role.get("status") or "") != "known":
        return "", ""
    class_iri = str(role.get("class_iri") or "").strip()
    return class_iri, _load_tbox_class_comment(
        meta_task_config_path,
        ontology_name,
        class_iri,
        publish_contract=contract,
    )


def _load_tbox_class_comment(
    meta_task_config_path: str,
    ontology_name: str,
    class_iri: str,
    *,
    publish_contract: Optional[dict[str, Any]] = None,
) -> str:
    """Read rdfs:comment for any pipeline-selected class without domain rules."""
    contract = publish_contract
    if not isinstance(contract, dict):
        try:
            contract = build_ontology_publish_contract(
                meta_task_config_path=meta_task_config_path,
                ontology_name=ontology_name,
            )
        except Exception:
            return ""
    tbox_path = str(
        contract.get("resolved_ttl_file")
        or contract.get("ttl_file")
        or ""
    ).strip()
    if not class_iri or not tbox_path:
        return ""
    try:
        graph = Graph()
        graph.parse(tbox_path, format="turtle")
        comments = [
            str(value)
            for value in graph.objects(URIRef(class_iri), RDFS.comment)
            if str(value).strip()
        ]
    except Exception:
        return ""
    return "\n\n".join(comments).strip()


def _resolve_selected_top_class_iri(
    meta_task_config_path: str,
    ontology_name: str,
    selected_class_local: str,
) -> str:
    """Resolve the pipeline-selected class local without selecting a class."""
    selected = str(selected_class_local or "").strip()
    if not selected:
        return ""
    contract = build_ontology_publish_contract(
        meta_task_config_path=meta_task_config_path,
        ontology_name=ontology_name,
    )
    matches = [
        str(item.get("class_iri") or "")
        for item in contract.get("classes") or []
        if _local_name(str(item.get("class_iri") or "")) == selected
    ]
    if len(matches) != 1:
        raise ValueError(
            "Pipeline-selected top class must resolve to exactly one T-Box class: "
            f"{selected!r} matched {matches}"
        )
    return matches[0]


def _write_top_class_selection(
    *,
    doi_dir: str,
    class_local: str,
    class_iri: str,
    source: str,
) -> None:
    """Persist the authoritative top-class choice made by the pipeline."""
    selection = {
        "schema_version": "top-entity-selection.v1",
        "class_local": str(class_local),
        "class_iri": str(class_iri),
        "source": str(source),
    }
    Path(doi_dir, "top_entity_selection.json").write_text(
        json.dumps(selection, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _persist_and_validate_top_class_selection(
    *,
    doi_dir: str,
    class_local: str,
    class_iri: str,
    source: str = "pipeline_runtime_policy",
) -> bool:
    """Enforce top-class lineage as a successful extraction postcondition."""
    if not str(class_local or "").strip() or not str(class_iri or "").strip():
        return False
    _write_top_class_selection(
        doi_dir=doi_dir,
        class_local=class_local,
        class_iri=class_iri,
        source=source,
    )
    try:
        persisted = json.loads(
            Path(doi_dir, "top_entity_selection.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        str(persisted.get("class_local") or "").strip() == str(class_local).strip()
        and str(persisted.get("class_iri") or "").strip() == str(class_iri).strip()
    )


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
    from src.utils.source_text_sanitize import sanitize_source_markdown

    top_local = _local_name(top_class_iri) or (line_prefixes[0] if line_prefixes else "Entity")
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
    revised = result.content if hasattr(result, "content") else str(result)
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


async def extract_top_entities(
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    *,
    invalidate_top_entities_txt_substrings: Optional[List[str]] = None,
    count_lines_starting_with: Optional[List[str]] = None,
    identifier_code_regex: Optional[str] = None,
    top_class_iri: str = "",
    top_class_comment: str = "",
) -> bool:
    """
    Extract top-level entities from the stitched markdown.
    
    Args:
        doi_hash: DOI hash identifier
        data_dir: Base data directory
        ontology_name: Name of the ontology to use
        
    Returns:
        True if extraction succeeded
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    stitched_md = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    text_md = os.path.join(doi_dir, f"{doi_hash}_text.md")
    raw_md = os.path.join(doi_dir, f"{doi_hash}.md")
    output_file = os.path.join(doi_dir, "top_entities.txt")
    
    # Check if already exists (skip only when content looks valid for this ontology).
    if os.path.exists(output_file):
        try:
            existing = Path(output_file).read_text(encoding="utf-8")
        except Exception:
            existing = ""
        low = existing.lower()
        placeholder_doc = "provide the document" in low
        stale_wrong_domain = _top_entities_txt_is_stale(existing, invalidate_top_entities_txt_substrings or [])
        ok_existing, _ = validate_top_entity_lines(
            existing,
            list(count_lines_starting_with or []),
        )
        global_context_cache = Path(doi_dir) / "global_procedure_context.json"
        if (
            existing.strip()
            and ok_existing
            and not placeholder_doc
            and not stale_wrong_domain
            and global_context_cache.exists()
        ):
            selected_local = (
                _local_name(top_class_iri)
                or (count_lines_starting_with or [""])[0]
            )
            _write_top_class_selection(
                doi_dir=doi_dir,
                class_local=selected_local,
                class_iri=top_class_iri,
                source="pipeline_runtime_policy",
            )
            logger.info(f"⏭️  Top entities already extracted: {output_file}")
            return True
        if (
            existing.strip()
            and ok_existing
            and not placeholder_doc
            and not stale_wrong_domain
        ):
            logger.info(
                "♻️  Re-running top extraction to resolve missing global procedure context"
            )
        elif stale_wrong_domain:
            logger.warning(
                f"⚠️  Stale/wrong-domain top_entities.txt; re-running extraction: {output_file}"
            )
        else:
            logger.warning(
                f"⚠️  Existing top_entities.txt looks invalid/placeholder; re-running extraction: {output_file}"
            )
    
    # Read paper content (robust fallback chain) and append supporting information
    # when available; procedures are often specified only in SI.
    vision_md = os.path.join(doi_dir, f"{doi_hash}_vision.md")
    paper_content = ""
    candidates = [vision_md, stitched_md, text_md, raw_md]
    chosen = None
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            c = Path(p).read_text(encoding="utf-8")
        except Exception:
            c = ""
        if c and c.strip():
            paper_content = c
            chosen = p
            break
    if not paper_content.strip():
        logger.error(
            f"❌ No usable paper content found. Tried: {', '.join([p for p in candidates if p])}"
        )
        return False
    logger.info(f"📄 Using paper content from: {chosen}")

    for si_name in (
        f"{doi_hash}_si_text.md",
        f"{doi_hash}_si_vision.md",
        f"{doi_hash}_si.md",
        f"{doi_hash}_si_tables.md",
    ):
        si_path = os.path.join(doi_dir, si_name)
        if not os.path.exists(si_path):
            continue
        try:
            si_text = Path(si_path).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {si_path}: {e}")
            continue
        if si_text and si_text.strip():
            paper_content += f"\n\n# Supporting Information: {si_name}\n\n{si_text}"
            logger.info(f"📎 Appended supporting information from: {si_path}")
    
    # Load extraction prompt
    logger.info(f"📋 Loading extraction prompt for {ontology_name} iteration 1")
    try:
        extraction_prompt = load_extraction_prompt(ontology_name, iteration=1)
    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        return False
    
    # The pipeline owns source loading and injection. A prompt can declare the
    # preferred placement through {paper_content}, but an externally supplied
    # artifact is still executable without mutating it to add that marker.
    from src.utils.source_text_sanitize import sanitize_source_markdown

    paper_content = sanitize_source_markdown(paper_content)
    top_class_comment = sanitize_source_markdown(top_class_comment or "")
    try:
        tbox_path = Path(project_root) / "data" / "ontologies" / f"{ontology_name}.ttl"
        tbox_contract = tbox_path.read_text(encoding="utf-8")
        global_context = resolve_global_context(
            source_text=paper_content,
            tbox_contract=tbox_contract,
            model=get_extraction_model("advanced_model"),
            cache_path=Path(doi_dir) / "global_procedure_context.json",
        )
        global_context_brief = render_global_context_brief(global_context)
    except Exception as exc:
        logger.warning(
            "⚠️  Global procedure context resolution failed; "
            "continuing extraction without a shared-context brief: %s",
            exc,
        )
        global_context_brief = ""
    full_prompt = sanitize_source_markdown(
        bind_paper_content(extraction_prompt, paper_content)
    )
    full_prompt = inject_global_context_brief(full_prompt, global_context_brief)
    full_prompt = _append_conservative_top_class_gate(full_prompt)

    # Save full prompt for reproducibility
    prompt_save_path = os.path.join(doi_dir, "iter1_full_prompt.md")
    os.makedirs(doi_dir, exist_ok=True)
    with open(prompt_save_path, "w", encoding="utf-8") as f:
        f.write(full_prompt)
    logger.info(f"💾 Full prompt saved to: {prompt_save_path}")
    
    # Get model from config
    model_key = "iter1_hints"
    model_name = get_extraction_model(model_key)
    logger.info(f"🤖 Using model: {model_name} (from {model_key})")
    
    # Create LLM
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    # Judges must use the same model as the extraction they evaluate.
    membership_model_name = model_name
    membership_llm = LLMCreator(
        model=membership_model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    omission_llm = LLMCreator(
        model=membership_model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    
    # Extract with cumulative semantic feedback. Top-entity identity is the
    # highest-priority extraction layer, so downstream work must not start from
    # a merely well-formed but semantically incomplete list.
    max_retries = 5
    feedback_history: list[str] = []
    last_structurally_valid_content = ""
    for attempt in range(max_retries):
        try:
            logger.info(f"🔍 Extracting top entities (attempt {attempt + 1}/{max_retries})...")
            effective_prompt = full_prompt
            if feedback_history:
                effective_prompt += _format_top_entity_feedback_history(
                    feedback_history
                )
            result = await llm.ainvoke(effective_prompt)
            
            # Extract content
            content = result.content if hasattr(result, 'content') else str(result)
            content = _normalize_top_entity_output(
                content,
                line_prefixes=count_lines_starting_with or [],
                identifier_code_regex=identifier_code_regex,
            )
            content = await _revise_top_entities_against_tbox(
                llm=llm,
                candidate_text=content,
                source_text=paper_content,
                top_class_iri=top_class_iri,
                top_class_comment=top_class_comment,
                line_prefixes=list(count_lines_starting_with or []),
                identifier_code_regex=identifier_code_regex,
            )
            content, omission_report = await _run_top_class_omission_judge(
                llm=omission_llm,
                candidate_text=content,
                source_text=paper_content,
                top_class_iri=top_class_iri,
                top_class_comment=top_class_comment,
                line_prefixes=list(count_lines_starting_with or []),
            )
            omission_report["model"] = membership_model_name
            Path(
                doi_dir,
                f"top_entities.omission_judge.attempt_{attempt + 1}.json",
            ).write_text(
                json.dumps(omission_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            content, membership_report = await _run_top_class_membership_judge(
                llm=membership_llm,
                candidate_text=content,
                source_text=paper_content,
                top_class_iri=top_class_iri,
                top_class_comment=top_class_comment,
            )
            membership_report["model"] = membership_model_name
            Path(
                doi_dir,
                f"top_entities.membership_judge.attempt_{attempt + 1}.json",
            ).write_text(
                json.dumps(membership_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            ok_lines, line_errors = validate_top_entity_lines(
                content,
                list(count_lines_starting_with or []),
            )
            if not ok_lines:
                raise ValueError(
                    "Top entity extraction failed normalized line validation: "
                    + "; ".join(line_errors[:3])
                )
            if content.strip():
                last_structurally_valid_content = content
            semantic_report = await asyncio.to_thread(
                judge_extraction_semantics,
                document_text=paper_content,
                ontology_contract={
                    "top_class": {
                        "iri": top_class_iri,
                        "local_name": (
                            _local_name(top_class_iri)
                            or (count_lines_starting_with or ["Entity"])[0]
                        ),
                        "class_contract": top_class_comment,
                    },
                    "audit_scope": (
                        "Independently verify that every retained candidate has positive "
                        "source evidence for the defining characteristics of the selected "
                        "top class. Merely proving that a named outcome or procedure exists "
                        "is insufficient. Test every relevant exclusion and remove any "
                        "candidate whose class eligibility remains ambiguous. If one "
                        "continuous passage names independently executed outcomes, those "
                        "named outcomes are the members; an unsplit parent or family label "
                        "for that passage is not a valid member."
                    ),
                    "deduction_evidence_policy": (
                        "Every non-zero deduction must put an exact verbatim substring from "
                        "top_class.class_contract in ontology_evidence. A paraphrase, generic "
                        "class-name assertion, conditional statement, or external definition "
                        "is invalid evidence. Explicit class exclusions override broad notions "
                        "of process or procedure."
                    ),
                },
                extracted_content=content,
                models=[model_name],
            )
            semantic_report["top_class_membership_judge"] = membership_report
            semantic_report["top_class_omission_judge"] = omission_report
            semantic_audit_path = Path(
                doi_dir, f"top_entities.semantic_audit.attempt_{attempt + 1}.json"
            )
            semantic_errors = _top_entity_semantic_audit_errors(
                semantic_report,
                top_class_comment=top_class_comment,
            )
            semantic_report["top_scope_acceptance"] = {
                "accepted": not semantic_errors,
                "grounded_contract_errors": semantic_errors,
                "policy": "exact_top_class_contract_evidence",
            }
            semantic_audit_path.write_text(
                json.dumps(semantic_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if semantic_errors:
                raise ValueError(
                    "Top-entity semantic audit rejected the candidate:\n"
                    + json.dumps(
                        {
                            "top_scope_acceptance": semantic_report.get(
                                "top_scope_acceptance"
                            ),
                        },
                        ensure_ascii=False,
                    )
                )
            
            # Save result (sanitize Greek/super-subscripts so filenames & CCDC stay ASCII-safe)
            from src.utils.source_text_sanitize import sanitize_source_markdown

            content = sanitize_source_markdown(content)
            os.makedirs(doi_dir, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(content)
            Path(doi_dir, "top_entities.semantic_audit.json").write_text(
                json.dumps(semantic_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            selected_local = (
                _local_name(top_class_iri)
                or (count_lines_starting_with or [""])[0]
            )
            _write_top_class_selection(
                doi_dir=doi_dir,
                class_local=selected_local,
                class_iri=top_class_iri,
                source="pipeline_runtime_policy",
            )
            
            logger.info(f"✅ Top entities saved to: {output_file}")
            
            # Log extracted entities
            lines = _count_hint_lines(content, count_lines_starting_with or [])
            logger.info(f"   Found {len(lines)} top-level entity line(s) (prefix filter)")
            for line in lines[:5]:  # Show first 5
                logger.info(f"   - {line[:80]}...")
            if len(lines) > 5:
                logger.info(f"   ... and {len(lines) - 5} more")
            
            return True
            
        except Exception as e:
            feedback_history.append(str(e))
            if attempt < max_retries - 1:
                logger.warning(f"⚠️  Attempt {attempt + 1} failed: {e}, retrying...")
                await asyncio.sleep(5 * (attempt + 1))
            else:
                if last_structurally_valid_content:
                    from src.utils.source_text_sanitize import sanitize_source_markdown

                    fallback_content = sanitize_source_markdown(
                        last_structurally_valid_content
                    )
                    Path(output_file).write_text(
                        fallback_content,
                        encoding="utf-8",
                    )
                    warning = {
                        "schema_version": "audit-exhaustion-warning.v1",
                        "kind": "top_entity_audit_exhausted",
                        "message": str(e),
                        "attempt_budget": max_retries,
                        "feedback_history": feedback_history,
                        "policy": (
                            "preserve_final_deterministically_valid_candidate"
                        ),
                    }
                    Path(
                        doi_dir, "top_entities.audit_exhaustion_warning.json"
                    ).write_text(
                        json.dumps(warning, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    selected_local = (
                        _local_name(top_class_iri)
                        or (count_lines_starting_with or [""])[0]
                    )
                    _write_top_class_selection(
                        doi_dir=doi_dir,
                        class_local=selected_local,
                        class_iri=top_class_iri,
                        source="pipeline_runtime_policy",
                    )
                    logger.warning(
                        "⚠️  Top-entity audit budget exhausted after %d attempts; "
                        "preserving the final normalized, schema-valid candidate.",
                        max_retries,
                    )
                    return True
                logger.error(f"❌ Extraction failed after {max_retries} attempts: {e}")
                return False
    
    return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the top entity extraction pipeline step.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if extraction succeeded
    """
    data_dir = config.get("data_dir", "data")
    
    logger.info(f"▶️  Top Entity Extraction: {doi_hash}")
    
    # Load meta config to get main ontology
    try:
        meta_task_config_path = config.get(
            "meta_task_config", "configs/meta_task/meta_task_config.json"
        )
        meta_config = load_meta_config(meta_task_config_path)
        main_ontology = meta_config.get("ontologies", {}).get("main", {})
        ontology_name = main_ontology.get("name", "ontosynthesis")
        logger.info(f"   Using ontology: {ontology_name}")
        top_class_iri, top_class_comment = _load_top_entity_contract(
            meta_task_config_path, ontology_name
        )
        policies = (main_ontology.get("runtime_policies") or {}) if isinstance(main_ontology, dict) else {}
        te_pol = (policies.get("top_entity_extraction") or {}) if isinstance(policies, dict) else {}
        invalidate_subs = te_pol.get("invalidate_top_entities_txt_substrings") or []
        count_prefixes = _top_entity_line_prefixes(
            policies if isinstance(policies, dict) else {},
            top_class_iri,
        )
        selected_class_local = (
            str(count_prefixes[0]).strip() if len(count_prefixes or []) == 1 else ""
        )
        if not top_class_iri and selected_class_local:
            top_class_iri = _resolve_selected_top_class_iri(
                meta_task_config_path,
                ontology_name,
                selected_class_local,
            )
        if top_class_iri and not top_class_comment:
            top_class_comment = _load_tbox_class_comment(
                meta_task_config_path,
                ontology_name,
                top_class_iri,
            )
        identifier_code_regex = te_pol.get("identifier_code_regex")
    except Exception as e:
        logger.error(f"❌ Failed to load meta config: {e}")
        return False
    
    # Run extraction
    try:
        success = asyncio.run(
            extract_top_entities(
                doi_hash,
                data_dir,
                ontology_name,
                invalidate_top_entities_txt_substrings=invalidate_subs,
                count_lines_starting_with=count_prefixes,
                identifier_code_regex=identifier_code_regex,
                top_class_iri=top_class_iri,
                top_class_comment=top_class_comment,
            )
        )
        
        if success:
            selected_class_local = (
                _local_name(top_class_iri)
                or (str(count_prefixes[0]).strip() if len(count_prefixes or []) == 1 else "")
            )
            selection_ok = _persist_and_validate_top_class_selection(
                doi_dir=os.path.join(data_dir, doi_hash),
                class_local=selected_class_local,
                class_iri=top_class_iri,
            )
            if not selection_ok:
                logger.error(
                    "❌ Top Entity Extraction cannot succeed without complete "
                    "pipeline top-class selection lineage"
                )
                return False
            logger.info(f"✅ Top Entity Extraction completed: {doi_hash}")
        else:
            logger.error(f"❌ Top Entity Extraction failed: {doi_hash}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Top Entity Extraction failed with exception: {e}")
        return False


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        print(f"Running top entity extraction for DOI hash: {test_hash}")
        success = run_step(test_hash, test_config)
        print(f"Top entity extraction {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.top_entity_extraction.extract <doi_hash>")

