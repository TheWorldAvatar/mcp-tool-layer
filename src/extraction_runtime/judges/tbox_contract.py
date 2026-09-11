"""Locked T-Box contract critic used by non-semantic extraction revision."""

from __future__ import annotations

import json
from typing import Any

from src.extraction_runtime.judges.llm import setup_judge_llm
from src.extraction_runtime.llm import response_text, strip_code_fences
from src.extraction_runtime.locked_mechanisms import CONTRACT_CRITIC_FORMAT_ATTEMPTS


def extract_tbox_audit_contract(prompt_text: str) -> str:
    lines = (prompt_text or "").splitlines()
    selected: list[str] = []
    for line in lines:
        if (
            "Current Target Entity" in line
            or line.lstrip().startswith("- Label:")
            or line.lstrip().startswith("- IRI:")
            or line.lstrip().startswith("- Stage-owned ")
        ):
            selected.append(line)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "Materializable Hint Contract:"
        ),
        None,
    )
    if start is None:
        return prompt_text
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].strip().endswith("Output Contract:")
        ),
        len(lines),
    )
    selected.extend(lines[start:end])
    return "\n".join(dict.fromkeys(selected)).strip() or prompt_text


def build_tbox_contract_audit_prompt(
    *,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
) -> str:
    focused_contract = extract_tbox_audit_contract(original_prompt)
    return (
        "You are a strict extraction-contract critic. Compare the CANDIDATE HINT "
        "against the SOURCE and the T-BOX-DERIVED CONTRACT embedded in the ORIGINAL "
        "EXTRACTION PROMPT.\n\n"
        "Perform exactly two narrow passes and report nothing else.\n\n"
        "PASS A — accepted numeric scalar-value coverage:\n"
        "- Consider only classes explicitly listed as stage-owned.\n"
        "- Before looking at the candidate, scan SOURCE from start to finish and inventory "
        "EVERY explicit numeric lexical value. For each value, ask whether any field accepted "
        "by a stage-owned class can semantically carry that value according to its contract "
        "comment. If yes, emit a coverage row, even when the corresponding entity/event is "
        "entirely absent from the candidate.\n"
        "- Emit one coverage_checks row for EVERY accepted source numeric value, whether "
        "present or missing. Preserve exact values including units, multiplicity, signs, "
        "ranges, and qualifiers. The value must contain at least one digit.\n"
        "- Do not require a value when the class contract has no field capable of carrying it.\n\n"
        "- `contract_evidence` must quote exact words from that field's `semantic contract` "
        "row which establish the numeric value's role. The class `accepts` list proves only "
        "placement and is never sufficient semantic evidence. If no semantic contract row "
        "supports the role, emit no coverage row.\n\n"
        "PASS B — mechanical field placement:\n"
        "- For every field under every candidate top-level class section, mechanically check "
        "whether that exact field occurs in that class's `accepts fields` list.\n"
        "- Emit one field_violations row for every field not accepted by its containing class. "
        "If another listed class accepts that exact field, record that owner class.\n\n"
        "Respect accumulated prior hints as an identity registry and do not demand duplicate "
        "prior-stage entities. Do NOT report ordering, duplicate identities, missing links, "
        "preferred modeling, unsupported assertions, or new events that the source does not "
        "explicitly state. Every row must quote concrete SOURCE or CONTRACT evidence. Never "
        "propose N/A or another sentinel for an unstated value. Audit every applicable entity "
        "and field; do not stop after the first issue.\n\n"
        "Return JSON only with this exact schema:\n"
        '{"coverage_checks": ['
        '{"class": "class local", "field": "accepted field", "entity": "source identity", '
        '"value": "exact numeric lexical value", "source_evidence": "exact source quote", '
        '"contract_evidence": "exact field contract quote", '
        '"present_in_candidate": true}], '
        '"field_violations": ['
        '{"class": "containing class local", "field": "misplaced field", '
        '"owner_class": "correct class local or empty string", '
        '"contract_evidence": "contract quote"}]}\n\n'
        "ORIGINAL EXTRACTION PROMPT:\n<<<PROMPT\n"
        f"{focused_contract}\nPROMPT\n>>>\n\n"
        "SOURCE:\n<<<SOURCE\n"
        f"{source_text}\nSOURCE\n>>>\n\n"
        "CANDIDATE HINT:\n<<<CANDIDATE\n"
        f"{candidate_text}\nCANDIDATE\n>>>\n"
    )


def build_tbox_contract_audit_refinement_prompt(
    *,
    audit_prompt: str,
    draft_checklist: str,
) -> str:
    return (
        "You are the completeness verifier for an extraction-contract checklist.\n"
        "The draft often makes one critical mistake: it inventories only entities or "
        "numeric values already present in the candidate. Ignore the candidate first: re-scan "
        "the SOURCE line by line, enumerate every numeric lexical value, and add each value "
        "that a stage-owned field contract can carry even when its entity is entirely absent "
        "from the candidate. Do not skip repeated-quantity or multiplicity expressions; keep "
        "the complete lexical expression (for example, `N × Q unit`) rather than reducing it "
        "to one repetition. Apply every Linked Target Scalar Contract even when the linking "
        "operation belongs to a later stage, provided the linked target class is stage-owned. Then "
        "re-scan every candidate class/field pair for fields "
        "not accepted by that containing class. Preserve grounded valid rows, remove "
        "ungrounded rows, and return only the exact JSON schema requested by the audit.\n\n"
        f"{audit_prompt}\n\n"
        "DRAFT CHECKLIST TO COMPLETE:\n<<<CHECKLIST\n"
        f"{draft_checklist}\nCHECKLIST\n>>>\n"
    )


def build_tbox_contract_multiplicity_audit_prompt(*, audit_prompt: str) -> str:
    return (
        "Run only a repeated-quantity completeness pass over the audit below. Scan SOURCE "
        "for every numeric lexical expression that encodes repetition or multiplicity "
        "(for example `N × Q unit`, `Q unit each`, or an explicit repeated count). If a "
        "stage-owned field or Linked Target Scalar Contract can carry the complete value, "
        "emit its coverage_checks row even when the target entity is absent from the "
        "candidate. Do not shorten the expression to one repetition. Return field_violations "
        "as an empty list and return only the exact JSON checklist schema.\n\n"
        f"{audit_prompt}"
    )


def parse_tbox_contract_audit(text: str) -> list[str]:
    try:
        payload = json_object(text)
    except Exception as exc:
        raise ValueError(f"contract critic returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "coverage_checks",
        "field_violations",
    }:
        raise ValueError(
            "contract critic must return exactly coverage_checks and field_violations"
        )
    coverage_checks = payload.get("coverage_checks")
    field_violations = payload.get("field_violations")
    if not isinstance(coverage_checks, list) or not isinstance(field_violations, list):
        raise ValueError("contract critic check collections must be lists")
    normalized: list[str] = []
    for index, check in enumerate(coverage_checks):
        if not isinstance(check, dict):
            raise ValueError(f"coverage_checks[{index}] must be an object")
        required = {
            "class",
            "field",
            "entity",
            "value",
            "source_evidence",
            "contract_evidence",
            "present_in_candidate",
        }
        if set(check) != required:
            raise ValueError(f"coverage_checks[{index}] keys differ from required schema")
        if not all(
            str(check.get(key) or "").strip()
            for key in required - {"present_in_candidate"}
        ):
            raise ValueError(f"coverage_checks[{index}] has empty required values")
        present = check["present_in_candidate"]
        if not isinstance(present, bool):
            raise ValueError(f"coverage_checks[{index}].present_in_candidate must be bool")
        if not present:
            normalized.append(
                "MISSING_ACCEPTED_SOURCE_VALUE: "
                f"`{check['class']}.{check['field']}` for `{check['entity']}` "
                f"must preserve `{check['value']}` [source: {check['source_evidence']}] "
                f"[contract: {check['contract_evidence']}]"
            )
    for index, violation in enumerate(field_violations):
        if not isinstance(violation, dict):
            raise ValueError(f"field_violations[{index}] must be an object")
        required = {"class", "field", "owner_class", "contract_evidence"}
        if set(violation) != required:
            raise ValueError(f"field_violations[{index}] keys differ from required schema")
        if not all(
            str(violation.get(key) or "").strip()
            for key in required - {"owner_class"}
        ):
            raise ValueError(f"field_violations[{index}] has empty required values")
        field = str(violation["field"]).strip()
        owner = str(violation["owner_class"]).strip()
        repair = f"; move it to `{owner}`" if owner else ""
        normalized.append(
            "FIELD_NOT_ACCEPTED_BY_CLASS: "
            f"`{violation['class']}.{field}` is not accepted{repair} "
            f"[contract: {violation['contract_evidence']}]"
        )
    return normalized


def json_object(text: str) -> dict[str, Any]:
    payload = json.loads(strip_code_fences(text))
    if not isinstance(payload, dict):
        raise ValueError("contract critic JSON must be an object")
    return payload


async def run_tbox_contract_critic(
    *,
    model_name: str,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Always run draft + refinement + multiplicity critic. Format exhaustion is fail-open."""
    llm = setup_judge_llm(model_name)
    warnings: list[dict[str, Any]] = []
    audit_prompt = build_tbox_contract_audit_prompt(
        original_prompt=original_prompt,
        source_text=source_text,
        candidate_text=candidate_text,
    )
    draft = response_text(await llm.ainvoke(audit_prompt))
    refinement_prompt = build_tbox_contract_audit_refinement_prompt(
        audit_prompt=audit_prompt,
        draft_checklist=draft,
    )
    feedback: list[str] = []
    audit_error = ""
    for _ in range(CONTRACT_CRITIC_FORMAT_ATTEMPTS):
        result = await llm.ainvoke(
            refinement_prompt
            + (
                "\n\nYour previous verdict was invalid: "
                f"{audit_error}\nReturn only schema-valid JSON."
                if audit_error
                else ""
            )
        )
        try:
            feedback = parse_tbox_contract_audit(response_text(result))
            audit_error = ""
            break
        except ValueError as exc:
            audit_error = str(exc)
    if audit_error:
        warnings.append(
            {
                "kind": "tbox_contract_audit_format_exhausted",
                "message": audit_error,
                "format_attempt_budget": CONTRACT_CRITIC_FORMAT_ATTEMPTS,
            }
        )
        feedback = []
    multiplicity_prompt = build_tbox_contract_multiplicity_audit_prompt(
        audit_prompt=audit_prompt
    )
    multiplicity_error = ""
    for _ in range(CONTRACT_CRITIC_FORMAT_ATTEMPTS):
        result = await llm.ainvoke(
            multiplicity_prompt
            + (
                "\n\nYour previous checklist was invalid: "
                f"{multiplicity_error}\nReturn only schema-valid JSON."
                if multiplicity_error
                else ""
            )
        )
        try:
            extra = parse_tbox_contract_audit(response_text(result))
            feedback = list(dict.fromkeys([*feedback, *extra]))
            multiplicity_error = ""
            break
        except ValueError as exc:
            multiplicity_error = str(exc)
    if multiplicity_error:
        warnings.append(
            {
                "kind": "multiplicity_audit_format_exhausted",
                "message": multiplicity_error,
                "format_attempt_budget": CONTRACT_CRITIC_FORMAT_ATTEMPTS,
            }
        )
    return feedback, warnings
