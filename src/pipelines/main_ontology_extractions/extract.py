"""
Main Ontology Extractions Pipeline Step

This module executes configured main-ontology extraction iterations and optional
enrichment sub-iterations. It produces extraction hints only; KG construction is
handled by later pipeline stages.
"""
import os
import sys
import json
import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, List, Dict, TYPE_CHECKING, Tuple

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.ModelConfig import ModelConfig
from models.LLMCreator import LLMCreator
from src.utils.global_logger import get_logger
from src.agents.scripts_and_prompts_generation.llm_extraction_judge import (
    judge_extraction_semantics,
)
from src.agents.scripts_and_prompts_generation.llm_procedure_inheritance_resolver import (
    BRIEF_BEGIN as PROCEDURE_INHERITANCE_BRIEF_BEGIN,
    render_procedure_inheritance_brief,
    resolve_procedure_inheritance,
)
from src.agents.scripts_and_prompts_generation.llm_global_context_resolver import (
    inject_global_context_brief,
    render_global_context_brief,
    resolve_global_context,
)
from src.utils.extraction_models import get_extraction_model
from src.pipelines.utils.llm_transport_retry import (
    is_llm_transport_error,
    retry_async_on_transport,
)
from src.pipelines.structured_extraction import (
    is_marker_only_optional_output,
    validate_hint_payload,
)
from src.pipelines.utils.top_entity_identity import entity_artifact_name
from src.pipelines.utils.runtime_paths import bounded_sidecar_path as _bounded_sidecar_path
from src.pipelines.utils.ttl_publisher import load_meta_task_config, get_main_ontology_name

if TYPE_CHECKING:
    from models.BaseAgent import BaseAgent

logger = get_logger("pipeline", "main_ontology_extractions")

# Yield-only / single-predicate hints are often one short TTL line (< 50 chars) but still valid.
_MIN_EXTRACTION_CHARS = 20
_CLOSED_LEDGER_RETRY_SPAN_PRESERVATION = (
    "SPAN PRESERVATION: If a failure asks you to split or rewrite one evidence "
    "atom, every other in-scope operation in that atom's verbatim quote must "
    "remain its own evidence atom. A correction that only splits Adds and drops "
    "the rest of the same quote is invalid. Rebuild the complete ledger from the "
    "full target evidence, carrying those remaining quote-local operations "
    "forward."
)


def _get_base_agent():
    """Import BaseAgent lazily so simple-LLM runs do not require agent dependencies."""
    from models.BaseAgent import BaseAgent

    return BaseAgent


def _normalize_llm_content(content_or_message: object) -> str:
    """
    Normalize LangChain message / raw content (``str | list | dict``) to a plain string.

    Avoids ``str([]) -> \"[]\"`` and similar pitfalls when the provider returns block lists.
    """
    from models.BaseAgent import _normalize_ai_message_content

    raw = content_or_message.content if hasattr(content_or_message, "content") else content_or_message
    return _normalize_ai_message_content(raw)


def _merge_mcp_set_extraction_validation(
    extraction_validation: dict | None,
    mcp_set_name: str | None,
) -> dict[str, Any]:
    """Prefer MCP-set owned tool-group policy over iteration-copied leftovers."""
    merged = dict(extraction_validation or {})
    if not mcp_set_name:
        return merged
    from models.MCPConfig import load_mcp_set_extraction_validation

    policy = load_mcp_set_extraction_validation(mcp_set_name)
    groups = (policy or {}).get("required_executed_tool_groups")
    if groups:
        merged["required_executed_tool_groups"] = list(groups)
    return merged


def _required_executed_tool_groups(
    extraction_validation: dict | None,
) -> list[dict[str, Any]]:
    """Return configured required MCP tool groups from extraction_validation."""
    groups = (extraction_validation or {}).get("required_executed_tool_groups") or []
    return [group for group in groups if isinstance(group, dict)]


def _format_required_tool_contract_block(
    groups: list[dict[str, Any]],
) -> str:
    """Build a mechanical prompt appendix that requires configured MCP tool activity."""
    if not groups:
        return ""
    lines = [
        "## Required MCP Tool Contract",
        (
            "Before returning the final extraction output for this attempt, you MUST "
            "execute the following MCP tool activity with the tools available in this run."
        ),
        (
            "Do not return final extraction output until every required group below has been "
            "satisfied for every applicable entity occurrence by at least one completed tool "
            "call in this attempt whose arguments identify that entity. A call for one entity "
            "does not satisfy the requirement for another entity unless the tool explicitly "
            "accepts a batch and returns separately attributable results for every entity. "
            "A completed call that returns no match, ok=false, or empty content still "
            "satisfies the call requirement; treat that lookup as unresolved."
        ),
        "",
        "Required tool groups:",
    ]
    for group in groups:
        name = str(group.get("name") or "required_mcp_lookup").strip() or "required_mcp_lookup"
        candidates = [
            str(item).strip()
            for item in (group.get("any_of") or [])
            if str(item).strip()
        ]
        if not candidates:
            continue
        lines.append(
            f"- `{name}`: for every applicable entity occurrence, call at least one of "
            f"{candidates} with arguments identifying that entity."
        )
    lines.extend(
        [
            "",
            "After the required tool calls complete, return only the extraction output "
            "required by the prompt above. If a lookup is unresolved, leave those "
            "lookup-only values unset; do not invent lookup values to replace a "
            "missing tool result.",
        ]
    )
    return "\n".join(lines).strip()


def _inject_required_tool_contract(
    prompt: str,
    extraction_validation: dict | None,
    *,
    use_agent: bool,
) -> str:
    """Append the configured required-tool contract when agent/MCP mode is active."""
    if not use_agent:
        return prompt
    block = _format_required_tool_contract_block(
        _required_executed_tool_groups(extraction_validation)
    )
    if not block:
        return prompt
    marker = "## Required MCP Tool Contract"
    if marker in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{block}\n"


def _should_fail_open_required_tool_gate(
    *,
    attempt: int,
    max_retries: int,
    content: str,
) -> bool:
    """Keep the last non-empty draft after the required-tool budget is gone."""
    return attempt >= max(1, int(max_retries)) - 1 and bool(str(content or "").strip())


def _format_required_tool_feedback(
    tool_activity_errors: list[str],
    *,
    groups: list[dict[str, Any]],
    executed_tool_names: list[str] | None = None,
) -> str:
    """Build retry feedback that forces another tool-using attempt instead of JSON-only repair."""
    executed = [
        str(name).strip()
        for name in (executed_tool_names or [])
        if str(name).strip()
    ]
    lines = [
        "REQUIRED MCP TOOL ACTIVITY FEEDBACK:",
        "The previous attempt returned extraction output without satisfying the required "
        "MCP tool contract. This is a recoverable tool-activity miss, not a final failure yet.",
    ]
    for error in tool_activity_errors:
        lines.append(f"- {error}")
    if groups:
        lines.append("Required groups for the next attempt:")
        for group in groups:
            name = str(group.get("name") or "required_mcp_lookup").strip()
            candidates = [
                str(item).strip()
                for item in (group.get("any_of") or [])
                if str(item).strip()
            ]
            if candidates:
                lines.append(
                    f"- `{name}`: for every applicable entity occurrence, call at least one "
                    f"of {candidates} with arguments identifying that entity."
                )
    lines.extend(
        [
            f"Tools executed in the previous attempt: {executed or []}",
            "ACTION FOR THIS RETRY:",
            "1. For every applicable entity occurrence not yet covered, call at least one "
            "required tool from each missing group with arguments identifying that entity.",
            "2. Only after those tool calls have executed, return the extraction output.",
            "3. Do not answer with JSON-only output before the required tool activity occurs.",
        ]
    )
    return "\n".join(lines)

def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.
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


def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    return entity_artifact_name(label)


def resolve_file_path(path_template: str, doi_hash: str, entity_safe: str, data_dir: str = "data") -> str:
    """
    Resolve a file path template with placeholders.
    
    Args:
        path_template: Template with {entity_safe} placeholder
        doi_hash: DOI hash
        entity_safe: Safe entity name
        data_dir: Data directory root
        
    Returns:
        Resolved absolute file path
    """
    # Replace placeholder
    resolved = path_template.replace("{entity_safe}", entity_safe)
    # Build full path
    return os.path.join(data_dir, doi_hash, resolved)


def _write_text_with_parent(path: str, content: str) -> None:
    """Write text while tolerating concurrent runtime-directory cleanup."""
    parent = os.path.dirname(path)
    for attempt in range(2):
        os.makedirs(parent, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            return
        except FileNotFoundError:
            if attempt:
                raise


def _strip_code_fences_block(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _parse_structured_hint_payload(text: str):
    cleaned = _strip_code_fences_block(text)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    try:
        import yaml  # type: ignore

        return yaml.safe_load(cleaned)
    except Exception:
        return None


def _build_tbox_contract_audit_prompt(
    *,
    original_prompt: str,
    source_text: str,
    candidate_text: str,
) -> str:
    """Build a domain-independent LLM audit over source, hint, and T-Box contract."""
    focused_contract = _extract_tbox_audit_contract(original_prompt)
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


def _extract_tbox_audit_contract(prompt_text: str) -> str:
    """Select only identity, ownership, and materialization contract lines."""
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


def _build_tbox_contract_audit_refinement_prompt(
    *,
    audit_prompt: str,
    draft_checklist: str,
) -> str:
    """Build a second-pass completeness review without domain-specific rules."""
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


def _build_tbox_contract_multiplicity_audit_prompt(*, audit_prompt: str) -> str:
    """Build an isolated LLM pass for repeated-quantity lexical expressions."""
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


def _parse_tbox_contract_audit(
    text: str,
    *,
    source_text: str | None = None,
    contract_text: str | None = None,
    candidate_text: str | None = None,
) -> tuple[bool, list[str]]:
    """Validate and normalize the critic's compact JSON verdict."""
    try:
        payload = json.loads(_strip_code_fences_block(text))
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
    # Content correctness belongs to the LLM critic. These inputs remain in the
    # signature for compatibility and for prompt construction at the call site.
    del source_text, contract_text, candidate_text
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
        required = {
            "class",
            "field",
            "owner_class",
            "contract_evidence",
        }
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
    return not normalized, normalized


def _extract_stage_owned_classes(contract_text: str) -> set[str]:
    match = re.search(
        r"^- Stage-owned classes:\s*(.+)$",
        contract_text or "",
        flags=re.MULTILINE,
    )
    if not match:
        return set()
    return {
        value.strip()
        for value in match.group(1).split(",")
        if value.strip() and value.strip().casefold() != "none"
    }


def _extract_materializable_field_contracts(
    contract_text: str,
) -> dict[str, set[str]]:
    contracts: dict[str, set[str]] = {}
    patterns = (
        re.compile(
            r"^- `(?P<class>[A-Za-z0-9_]+)` -> `[^`]+` accepts fields: "
            r"(?P<fields>.+)$",
            flags=re.MULTILINE,
        ),
        re.compile(
            r"^- Entity class `(?P<class>[A-Za-z0-9_]+)` -> "
            r"`datatype_properties` accepts: (?P<fields>.+)$",
            flags=re.MULTILINE,
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(contract_text or ""):
            contracts[match.group("class")] = set(
                re.findall(r"`([A-Za-z0-9_]+)`", match.group("fields"))
            )
    return contracts


def _semantic_audit_sidecar_path(hints_file: str) -> str:
    candidate = Path(f"{hints_file}.semantic_audit.json")
    if len(str(candidate.absolute())) < 240:
        return str(candidate)
    digest = hashlib.sha256(
        str(Path(hints_file).name).encode("utf-8")
    ).hexdigest()[:16]
    iteration_match = re.match(r"(iter\d+)", Path(hints_file).name)
    iteration = iteration_match.group(1) if iteration_match else "hints"
    return str(candidate.parent / f"{iteration}_semantic_audit--{digest}.json")


def _infer_list_merge_key(
    base: list,
    update: list,
    preferred_keys: list[str] | None = None,
) -> str | None:
    """Select a configured identity key with stable overlapping scalar values."""
    base_dicts = [item for item in base if isinstance(item, dict)]
    update_dicts = [item for item in update if isinstance(item, dict)]
    if not base_dicts or not update_dicts or not preferred_keys:
        return None

    candidate_keys = set.intersection(
        *(set(item) for item in [*base_dicts, *update_dicts])
    )
    ordered_candidates = [
        key for key in preferred_keys if key in candidate_keys
    ]
    for key in ordered_candidates:
        base_values = [item.get(key) for item in base_dicts]
        update_values = [item.get(key) for item in update_dicts]
        if any(
            isinstance(value, (dict, list)) or value in (None, "")
            for value in [*base_values, *update_values]
        ):
            continue
        if len(set(base_values)) != len(base_values):
            continue
        if len(set(update_values)) != len(update_values):
            continue
        overlap = len(set(base_values) & set(update_values))
        if overlap:
            return str(key)
    return None


def _merge_structured_hint_payloads(
    base,
    update,
    *,
    identity_keys: list[str] | None = None,
):
    if isinstance(base, dict) and isinstance(update, dict):
        merged = dict(base)
        for key, value in update.items():
            if key in merged:
                merged[key] = _merge_structured_hint_payloads(
                    merged[key],
                    value,
                    identity_keys=identity_keys,
                )
            else:
                merged[key] = value
        return merged
    if isinstance(base, list) and isinstance(update, list):
        merged = list(base)
        identity_key = _infer_list_merge_key(
            base,
            update,
            preferred_keys=identity_keys,
        )
        if identity_key is None:
            return [*merged, *update]
        index = {
            item.get(identity_key): pos
            for pos, item in enumerate(merged)
            if isinstance(item, dict) and item.get(identity_key) not in (None, "")
        }
        for item in update:
            merge_value = (
                item.get(identity_key) if isinstance(item, dict) else None
            )
            if merge_value not in (None, "") and merge_value in index:
                merged[index[merge_value]] = _merge_structured_hint_payloads(
                    merged[index[merge_value]],
                    item,
                    identity_keys=identity_keys,
                )
            else:
                merged.append(item)
        return merged
    return update


def _merge_structured_hint_text(
    base_text: str,
    update_text: str,
    *,
    identity_keys: list[str] | None = None,
) -> str | None:
    base_payload = _parse_structured_hint_payload(base_text)
    update_payload = _parse_structured_hint_payload(update_text)
    if not isinstance(base_payload, dict) or not isinstance(update_payload, dict):
        return None
    merged_payload = _merge_structured_hint_payloads(
        base_payload,
        update_payload,
        identity_keys=identity_keys,
    )
    relations = merged_payload.get("relations")
    if isinstance(relations, list):
        deduplicated = []
        seen: set[str] = set()
        for relation in relations:
            marker = json.dumps(
                relation,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
            if marker in seen:
                continue
            seen.add(marker)
            deduplicated.append(relation)
        merged_payload["relations"] = deduplicated
    return json.dumps(merged_payload, ensure_ascii=False, indent=2)


def _kg_revision_relation_errors(
    candidate_text: str,
    revision_feedback: str,
) -> list[str]:
    """Reject a correction payload that retains an exactly reported bad relation."""
    try:
        candidate = json.loads(candidate_text)
        feedback = json.loads(revision_feedback)
    except (TypeError, json.JSONDecodeError):
        return []
    relations = candidate.get("relations") if isinstance(candidate, dict) else None
    violations = feedback.get("violations") if isinstance(feedback, dict) else None
    if not isinstance(relations, list) or not isinstance(violations, list):
        return []
    emitted = {
        (
            str(item.get("subject_ref") or ""),
            str(item.get("property") or ""),
            str(item.get("object_ref") or ""),
        )
        for item in relations
        if isinstance(item, dict)
    }
    errors: list[str] = []
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        triple = (
            str(violation.get("subject_ref") or ""),
            str(violation.get("property") or ""),
            str(violation.get("object_ref") or ""),
        )
        if all(triple) and triple in emitted:
            errors.append(
                "Correction retained reported invalid relation "
                f"{triple[0]} -{triple[1]}-> {triple[2]}"
            )
    return errors


def _looks_like_patch_enrichment_output(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    return (
        "patch_triples" in low
        or "kg_patch_triples" in low
        or "done_marker=true" in low
        or "done_marker: true" in low
    )


def _iter_base_hint_snapshot_path(base_hint_file: str, *, enriches: int | str, entity_safe: str) -> str:
    return os.path.join(
        os.path.dirname(base_hint_file),
        f"iter{enriches}_base_hints_{entity_safe}.txt",
    )


def _sub_iteration_patch_output_path(base_hint_file: str, *, enriches: int | str, sub_iter_num: int | str, entity_safe: str) -> str:
    return os.path.join(
        os.path.dirname(base_hint_file),
        f"iter{enriches}_{sub_iter_num}_patch_{entity_safe}.txt",
    )


def _write_declared_sub_iteration_file(
    *,
    sub_outputs: dict[str, Any],
    merged_hint_text: str,
    doi_hash: str,
    entity_safe: str,
    data_dir: str,
) -> str | None:
    """Persist the configured full enrichment artifact, when one is declared."""
    file_template = str(sub_outputs.get("file_path") or "").strip()
    if not file_template:
        return None
    output_path = resolve_file_path(
        file_template,
        doi_hash,
        entity_safe,
        data_dir,
    )
    _write_text_with_parent(output_path, merged_hint_text)
    return output_path


def _load_accumulated_prior_hints(
    *,
    iterations: list[dict[str, Any]],
    current_iteration: int,
    doi_hash: str,
    entity_safe: str,
    data_dir: str,
) -> tuple[str, list[str]]:
    """Load earlier per-entity hint artifacts as a read-only identity registry."""
    entries: list[dict[str, Any]] = []
    paths: list[str] = []
    for iteration in iterations:
        try:
            iteration_number = int(iteration.get("iteration_number"))
        except (TypeError, ValueError):
            continue
        if iteration_number >= current_iteration:
            continue
        outputs = iteration.get("outputs") or {}
        hint_template = outputs.get(
            "hints_file",
            f"mcp_run/iter{iteration_number}_hints_{{entity_safe}}.txt",
        )
        hint_path = resolve_file_path(
            str(hint_template),
            doi_hash,
            entity_safe,
            data_dir,
        )
        try:
            content = Path(hint_path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        if not content:
            continue
        hint_representation = str(
            iteration.get("hint_representation") or "ref-entity-relations.v1"
        )
        if hint_representation == "semantic-text.v1":
            if not content.lstrip().startswith("SEMANTIC_HINTS_V1"):
                raise ValueError(
                    f"Prior iteration {iteration_number} semantic hints are missing "
                    f"SEMANTIC_HINTS_V1: {hint_path}"
                )
            payload: Any = {
                "hint_representation": "semantic-text.v1",
                "semantic_ledger": content,
            }
        else:
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Prior iteration {iteration_number} hints are not valid JSON: "
                    f"{hint_path}: {exc}"
                ) from exc
        paths.append(hint_path)
        entries.append(
            {
                "iteration_number": iteration_number,
                "payload": payload,
            }
        )
    if not entries:
        return "", paths
    return json.dumps(
        {
            "schema_version": "accumulated-prior-hints.v1",
            "iterations": entries,
        },
        ensure_ascii=False,
        indent=2,
    ), paths


def load_iterations_config(ontology_name: str) -> dict:
    """Load the iterations configuration for the ontology."""
    config_path = get_iterations_config_path(ontology_name)
    
    if not os.path.exists(config_path):
        logger.error(f"Iterations config not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load iterations config: {e}")
        return {}


def get_iterations_config_path(ontology_name: str) -> str:
    """Resolve the iterations.json path for an ontology."""
    return resolve_generated_file(
        f"ai_generated_contents/iterations/{ontology_name}/iterations.json"
    )


def load_top_entities(doi_hash: str, data_dir: str = "data") -> List[Dict]:
    """Load the top entities JSON from iteration 1."""
    json_path = os.path.join(data_dir, doi_hash, "mcp_run", "iter1_top_entities.json")
    
    if not os.path.exists(json_path):
        logger.error(f"Top entities JSON not found: {json_path}")
        return []
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load top entities: {e}")
        return []


def _artifact_is_current(path: str, dependency_paths: List[str] | None = None) -> bool:
    """Return True if artifact exists, is non-empty, and is not older than dependencies."""
    if not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) <= 0:
            return False
        artifact_mtime = os.path.getmtime(path)
    except Exception:
        return False

    dep_mtimes: list[float] = []
    for dep in dependency_paths or []:
        if not dep:
            continue
        dep_resolved = dep if os.path.exists(dep) else resolve_generated_file(dep)
        if os.path.exists(dep_resolved):
            try:
                dep_mtimes.append(os.path.getmtime(dep_resolved))
            except Exception:
                pass
    if dep_mtimes and artifact_mtime < max(dep_mtimes):
        return False
    return True


def _expected_hint_files_exist(
    doi_hash: str,
    iterations: List[Dict],
    top_entities: List[Dict],
    data_dir: str = "data",
    iterations_config_path: str | None = None,
) -> bool:
    """
    Return True only if every per-entity iteration that should emit hints has
    produced a non-empty hints file for every top entity.
    """
    for iteration in iterations:
        if not isinstance(iteration, dict):
            continue
        if not iteration.get("per_entity", False):
            continue
        iter_num = iteration.get("iteration_number")
        outputs = iteration.get("outputs", {}) or {}
        hint_file_template = outputs.get("hints_file", f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt")
        for entity in top_entities:
            entity_label = entity.get("label", "")
            safe = _safe_name(entity_label)
            hint_file = resolve_file_path(hint_file_template, doi_hash, safe, data_dir)
            freshness_deps = [iterations_config_path or ""]
            freshness_deps.extend(
                _prompt_contract_dependency_paths(iteration.get("extraction_prompt", ""))
            )
            if iteration.get("has_pre_extraction"):
                freshness_deps.extend(
                    _prompt_contract_dependency_paths(
                        iteration.get("pre_extraction_prompt", "")
                    )
                )
            if not _artifact_is_current(hint_file, freshness_deps):
                return False
    return True


def _prompt_contract_dependency_paths(prompt_path: str) -> list[str]:
    """Return a prompt path plus its sibling deterministic contract component."""
    if not prompt_path:
        return []
    resolved = prompt_path if os.path.exists(prompt_path) else resolve_generated_file(prompt_path)
    path = Path(resolved)
    return [prompt_path, str(path.with_name(f"{path.stem}.materializable.inc"))]


def load_prompt(prompt_path: str) -> str:
    """Load a generated prompt plus its deterministic contract component."""
    prompt_path = resolve_generated_file(prompt_path)
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt file not found: {prompt_path}")
        return ""
    
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            prompt = f.read()
        path = Path(prompt_path)
        contract_path = path.with_name(f"{path.stem}.materializable.inc")
        if contract_path.is_file():
            component = contract_path.read_text(encoding="utf-8").strip()
            already_spliced = (
                "----- DETERMINISTIC T-BOX CONTRACT (mechanically spliced; do not edit) -----"
                in prompt
                or (component and component in prompt)
            )
            if not already_spliced:
                prompt = f"{prompt.rstrip()}\n\n{component}\n"
        return prompt
    except Exception as e:
        logger.error(f"Failed to load prompt: {e}")
        return ""


def load_paper_content_with_sources(doi_hash: str, data_dir: str = "data") -> Tuple[str, List[str]]:
    """Load the best-available paper content and supplemental context.

    Priority order:
      1. {hash}_vision.md  — vision LLM transcription (highest fidelity for medical PDFs)
      2. {hash}_stitched.md — section-filtered / stitched content
      3. {hash}_text.md    — plain text extraction
      4. {hash}.md         — raw combined output

    If supporting-information markdown exists, append it after the selected main
    paper text because extraction evidence may be split across source documents.
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    vision_md = os.path.join(doi_dir, f"{doi_hash}_vision.md")
    stitched = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    text_md = os.path.join(doi_dir, f"{doi_hash}_text.md")
    raw_md = os.path.join(doi_dir, f"{doi_hash}.md")

    main_text = ""
    source_paths: List[str] = []
    for p in (vision_md, stitched, text_md, raw_md):
        if not os.path.exists(p):
            continue
        try:
            txt = Path(p).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {p}: {e}")
            continue
        if txt and txt.strip():
            main_text = txt
            source_paths.append(p)
            break

    if main_text:
        parts = [main_text]
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
                si_txt = Path(si_path).read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read {si_path}: {e}")
                continue
            if si_txt and si_txt.strip():
                parts.append(f"\n\n# Supporting Information: {si_name}\n\n{si_txt}")
                source_paths.append(si_path)
        return "".join(parts), source_paths

    logger.error(
        f"No usable paper content found for {doi_hash}. Tried stitched/text/raw markdown."
    )
    return "", []


def load_paper_content(doi_hash: str, data_dir: str = "data") -> str:
    """Load paper content for callers that do not need source dependency paths."""
    content, _ = load_paper_content_with_sources(doi_hash, data_dir)
    return content


def bind_runtime_context(
    prompt_template: str,
    *,
    doi_hash: str = "",
    entity_label: str,
    entity_uri: str,
    source_text: str,
    iteration_input: str = "",
    accumulated_hints: str = "",
    identity_dossier: dict | None = None,
) -> str:
    """Bind the complete pipeline-owned extraction runtime envelope."""
    declared_doi = "{doi}" in prompt_template or "{hash}" in prompt_template
    declared_label = "{entity_label}" in prompt_template
    declared_uri = "{entity_uri}" in prompt_template
    declared_dossier = "{entity_identity_dossier}" in prompt_template
    dossier_text = json.dumps(
        identity_dossier or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    prompt = prompt_template.replace("{doi}", doi_hash).replace("{hash}", doi_hash)
    prompt = prompt.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    prompt = prompt.replace("{entity_identity_dossier}", dossier_text)
    declared_source = "{paper_content}" in prompt or "{context}" in prompt
    prompt = prompt.replace("{paper_content}", source_text)
    prompt = prompt.replace("{context}", source_text)
    declared_iteration_input = "{iteration_input}" in prompt
    prompt = prompt.replace("{iteration_input}", iteration_input)
    declared_accumulated_hints = "{accumulated_hints}" in prompt
    prompt = prompt.replace("{accumulated_hints}", accumulated_hints)

    additions: list[str] = []
    missing_identity: list[str] = []
    if doi_hash and not declared_doi:
        missing_identity.append(f"Document DOI/hash: {doi_hash}")
    if not declared_label:
        missing_identity.append(f"Current entity label: {entity_label}")
    if not declared_uri:
        missing_identity.append(f"Current entity exact URI: {entity_uri}")
    if missing_identity:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: BEGIN ----",
                *missing_identity,
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: END ----",
            ]
        )
    if identity_dossier and not declared_dossier:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY IDENTITY DOSSIER: BEGIN ----",
                "This dossier is the authoritative identity scope for the current entity.",
                "Use only its explicit fields and facts; do not infer missing identity facts.",
                "Do not substitute, merge, or redirect the current entity to another top-entity scope.",
                dossier_text,
                "---- PIPELINE-INJECTED ENTITY IDENTITY DOSSIER: END ----",
            ]
        )
    if not declared_source:
        additions.extend(
            [
                "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----",
                source_text,
                "---- PIPELINE-INJECTED SOURCE TEXT: END ----",
            ]
        )
    if iteration_input and not declared_iteration_input:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ITERATION INPUT: BEGIN ----",
                iteration_input,
                "---- PIPELINE-INJECTED ITERATION INPUT: END ----",
            ]
        )
    if accumulated_hints and not declared_accumulated_hints:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ACCUMULATED PRIOR HINTS: BEGIN ----",
                "The following prior hints are a read-only semantic identity registry.",
                "Do not re-emit an entity or top-entity link already represented here.",
                "When the current iteration must reference an existing entity, reuse its exact class and label.",
                "Emit only new stage-owned entities, new stage-owned links, or source-supported fields that the prior hints do not already contain.",
                "The current target entity may be repeated only as a minimal shell needed to attach new stage-owned links.",
                accumulated_hints,
                "---- PIPELINE-INJECTED ACCUMULATED PRIOR HINTS: END ----",
            ]
        )
    return prompt.rstrip() + ("\n\n" + "\n".join(additions) + "\n" if additions else "")


def _inject_procedure_inheritance_brief(prompt: str, brief: str) -> str:
    """Append one runtime-only inheritance brief without changing generated prompts."""
    normalized = str(brief or "").strip()
    if not normalized or PROCEDURE_INHERITANCE_BRIEF_BEGIN in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + normalized + "\n"


def _append_closed_ledger_output_boundary(prompt: str, brief: str) -> str:
    """Restate the closed-ledger transport contract after all runtime evidence."""
    inheritance_requirement = (
        "Respect same-as / following / analogously inheritance in the injected "
        "context. Cover inherited operations the target did not explicitly change. "
        "The effective_workflow is a starting map, not a license to drop unmentioned "
        "inherited operations."
        if str(brief or "").strip()
        else "Derive complete operation coverage from the target source and active T-Box."
    )
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED CLOSED-LEDGER OUTPUT BOUNDARY: BEGIN ----\n"
        + inheritance_requirement
        + "\nReturn exactly one JSON object and no prose or Markdown fence. It must contain "
        "a `scope_resolution` object with `completion_attestation`, and an `evidence` array. "
        "The completion_attestation must set target_located, all_references_resolved, "
        "all_modifications_applied, and effective_workflow_complete to true only after the "
        "complete ledger has been built. Each evidence row must use sequential E### "
        "evidence_id and one-based source_order and must contain a non-empty verbatim_quote, "
        "a non-empty candidate_types array, and a candidate_properties object. Preserve "
        "identity, amount, role, and operation-local qualifiers on the same semantic "
        "occurrence. Do not include hasOrder in candidate_properties.\n"
        "---- PIPELINE-INJECTED CLOSED-LEDGER OUTPUT BOUNDARY: END ----\n"
    )


def _append_ref_entity_output_boundary(prompt: str) -> str:
    """Place the strict output instruction after all injected runtime evidence."""
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED FINAL OUTPUT BOUNDARY: BEGIN ----\n"
        "The prior-hints and identity-dossier blocks above are read-only inputs. "
        "Do not quote, copy, wrap, or re-emit those blocks.\n"
        "Return only one JSON object for the current iteration delta. It must have "
        "exactly two top-level arrays named `entities` and `relations` and must contain "
        "no heading, commentary, Markdown fence, or second JSON document.\n"
        "---- PIPELINE-INJECTED FINAL OUTPUT BOUNDARY: END ----\n"
    )


def _ledger_target_evidence_passages(ledger_text: str) -> list[str]:
    """Read the complete target passage already stored on a closed ledger."""
    try:
        payload = json.loads(_strip_code_fences_block(ledger_text))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    scope = payload.get("scope_resolution")
    raw = scope.get("target_evidence") if isinstance(scope, dict) else None
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _append_complete_target_passage(prompt: str, ledger_text: str) -> str:
    """Hand the whole target passage to the next step so later sentences are not dropped."""
    passages = _ledger_target_evidence_passages(ledger_text)
    if not passages:
        return prompt
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED COMPLETE TARGET PASSAGE: BEGIN ----\n"
        "This is the complete producing workflow. The first span that names the "
        "target is an identity anchor, not a start bound. Earlier same-source "
        "operations consumed by later sentences count, and later sentences count. "
        "Cover every in-scope operation. Do not start at the first identifying "
        "mention and do not stop after the first sentence.\n\n"
        + "\n\n".join(passages)
        + "\n---- PIPELINE-INJECTED COMPLETE TARGET PASSAGE: END ----\n"
    )


def _inheritance_context_passages(brief: str) -> list[str]:
    """Collect the inherited source quotes already stored on the runtime brief."""
    if not str(brief or "").strip():
        return []
    try:
        payload = _inheritance_brief_payload(brief)
    except Exception:
        return []
    passages: list[str] = []
    seen: set[str] = set()

    def _add(quote: str) -> None:
        text = str(quote or "").strip()
        if text and text not in seen:
            seen.add(text)
            passages.append(text)

    for dependency in payload.get("dependencies") or []:
        if isinstance(dependency, dict):
            _add(dependency.get("source_evidence"))
    for workflow in payload.get("base_workflows") or []:
        atoms = workflow.get("atoms") if isinstance(workflow, dict) else None
        if not isinstance(atoms, list):
            continue
        for atom in atoms:
            if isinstance(atom, dict):
                _add(atom.get("source_evidence"))
    return passages


def _append_complete_inheritance_context(prompt: str, brief: str) -> str:
    """Hand the full inherited source context to the next step."""
    if not str(brief or "").strip():
        return prompt
    passages = _inheritance_context_passages(brief)
    block = (
        "Respect same-as / following / analogously inheritance. The inherited "
        "context is complete. Apply only the target's explicit changes; do not "
        "drop inherited operations just because the target restatement does not "
        "repeat them."
    )
    if passages:
        block = block + "\n\n" + "\n\n".join(passages)
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED COMPLETE INHERITANCE CONTEXT: BEGIN ----\n"
        + block
        + "\n---- PIPELINE-INJECTED COMPLETE INHERITANCE CONTEXT: END ----\n"
    )


def _append_semantic_hint_output_boundary(prompt: str) -> str:
    """Require a format-light semantic ledger rather than graph-shaped JSON."""
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED SEMANTIC OUTPUT BOUNDARY: BEGIN ----\n"
        "Return a concise natural-language semantic ledger headed exactly "
        "`SEMANTIC_HINTS_V1`. Do not output JSON, RDF, entity refs, IRIs, or tool calls. "
        "Describe every source-supported, iteration-owned occurrence and relation in source "
        "order, preserving exact lexical values and every occurrence boundary required by the "
        "active T-Box comments and integrity annotations already supplied in the prompt. "
        "Do not invent missing values, domain rules, or placeholder entities. "
        "A tool result that reports no match, ok=false, matched=false, or empty content "
        "is unresolved. Copy lookup values only when the tool actually returned them; "
        "do not invent lookup values to fill a miss.\n"
        "---- PIPELINE-INJECTED SEMANTIC OUTPUT BOUNDARY: END ----\n"
    )


def _prune_untyped_closed_ledger_evidence(candidate_text: str) -> str:
    """Drop context-only atoms that cannot satisfy the typed evidence contract."""
    try:
        payload = json.loads(_strip_code_fences_block(candidate_text))
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
        payload = json.loads(_strip_code_fences_block(candidate_text))
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
        audit = json.loads(_strip_code_fences_block(audit_text))
        candidate = json.loads(_strip_code_fences_block(candidate_text))
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
        judgement = json.loads(_strip_code_fences_block(judgement_text))
        candidate = json.loads(_strip_code_fences_block(candidate_text))
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
    candidate = json.loads(_strip_code_fences_block(candidate_text))
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
            raw = _normalize_llm_content(result)
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
                trace_path = _bounded_sidecar_path(
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
        candidate = json.loads(_strip_code_fences_block(candidate_text))
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
        payload = json.loads(_strip_code_fences_block(source_text))
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
        payload = json.loads(_strip_code_fences_block(vote_text))
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
                    _normalize_llm_content(result),
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
        audit = json.loads(_strip_code_fences_block(audit_text))
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
                    _normalize_llm_content(result),
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
        audit_text = _normalize_llm_content(result)
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
            audit_text = _normalize_llm_content(result)
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


async def run_pre_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    paper_content: str,
    prompt_template: str,
    model_key: str,
    iter_num: int,
    data_dir: str = "data",
    freshness_paths: List[str] | None = None,
    identity_dossier: dict | None = None,
    accumulated_hints: str = "",
    pre_extraction_validation: dict | None = None,
    max_retries: int = 5,
    procedure_inheritance_brief: str = "",
) -> str:
    """
    Run pre-extraction for an entity (e.g., iteration 3 pre-extraction).
    
    Returns:
        Extracted text content
    """
    safe = _safe_name(entity_label)
    output_dir = os.path.join(data_dir, doi_hash, "pre_extraction")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"entity_text_{safe}.txt")
    
    # Check if already exists
    if _artifact_is_current(output_path, freshness_paths):
        logger.info(f"    ⏭️  Pre-extraction already exists for '{entity_label}'")
        with open(output_path, 'r', encoding='utf-8') as f:
            return f.read()
    if os.path.exists(output_path):
        logger.info(f"    🔁 Pre-extraction is stale for '{entity_label}', regenerating")
    
    logger.info(f"    🔍 Running pre-extraction for '{entity_label}'...")
    
    prompt = bind_runtime_context(
        prompt_template,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        source_text=paper_content,
        accumulated_hints=accumulated_hints,
        identity_dossier=identity_dossier,
    )
    prompt = _inject_procedure_inheritance_brief(
        prompt,
        procedure_inheritance_brief,
    )
    prompt = _append_closed_ledger_output_boundary(
        prompt,
        procedure_inheritance_brief,
    )
    prompt = _append_complete_inheritance_context(
        prompt,
        procedure_inheritance_brief,
    )
    
    # Save full prompt for debugging in organized subfolder
    prompts_dir = os.path.join(data_dir, doi_hash, "prompts", f"iter{iter_num}_pre_extraction")
    os.makedirs(prompts_dir, exist_ok=True)
    prompt_file = os.path.join(prompts_dir, f"{safe}.md")
    try:
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration {iter_num} Pre-Extraction Prompt\n\n")
            f.write(f"**Entity**: {entity_label}\n\n")
            f.write(f"**Entity URI**: {entity_uri}\n\n")
            f.write(f"**Model**: {get_extraction_model(model_key)}\n\n")
            f.write("---\n\n")
            f.write(prompt)
        logger.info(f"    💾 Saved pre-extraction prompt to: {prompt_file}")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to save pre-extraction prompt: {e}")
    
    # Get model
    model_name = get_extraction_model(model_key)
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    
    validation_cfg = pre_extraction_validation or {}
    closed_ledger_cfg = validation_cfg.get("closed_ledger") or {}
    closed_ledger_enabled = bool(
        closed_ledger_cfg.get(
            "enabled", "closed ledger" in prompt_template.casefold()
        )
    )
    closed_ledger_vote_count = max(
        1,
        int(
            closed_ledger_cfg.get(
                "semantic_audit_votes", 3
            )
        ),
    )
    closed_ledger_format_retries = max(
        1, int(closed_ledger_cfg.get("audit_format_retries", 3))
    )
    closed_ledger_fail_open_on_format_error = bool(
        closed_ledger_cfg.get("fail_open_on_audit_format_error", True)
    )
    closed_ledger_nonblocking_after_exhaustion = bool(
        closed_ledger_cfg.get("nonblocking_after_semantic_exhaustion", True)
    )
    closed_ledger_format_model = str(
        closed_ledger_cfg.get("audit_format_model") or model_name
    ).strip()
    audit_llm = None
    format_llm = None
    responses_dir = os.path.join(
        data_dir, doi_hash, "responses", f"iter{iter_num}_pre_extraction"
    )
    validation_feedback_history: list[str] = []
    previous_feedback_fingerprint: frozenset[str] | None = None

    # Extract with bounded retries and closed-ledger repair feedback.
    max_retries = max(1, int(max_retries))
    last_draft = ""
    for attempt in range(max_retries):
        nonblocking_audit_warning: dict[str, Any] | None = None
        try:
            logger.info(f"    🔍 Running pre-extraction (attempt {attempt + 1}/{max_retries})")
            effective_prompt = prompt
            if validation_feedback_history:
                effective_prompt += (
                    "\n\nCLOSED-LEDGER VALIDATION FEEDBACK FROM ALL PREVIOUS ATTEMPTS "
                    "(every item remains mandatory):\n"
                    + _format_closed_ledger_feedback_history(
                        validation_feedback_history
                    )
                    + "\nRebuild and return the complete corrected ledger JSON only. "
                    "Preserve every earlier correction while fixing the latest failure. "
                    f"{_CLOSED_LEDGER_RETRY_SPAN_PRESERVATION} "
                    "Do not merely explain the corrections.\n"
                )
                effective_prompt = _append_complete_target_passage(
                    effective_prompt, last_draft
                )
            result = await retry_async_on_transport(
                lambda: llm.ainvoke(effective_prompt),
                logger=logger,
                what=f"pre-extraction llm '{entity_label}'",
            )
            content = _normalize_llm_content(result)
            last_draft = content
            
            # CRITICAL VALIDATION: Check if content is meaningful
            if not content or not content.strip():
                raise ValueError(f"LLM returned empty content for pre-extraction of '{entity_label}'")
            
            if len(content.strip()) < _MIN_EXTRACTION_CHARS:
                logger.error(
                    "    Pre-extraction too short: type=%s len(raw)=%s repr=%s",
                    type(content).__name__,
                    len(content) if content is not None else None,
                    repr(content)[:500],
                )
                raise ValueError(
                    f"LLM returned suspiciously short content ({len(content)} chars) for pre-extraction of '{entity_label}'"
                )

            if closed_ledger_enabled:
                content = _prune_untyped_closed_ledger_evidence(content)
                shape_errors = _validate_closed_ledger_shape(content, paper_content)
                if shape_errors:
                    raise ValueError(
                        "Closed-ledger structural validation failed:\n- "
                        + "\n- ".join(shape_errors[:20])
                    )
                if audit_llm is None:
                    audit_llm = LLMCreator(
                        model=model_name,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                    ).setup_llm()
                if format_llm is None:
                    format_llm = LLMCreator(
                        model=closed_ledger_format_model,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                    ).setup_llm()
                audit_feedback: list[str] = []
                try:
                    audit_feedback = await _run_closed_ledger_audit_panel(
                        audit_llm=audit_llm,
                        format_llm=format_llm,
                        original_prompt=prompt,
                        source_text=paper_content,
                        candidate_text=content,
                        prior_feedback=validation_feedback_history,
                        vote_count=closed_ledger_vote_count,
                        format_retries=closed_ledger_format_retries,
                        inheritance_brief=procedure_inheritance_brief,
                        format_trace_dir=responses_dir,
                        format_trace_stem=f"{safe}.candidate_{attempt + 1}",
                    )
                except ValueError as audit_format_error:
                    if not closed_ledger_fail_open_on_format_error:
                        raise
                    nonblocking_audit_warning = {
                        "kind": "non_type_audit_format_exhausted",
                        "message": str(audit_format_error),
                        "candidate_attempt": attempt + 1,
                        "format_retries": closed_ledger_format_retries,
                    }
                    logger.warning(
                        "    ⚠️  Closed-ledger audit formatting remained invalid after "
                        "%s dedicated format attempts; preserving the structurally valid "
                        "candidate and continuing: %s",
                        closed_ledger_format_retries,
                        audit_format_error,
                    )
                try:
                    type_feedback = await _run_type_selection_judge(
                        audit_llm=audit_llm,
                        original_prompt=prompt,
                        source_text=paper_content,
                        candidate_text=content,
                        format_retries=closed_ledger_format_retries,
                        trace_dir=responses_dir,
                        trace_stem=f"{safe}.candidate_{attempt + 1}",
                    )
                    audit_feedback = list(
                        dict.fromkeys([*audit_feedback, *type_feedback])
                    )
                except ValueError as type_format_error:
                    if not closed_ledger_fail_open_on_format_error:
                        raise
                    nonblocking_audit_warning = {
                        "kind": "type_selection_audit_format_exhausted",
                        "message": str(type_format_error),
                        "candidate_attempt": attempt + 1,
                        "actual_attempts_per_atom": closed_ledger_format_retries,
                    }
                    logger.warning(
                        "    ⚠️  Type-selection audit formatting remained invalid after "
                        "%s actual per-atom attempts; preserving non-type audit feedback "
                        "and continuing: %s",
                        closed_ledger_format_retries,
                        type_format_error,
                    )
                if audit_feedback:
                    rejection_message = (
                        "Closed-ledger semantic audit rejected the draft:\n- "
                        + "\n- ".join(audit_feedback)
                    )
                    feedback_fingerprint = _closed_ledger_feedback_fingerprint(
                        audit_feedback
                    )
                    stalled = _should_stop_closed_ledger_retry(
                        current_fingerprint=feedback_fingerprint,
                        previous_fingerprint=previous_feedback_fingerprint,
                        attempt=attempt,
                        max_retries=max_retries,
                        nonblocking=closed_ledger_nonblocking_after_exhaustion,
                    )
                    if stalled:
                        warning_kind = (
                            "semantic_audit_exhausted"
                            if attempt >= max_retries - 1
                            else "semantic_audit_stalled"
                        )
                        nonblocking_audit_warning = {
                            "kind": warning_kind,
                            "message": rejection_message,
                            "candidate_attempt": attempt + 1,
                            "semantic_attempt_budget": max_retries,
                            "feedback_fingerprint": sorted(feedback_fingerprint),
                            "feedback_history": [
                                *validation_feedback_history,
                                rejection_message,
                            ],
                        }
                        if warning_kind == "semantic_audit_stalled":
                            logger.warning(
                                "    ⚠️  Closed-ledger semantic audit repeated the same "
                                "filtered findings; preserving the structurally valid "
                                "candidate and continuing non-blockingly."
                            )
                        else:
                            logger.warning(
                                "    ⚠️  Closed-ledger semantic audit exhausted %s candidate "
                                "attempts; preserving the final structurally valid candidate "
                                "and continuing non-blockingly.",
                                max_retries,
                            )
                    else:
                        previous_feedback_fingerprint = feedback_fingerprint
                        raise ValueError(rejection_message)
            
            # Save result to pre_extraction folder
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # CRITICAL: Verify file was actually written
            if not os.path.exists(output_path):
                raise IOError(f"Failed to write pre-extraction file: {output_path}")
            
            # Verify file has content
            with open(output_path, 'r', encoding='utf-8') as f:
                written_content = f.read()
            if not written_content or not written_content.strip():
                raise IOError(f"Pre-extraction file was created but is empty: {output_path}")
            
            # Also save response in responses folder for tracking
            os.makedirs(responses_dir, exist_ok=True)
            response_file = os.path.join(responses_dir, f"{safe}.md")
            with open(response_file, 'w', encoding='utf-8') as f:
                f.write(f"# Iteration {iter_num} Pre-Extraction Response\n\n")
                f.write(f"**Entity**: {entity_label}\n\n")
                f.write(f"**Model**: {model_name}\n\n")
                f.write("---\n\n")
                f.write(content)
            if nonblocking_audit_warning is not None:
                warning_file = _bounded_sidecar_path(
                    responses_dir,
                    safe,
                    ".closed_ledger_warning.json",
                )
                warning_file.write_text(
                    json.dumps(
                        nonblocking_audit_warning,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            
            logger.info(f"    ✅ Pre-extraction completed ({len(content)} chars) - file verified")
            return content
            
        except Exception as e:
            validation_feedback_history.append(str(e))
            if attempt < max_retries - 1:
                wait_time = _pre_extraction_retry_wait_seconds(e, attempt)
                logger.warning(f"    ⚠️  Pre-extraction attempt {attempt + 1}/{max_retries} failed: {e}")
                if wait_time > 0:
                    logger.info(f"    ⏳ Waiting {wait_time:g}s before retry...")
                    await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Pre-extraction failed after {max_retries} attempts: {e}")
                raise RuntimeError(f"Failed to pre-extract for entity '{entity_label}' after {max_retries} attempts. Last error: {e}")
    
    # Should never reach here due to raise above, but just in case
    raise RuntimeError(f"Failed to pre-extract for entity '{entity_label}' after {max_retries} attempts")


def _load_reused_pre_extraction(
    *,
    data_dir: str,
    doi_hash: str,
    entity_label: str,
) -> str:
    """Load a frozen pre-extraction checkpoint without invoking an LLM."""
    safe = _safe_name(entity_label)
    path = os.path.join(
        data_dir,
        doi_hash,
        "pre_extraction",
        f"entity_text_{safe}.txt",
    )
    try:
        content = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Frozen pre-extraction checkpoint is missing for '{entity_label}': {path}"
        ) from exc
    if not content.strip():
        raise RuntimeError(
            f"Frozen pre-extraction checkpoint is empty for '{entity_label}': {path}"
        )
    return content


def _compose_downstream_iteration_source(
    *,
    data_dir: str,
    doi_hash: str,
    entity_label: str,
    iteration_input: str,
    fallback_source: str,
) -> str:
    """Keep downstream property extraction inside the current entity scope."""
    try:
        scoped_source = _load_reused_pre_extraction(
            data_dir=data_dir,
            doi_hash=doi_hash,
            entity_label=entity_label,
        )
    except RuntimeError:
        scoped_source = ""
    sections = [
        section.strip()
        for section in (scoped_source, iteration_input)
        if section.strip()
    ]
    return "\n\n".join(sections) or fallback_source


async def run_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    source_text: str,
    prompt_template: str,
    model_key: str,
    hints_file: str,
    iter_num: int,
    use_agent: bool = False,
    mcp_tools: list = None,
    mcp_set_name: str = None,
    freshness_paths: List[str] | None = None,
    extraction_validation: dict | None = None,
    iteration_input: str = "",
    accumulated_hints: str = "",
    identity_dossier: dict | None = None,
    enforce_closed_ledger_projection: bool = False,
    hint_representation: str = "ref-entity-relations.v1",
    procedure_inheritance_brief: str = "",
) -> str:
    """
    Run extraction (hints generation) for an entity.
    Can use either a simple LLM or an agent with MCP tools.
    
    Returns:
        Extracted hints content
    """
    extraction_validation = _merge_mcp_set_extraction_validation(
        extraction_validation,
        mcp_set_name,
    )
    freshness_inputs = list(freshness_paths or [])
    freshness_inputs.append(__file__)

    # Check if already exists
    if _artifact_is_current(hints_file, freshness_inputs):
        logger.info(f"    ⏭️  Extraction already exists for '{entity_label}'")
        with open(hints_file, 'r', encoding='utf-8') as f:
            return f.read()
    if os.path.exists(hints_file):
        logger.info(f"    🔁 Existing extraction is stale for '{entity_label}', regenerating")
    
    logger.info(f"    🔍 Running extraction for '{entity_label}'...")
    
    prompt = bind_runtime_context(
        prompt_template,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        source_text=source_text,
        iteration_input=iteration_input,
        accumulated_hints=accumulated_hints,
        identity_dossier=identity_dossier,
    )
    prompt = _inject_procedure_inheritance_brief(
        prompt,
        procedure_inheritance_brief,
    )
    semantic_hint_mode = hint_representation == "semantic-text.v1"
    prompt = (
        _append_semantic_hint_output_boundary(prompt)
        if semantic_hint_mode
        else _append_ref_entity_output_boundary(prompt)
    )
    prompt = _append_complete_target_passage(prompt, source_text)
    prompt = _append_complete_inheritance_context(
        prompt,
        procedure_inheritance_brief,
    )
    prompt = _inject_required_tool_contract(
        prompt,
        extraction_validation,
        use_agent=bool(use_agent and mcp_tools and mcp_set_name),
    )
    required_tool_groups = _required_executed_tool_groups(extraction_validation)
    
    # Save full prompt for debugging in organized subfolder
    safe = _safe_name(entity_label)
    # Determine the prompt directory based on iteration type
    prompts_dir = os.path.join(os.path.dirname(os.path.dirname(hints_file)), "prompts", f"iter{iter_num}_extraction")
    os.makedirs(prompts_dir, exist_ok=True)
    prompt_file = os.path.join(prompts_dir, f"{safe}.md")
    try:
        mode = (
            f"**Mode**: Agent with MCP tools\n\n"
            f"**MCP Tools**: {mcp_tools}\n\n"
            f"**MCP Set**: {mcp_set_name}\n\n"
            if use_agent
            else "**Mode**: Simple LLM\n\n"
        )
        _write_text_with_parent(
            prompt_file,
            f"# Iteration {iter_num} Extraction Prompt\n\n"
            f"**Entity**: {entity_label}\n\n"
            f"**Entity URI**: {entity_uri}\n\n"
            f"**Model**: {get_extraction_model(model_key)}\n\n"
            f"{mode}---\n\n{prompt}",
        )
        logger.info(f"    💾 Saved prompt to: {prompt_file}")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to save prompt: {e}")
    
    # Get model
    model_name = get_extraction_model(model_key)

    def _build_revision_prompt(*, original_prompt: str, original_source: str, draft_output: str) -> str:
        return (
            "You are revising an extraction draft so it strictly complies with the original extraction prompt.\n\n"
            "Requirements:\n"
            "- Follow the ORIGINAL EXTRACTION PROMPT exactly.\n"
            "- Use ONLY the ORIGINAL SOURCE TEXT and the ORIGINAL EXTRACTION PROMPT as authority.\n"
            "- Keep only fields/assertions that are explicitly supported by the source text or explicitly allowed as derived values by the original prompt.\n"
            "- Remove weak guesses, speculative inferences, prophylactic/preventive interpretations, and any field not clearly justified by the prompt + source.\n"
            "- If the source text presents mutually exclusive alternatives (for example, branches joined by 'or', 'alternatively', 'either', or similar wording), do NOT serialize those alternatives as consecutive events in one linear output unless the ORIGINAL EXTRACTION PROMPT explicitly asks for branching.\n"
            "- When a mutually exclusive alternative must be reduced to one linear path and the ORIGINAL EXTRACTION PROMPT gives no tie-breaker, keep the first explicit branch and drop later alternative branches.\n"
            "- Treat exclusion rules, NOT/ONLY conditions, and conflict-resolution rules in the original prompt as higher priority than tentative positive matches in the draft.\n"
            "- If a field is mentioned only in prevention/risk/avoidance, setup/closure, historical/background, or otherwise excluded context, DROP that field unless the original prompt explicitly allows it.\n"
            "- If the original prompt provides allowed concrete instance types, do NOT keep generic parent/container labels as emitted instance types when a concrete type can be selected from the prompt.\n"
            "- Output ONLY the final extraction hints, with no explanations, no reasoning, no summary, no markdown code fences, and no missing-value commentary.\n"
            "- Preserve the exact field/property names and exact marker tokens required by the original prompt.\n"
            "- If the draft contains narrative sections, convert them into the strict final hint format required by the original prompt.\n"
            "- Omit unsupported fields entirely unless the original prompt explicitly requires a fixed negative/positive marker token.\n\n"
            "ORIGINAL EXTRACTION PROMPT:\n"
            "<<<PROMPT\n"
            f"{original_prompt}\n"
            "PROMPT\n>>>\n\n"
            "ORIGINAL SOURCE TEXT:\n"
            "<<<SOURCE\n"
            f"{original_source}\n"
            "SOURCE\n>>>\n\n"
            "DRAFT OUTPUT TO REVISE:\n"
            "<<<DRAFT\n"
            f"{draft_output}\n"
            "DRAFT\n>>>\n\n"
            "Return ONLY the revised final hints.\n"
        )

    def _build_support_audit_prompt(*, original_prompt: str, original_source: str, candidate_output: str) -> str:
        return (
            "You are auditing extraction hints for strict evidential support.\n\n"
            "Task:\n"
            "- Review EVERY field currently present in the CANDIDATE OUTPUT.\n"
            "- Keep a field ONLY if it is directly supported by the ORIGINAL SOURCE TEXT or explicitly allowed as a derived value by the ORIGINAL EXTRACTION PROMPT.\n"
            "- If the CANDIDATE OUTPUT linearizes mutually exclusive alternatives from the source into multiple simultaneous fields/events, reduce it to ONE canonical branch unless the ORIGINAL EXTRACTION PROMPT explicitly requests branching.\n"
            "- When reducing mutually exclusive alternatives to one canonical branch and the ORIGINAL EXTRACTION PROMPT gives no tie-breaker, keep the first explicit branch from the source and drop later alternatives.\n"
            "- If exclusion rules in the ORIGINAL EXTRACTION PROMPT conflict with a positive-looking mention, the exclusion rule wins.\n"
            "- Be conservative: if support is ambiguous, indirect, preventive, prophylactic, historical, setup-related, or otherwise excluded, DROP the field.\n"
            "- Return valid JSON with exactly two top-level keys: `supported_output` and `dropped_fields`.\n"
            "- `supported_output` must contain only the final supported extraction structure.\n"
            "- `dropped_fields` must be a list of objects with keys `field` and `reason`.\n"
            "- Do not include markdown fences, explanations, or any extra keys.\n\n"
            "ORIGINAL EXTRACTION PROMPT:\n"
            "<<<PROMPT\n"
            f"{original_prompt}\n"
            "PROMPT\n>>>\n\n"
            "ORIGINAL SOURCE TEXT:\n"
            "<<<SOURCE\n"
            f"{original_source}\n"
            "SOURCE\n>>>\n\n"
            "CANDIDATE OUTPUT:\n"
            "<<<CANDIDATE\n"
            f"{candidate_output}\n"
            "CANDIDATE\n>>>\n"
        )

    def _strip_code_fences(text: str) -> str:
        stripped = (text or "").strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
            stripped = re.sub(r"\s*```$", "", stripped, count=1)
        return stripped.strip()

    def _extract_expected_leaf_property_names(prompt_text: str) -> set[str]:
        """
        Best-effort extraction of canonical leaf property names from the generated prompt.

        We only validate leaf keys (actual emitted properties), not container section names
        like `PatientInfo` or `Procedure`.
        """
        expected: set[str] = set()
        for match in re.finditer(r"^- ([A-Za-z0-9_]+)\s+\(xsd:[^)]+\):", prompt_text or "", re.MULTILINE):
            expected.add(match.group(1))
        return expected

    def _iter_leaf_json_keys(value) -> list[str]:
        keys: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    keys.extend(_iter_leaf_json_keys(child))
                else:
                    keys.append(str(key))
        elif isinstance(value, list):
            for item in value:
                keys.extend(_iter_leaf_json_keys(item))
        return keys

    def _parse_structured_output(text: str):
        cleaned = _strip_code_fences(text)
        if not cleaned:
            return None
        try:
            return json.loads(cleaned)
        except Exception:
            pass
        try:
            import yaml  # type: ignore

            return yaml.safe_load(cleaned)
        except Exception:
            return None

    def _generic_ordered_member_type_errors(text: str, validation_cfg: dict | None) -> list[str]:
        """Detect configured ordered-member outputs that used generic container labels."""
        cfg = validation_cfg or {}
        rule = cfg.get("forbid_generic_ordered_member_types", {}) if isinstance(cfg, dict) else {}
        if not isinstance(rule, dict) or not bool(rule.get("enabled")):
            return []
        payload = _parse_structured_output(text)
        if payload is None:
            return []
        generic_labels = {
            re.sub(r"[^a-z]", "", str(label or "").strip().lower())
            for label in (rule.get("generic_labels") or [])
        }
        generic_labels = {label for label in generic_labels if label}
        if not generic_labels:
            return []
        generic_key_patterns = [
            re.compile(str(pattern), flags=re.IGNORECASE)
            for pattern in (rule.get("generic_key_patterns") or [])
            if str(pattern or "").strip()
        ]
        type_keys = set(rule.get("type_keys") or ["rdf:type", "type", "class"])
        errors: list[str] = []

        def has_concrete_step_type(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            for type_key in type_keys:
                raw_type = item.get(type_key)
                if raw_type is None:
                    continue
                type_norm = re.sub(r"[^a-z]", "", str(raw_type).split(":")[-1].lower())
                return bool(type_norm and type_norm not in generic_labels)
            return False

        def visit(value, path: str = "$") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key or "").strip()
                    key_norm = re.sub(r"[^a-z]", "", key_text.lower())
                    key_matches_pattern = any(pattern.match(key_text) for pattern in generic_key_patterns)
                    if (key_norm in generic_labels or key_matches_pattern) and isinstance(child, dict) and not has_concrete_step_type(child):
                        errors.append(f"{path}.{key_text}: generic step key")
                    if key_text in type_keys:
                        child_norm = re.sub(r"[^a-z]", "", str(child or "").strip().lower())
                        if child_norm in generic_labels:
                            errors.append(f"{path}.{key_text}: generic step type `{child}`")
                    visit(child, f"{path}.{key_text}")
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    visit(item, f"{path}[{idx}]")

        visit(payload)
        return errors

    def _configured_required_member_errors(text: str, source: str, validation_cfg: dict | None) -> list[str]:
        """Validate configured source-triggered required member types/properties."""
        cfg = validation_cfg or {}
        rules = cfg.get("require_members_when_source_matches", []) if isinstance(cfg, dict) else []
        if not isinstance(rules, list) or not rules:
            return []
        payload = _parse_structured_output(text)
        if payload is None:
            return []
        errors: list[str] = []

        def _type_locals(item: Any) -> set[str]:
            if not isinstance(item, dict):
                return set()
            raw = item.get("rdf:type") or item.get("type") or item.get("class") or []
            values = raw if isinstance(raw, list) else [raw]
            return {
                re.sub(r"[^a-z]", "", str(value).split(":")[-1].lower())
                for value in values
                if str(value or "").strip()
            }

        for idx, raw_rule in enumerate(rules):
            if not isinstance(raw_rule, dict) or not bool(raw_rule.get("enabled", True)):
                continue
            patterns = [str(p) for p in raw_rule.get("source_patterns", []) or [] if str(p or "").strip()]
            if patterns and not any(re.search(pattern, source or "", flags=re.IGNORECASE | re.DOTALL) for pattern in patterns):
                continue
            section_name = str(raw_rule.get("section_name") or "").strip()
            if not section_name:
                errors.append(
                    f"configured required member rule {idx}: missing `section_name`"
                )
                continue
            members = payload.get(section_name) if isinstance(payload, dict) else None
            if not isinstance(members, list):
                errors.append(f"configured required member rule {idx}: missing list section `{section_name}`")
                continue
            expected_type = re.sub(r"[^a-z]", "", str(raw_rule.get("expected_type") or "").split(":")[-1].lower())
            required_properties = [
                str(prop).strip()
                for prop in raw_rule.get("required_properties", []) or []
                if str(prop or "").strip()
            ]
            matching_members = [
                member
                for member in members
                if not expected_type or expected_type in _type_locals(member)
            ]
            if not matching_members:
                errors.append(
                    f"configured required member rule {idx}: source evidence requires `{raw_rule.get('expected_type')}`"
                )
                continue
            missing_props = [
                prop
                for prop in required_properties
                if not any(isinstance(member, dict) and prop in member for member in matching_members)
            ]
            if missing_props:
                errors.append(
                    f"configured required member rule {idx}: `{raw_rule.get('expected_type')}` missing properties {missing_props}"
                )
        return errors

    def _required_tool_activity_errors(metadata: dict | None) -> list[str]:
        validation = dict(extraction_validation or {})
        presence_cfg = validation.get("presence_coverage_audit") or {}
        if presence_cfg.get("enabled") and mcp_tools:
            from src.agents.scripts_and_prompts_generation.presence_coverage_judge import (
                catalog_for_groups,
                format_presence_coverage_feedback,
                judge_tool_coverage,
            )

            activity = (metadata or {}).get("tool_activity") or {}
            report = judge_tool_coverage(
                hints_text=str(locals().get("content") or ""),
                tool_activity=activity,
                catalog=catalog_for_groups(
                    presence_cfg.get("mcp_groups") or mcp_tools
                ),
                require_configured_groups=True,
            )
            if not report.get("accepted"):
                return [format_presence_coverage_feedback({"tool_coverage": report})]
        groups = validation.get("required_executed_tool_groups") or []
        if not groups:
            return []
        activity = (metadata or {}).get("tool_activity") or {}
        executed = {
            str(name).strip()
            for name in (activity.get("executed_tool_names") or [])
            if str(name).strip()
        }
        errors: list[str] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            candidates = {
                str(name).strip()
                for name in (group.get("any_of") or [])
                if str(name).strip()
            }
            if candidates and executed.isdisjoint(candidates):
                errors.append(
                    f"{group.get('name') or 'required MCP lookup'} requires one of "
                    f"{sorted(candidates)}; executed={sorted(executed)}"
                )
        return errors
    
    # Extract with retries (increased to 5 attempts)
    max_retries = 5
    agent = None  # Initialize agent once outside retry loop
    allow_agent_fallback = True  # if MCP tool sessions fail, fall back to simple LLM
    llm = None
    contract_critic_llm = None
    operation_projection_llm = None
    
    last_validation_error = ""
    last_feedback_kind = ""
    validation_feedback_history: list[str] = []
    for attempt in range(max_retries):
        try:
            semantic_report: dict[str, Any] | None = None
            nonblocking_audit_warnings: list[dict[str, Any]] = []
            effective_prompt = prompt
            if last_validation_error:
                if last_feedback_kind == "required_tools":
                    effective_prompt = (
                        prompt
                        + "\n\n"
                        + last_validation_error
                        + "\n"
                    )
                else:
                    effective_prompt = (
                        prompt
                        + "\n\nVALIDATION FEEDBACK FROM ALL PREVIOUS ATTEMPTS "
                        "(oldest to newest):\n"
                        + "\n\n".join(
                            f"ATTEMPT {index} FAILURE:\n{feedback}"
                            for index, feedback in enumerate(
                                validation_feedback_history, start=1
                            )
                        )
                        + "\nReturn corrected extraction hints only. Fix every recorded "
                        "issue and preserve all source-supported facts accepted by earlier "
                        "feedback; do not regress an earlier correction.\n"
                    )
            if use_agent and mcp_tools and mcp_set_name:
                # Use agent with MCP tools (e.g., for iter2)
                # Create agent only once on first attempt, reuse for retries
                if agent is None:
                    logger.info(f"    🤖 Initializing agent with MCP tools: {mcp_tools}")
                    BaseAgent = _get_base_agent()
                    agent = BaseAgent(
                        model_name=model_name,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                        mcp_tools=mcp_tools,
                        mcp_set_name=mcp_set_name
                    )
                
                logger.info(f"    🔍 Running agent extraction (attempt {attempt + 1}/{max_retries})")
                result, agent_meta = await retry_async_on_transport(
                    lambda: agent.run(effective_prompt, recursion_limit=600),
                    logger=logger,
                    what=f"extraction agent '{entity_label}'",
                )
                content = _normalize_llm_content(result)
                tool_activity_errors = _required_tool_activity_errors(agent_meta)
                if tool_activity_errors:
                    activity = (agent_meta or {}).get("tool_activity") or {}
                    feedback = _format_required_tool_feedback(
                        tool_activity_errors,
                        groups=required_tool_groups,
                        executed_tool_names=list(
                            activity.get("executed_tool_names") or []
                        ),
                    )
                    # Retry while budget remains. After the last attempt, keep
                    # a non-empty draft and continue instead of aborting the paper.
                    if _should_fail_open_required_tool_gate(
                        attempt=attempt,
                        max_retries=max_retries,
                        content=content,
                    ):
                        nonblocking_audit_warnings.append(
                            {
                                "kind": "required_tool_activity_exhausted",
                                "message": feedback,
                                "candidate_attempt": attempt + 1,
                                "tool_attempt_budget": max_retries,
                            }
                        )
                        logger.warning(
                            "    ⚠️  Required MCP tool-activity budget exhausted "
                            "for '%s'; preserving the final extraction candidate "
                            "and continuing.",
                            entity_label,
                        )
                    else:
                        raise ValueError(feedback)
            else:
                # Use the configured direct LLM runtime.
                logger.info(f"    🔍 Running simple LLM extraction (attempt {attempt + 1}/{max_retries})")
                if llm is None:
                    llm = LLMCreator(
                        model=model_name,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                    ).setup_llm()
                result = await retry_async_on_transport(
                    lambda: llm.ainvoke(effective_prompt),
                    logger=logger,
                    what=f"extraction llm '{entity_label}'",
                )
                content = _normalize_llm_content(result)
                agent_meta = {}
                # Preserve the model's representation. All content quality decisions are
                # delegated to LLM judges below; deterministic code checks schema only.
            
            # CRITICAL VALIDATION: Check if content is meaningful
            if not content or not content.strip():
                raise ValueError(f"LLM returned empty content for entity '{entity_label}'")

            short_marker = is_marker_only_optional_output(content)
            if short_marker:
                logger.warning(
                    "    Non-blocking marker-only extraction diagnostic %r for '%s'",
                    content.strip(),
                    entity_label,
                )

            if semantic_hint_mode:
                if not content.lstrip().startswith("SEMANTIC_HINTS_V1"):
                    raise ValueError(
                        "Semantic hints must begin with the exact marker "
                        "SEMANTIC_HINTS_V1"
                    )
                if enforce_closed_ledger_projection:
                    if operation_projection_llm is None:
                        operation_projection_llm = LLMCreator(
                            model=model_name,
                            model_config=ModelConfig(temperature=0, top_p=1.0),
                            remote_model=True,
                        ).setup_llm()
                    semantic_report = await _run_operation_projection_panel(
                        audit_llm=operation_projection_llm,
                        original_prompt=prompt,
                        source_text=source_text,
                        candidate_text=content,
                    )
                    if not bool(
                        (semantic_report.get("acceptance") or {}).get("accepted")
                    ):
                        rejection = (
                            "Closed-ledger operation projection rejected the extraction:\n- "
                            + "\n- ".join(semantic_report.get("feedback") or [])
                        )
                        if attempt >= max_retries - 1:
                            nonblocking_audit_warnings.append(
                                {
                                    "kind": "operation_projection_audit_exhausted",
                                    "message": rejection,
                                    "candidate_attempt": attempt + 1,
                                }
                            )
                            logger.warning(
                                "    ⚠️  Operation-projection audit budget exhausted; "
                                "preserving the final schema-valid candidate."
                            )
                        else:
                            raise ValueError(rejection)
            else:
                try:
                    ok_hint_payload, hint_errors = validate_hint_payload(
                        content,
                        allow_empty=short_marker,
                        accumulated_hints=accumulated_hints,
                        expected_schema="ref-entity-relations.v1",
                        allowed_entity_iris={entity_uri},
                    )
                except ValueError as exc:
                    ok_hint_payload, hint_errors = False, [str(exc)]
                if not ok_hint_payload:
                    raise ValueError(
                        "Extraction hint representation is not materializable:\n- "
                        + "\n- ".join(hint_errors)
                    )
            if enforce_closed_ledger_projection and not semantic_hint_mode:
                try:
                    ledger_reference: Any = json.loads(
                        _strip_code_fences_block(source_text)
                    )
                except Exception:
                    ledger_reference = source_text
                projection_report = await asyncio.to_thread(
                    judge_extraction_semantics,
                    document_text=source_text,
                    ontology_contract={
                        "iteration": iter_num,
                        "target_entity": {
                            "label": entity_label,
                            "iri": entity_uri,
                        },
                        "semantic_contract": prompt_template,
                        "validation_policy": (
                            "LLM-only semantic projection fidelity; do not require "
                            "serialization or lexical equality"
                        ),
                    },
                    extracted_content=content,
                    reference_content=ledger_reference,
                    models=[model_name],
                    prior_feedback=validation_feedback_history,
                )
                if not bool(
                    (projection_report.get("acceptance") or {}).get("accepted")
                ):
                    rejection = (
                        "LLM semantic projection audit rejected the base hints:\n"
                        + json.dumps(
                            {
                                "acceptance": projection_report.get("acceptance"),
                                "observations": projection_report.get("observations"),
                            },
                            ensure_ascii=False,
                        )
                    )
                    if attempt >= max_retries - 1:
                        nonblocking_audit_warnings.append(
                            {
                                "kind": "semantic_projection_audit_exhausted",
                                "message": rejection,
                                "candidate_attempt": attempt + 1,
                            }
                        )
                        logger.warning(
                            "    ⚠️  Semantic-projection audit budget exhausted; "
                            "preserving the final schema-valid candidate."
                        )
                    else:
                        raise ValueError(rejection)
            critic_cfg = (
                (extraction_validation or {}).get("llm_tbox_contract_feedback")
                or {}
            )
            critic_enabled = not semantic_hint_mode and bool(
                critic_cfg.get(
                    "enabled",
                    True,
                )
            )
            if critic_enabled:
                if contract_critic_llm is None:
                    critic_model = str(
                        critic_cfg.get("model") or model_name
                    ).strip()
                    contract_critic_llm = LLMCreator(
                        model=critic_model,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                    ).setup_llm()
                audit_prompt = _build_tbox_contract_audit_prompt(
                    original_prompt=prompt,
                    source_text=source_text,
                    candidate_text=content,
                )
                draft_audit_result = await contract_critic_llm.ainvoke(audit_prompt)
                refinement_prompt = _build_tbox_contract_audit_refinement_prompt(
                    audit_prompt=audit_prompt,
                    draft_checklist=_normalize_llm_content(draft_audit_result),
                )
                audit_error = ""
                contract_feedback: list[str] = []
                for audit_attempt in range(3):
                    audit_result = await contract_critic_llm.ainvoke(
                        refinement_prompt
                        + (
                            "\n\nYour previous verdict was invalid: "
                            f"{audit_error}\nReturn only schema-valid JSON."
                            if audit_error
                            else ""
                        )
                    )
                    try:
                        _, contract_feedback = _parse_tbox_contract_audit(
                            _normalize_llm_content(audit_result),
                            source_text=source_text,
                            contract_text=prompt,
                            candidate_text=content,
                        )
                        audit_error = ""
                        break
                    except ValueError as exc:
                        audit_error = str(exc)
                        logger.warning(
                            "    Contract critic format attempt %s/3 failed: %s",
                            audit_attempt + 1,
                            exc,
                        )
                if audit_error:
                    nonblocking_audit_warnings.append(
                        {
                            "kind": "tbox_contract_audit_format_exhausted",
                            "message": audit_error,
                            "format_attempt_budget": 3,
                        }
                    )
                    contract_feedback = []
                    logger.warning(
                        "    ⚠️  T-Box contract audit format budget exhausted; "
                        "preserving the schema-valid extraction candidate: %s",
                        audit_error,
                    )
                multiplicity_prompt = _build_tbox_contract_multiplicity_audit_prompt(
                    audit_prompt=audit_prompt
                )
                multiplicity_error = ""
                for multiplicity_attempt in range(3):
                    multiplicity_result = await contract_critic_llm.ainvoke(
                        multiplicity_prompt
                        + (
                            "\n\nYour previous checklist was invalid: "
                            f"{multiplicity_error}\nReturn only schema-valid JSON."
                            if multiplicity_error
                            else ""
                        )
                    )
                    try:
                        _, multiplicity_feedback = _parse_tbox_contract_audit(
                            _normalize_llm_content(multiplicity_result),
                            source_text=source_text,
                            contract_text=prompt,
                            candidate_text=content,
                        )
                        contract_feedback = list(
                            dict.fromkeys(
                                [*contract_feedback, *multiplicity_feedback]
                            )
                        )
                        multiplicity_error = ""
                        break
                    except ValueError as exc:
                        multiplicity_error = str(exc)
                        logger.warning(
                            "    Multiplicity critic format attempt %s/3 failed: %s",
                            multiplicity_attempt + 1,
                            exc,
                        )
                if multiplicity_error:
                    nonblocking_audit_warnings.append(
                        {
                            "kind": "multiplicity_audit_format_exhausted",
                            "message": multiplicity_error,
                            "format_attempt_budget": 3,
                        }
                    )
                    logger.warning(
                        "    ⚠️  Multiplicity audit format budget exhausted; "
                        "preserving the schema-valid extraction candidate: %s",
                        multiplicity_error,
                    )
                if contract_feedback:
                    rejection = (
                        "LLM T-Box contract critic rejected the draft:\n- "
                        + "\n- ".join(contract_feedback)
                    )
                    if attempt >= max_retries - 1:
                        nonblocking_audit_warnings.append(
                            {
                                "kind": "tbox_contract_audit_exhausted",
                                "message": rejection,
                                "candidate_attempt": attempt + 1,
                            }
                        )
                        logger.warning(
                            "    ⚠️  T-Box contract audit budget exhausted; "
                            "preserving the final schema-valid candidate."
                        )
                    else:
                        raise ValueError(rejection)
            
            # Save result to hints file (sanitize Greek/super-subscripts so KG
            # prompts never re-inject characters removed from source MD).
            from src.utils.source_text_sanitize import sanitize_source_markdown

            content = sanitize_source_markdown(content)
            _write_text_with_parent(hints_file, content)
            if semantic_report is not None:
                _write_text_with_parent(
                    _semantic_audit_sidecar_path(hints_file),
                    json.dumps(semantic_report, ensure_ascii=False, indent=2),
                )
            if nonblocking_audit_warnings:
                warning_path = _bounded_sidecar_path(
                    str(Path(hints_file).parent),
                    Path(hints_file).stem,
                    ".audit_exhaustion_warning.json",
                )
                warning_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "audit-exhaustion-warning.v1",
                            "warnings": nonblocking_audit_warnings,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )

            # CRITICAL: Verify file was actually written
            if not os.path.exists(hints_file):
                raise IOError(f"Failed to write hints file: {hints_file}")

            # Verify file has content
            with open(hints_file, "r", encoding="utf-8") as f:
                written_content = f.read()
            if not written_content or not written_content.strip():
                raise IOError(f"Hints file was created but is empty: {hints_file}")

            # Also save response in responses folder for tracking
            responses_dir = os.path.join(
                os.path.dirname(os.path.dirname(hints_file)),
                "responses",
                f"iter{iter_num}_extraction",
            )
            os.makedirs(responses_dir, exist_ok=True)
            response_file = os.path.join(responses_dir, f"{safe}.md")
            with open(response_file, "w", encoding="utf-8") as f:
                f.write(f"# Iteration {iter_num} Extraction Response\n\n")
                f.write(f"**Entity**: {entity_label}\n\n")
                f.write(f"**Model**: {model_name}\n\n")
                if use_agent:
                    f.write(f"**Mode**: Agent with MCP tools\n\n")
                    f.write(f"**MCP Tools**: {mcp_tools}\n\n")
                    tool_activity = (agent_meta or {}).get("tool_activity") or {}
                    f.write(
                        "**Executed MCP Tools**: "
                        f"{tool_activity.get('executed_tool_name_set') or []}\n\n"
                    )
                    f.write(
                        "**MCP Tool Calls**: "
                        f"{tool_activity.get('tool_message_count') or 0}\n\n"
                    )
                else:
                    f.write(f"**Mode**: Simple LLM\n\n")
                f.write("---\n\n")
                f.write(content)
            
            logger.info(f"    ✅ Extraction completed ({len(content)} chars) - hints file verified")
            return content
            
        except Exception as e:
            last_validation_error = str(e)
            last_feedback_kind = (
                "required_tools"
                if "REQUIRED MCP TOOL ACTIVITY FEEDBACK:" in last_validation_error
                else "validation"
            )
            if last_feedback_kind == "validation":
                validation_feedback_history.append(last_validation_error)
            # If MCP toolchain can't start (common on Windows without Docker / missing binaries),
            # fall back to simple LLM so we still produce extraction hint files.
            if (
                allow_agent_fallback
                and use_agent
                and (mcp_tools and mcp_set_name)
                and any(
                    s in str(e)
                    for s in (
                        "Could not open MCP session",
                        "unhandled errors in a TaskGroup",
                        "FileNotFoundError",
                        "WinError 2",
                        "Docker is not running",
                    )
                )
            ):
                logger.warning(
                    f"    ⚠️  MCP tools unavailable for iter{iter_num} extraction; "
                    f"falling back to simple LLM for '{entity_label}'. Error was: {e}"
                )
                # Disable agent path for subsequent retries in this extraction
                use_agent = False
                agent = None
                allow_agent_fallback = False
                # Retry immediately (no backoff) using simple LLM branch
                continue

            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)  # Exponential backoff: 5s, 10s, 15s, 20s
                if last_feedback_kind == "required_tools":
                    logger.warning(
                        "    ⚠️  Required MCP tool activity missing on attempt "
                        f"{attempt + 1}/{max_retries}; feeding tool-contract feedback and retrying"
                    )
                else:
                    logger.warning(
                        f"    ⚠️  Extraction attempt {attempt + 1}/{max_retries} failed: {e}"
                    )
                logger.info(f"    ⏳ Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Extraction failed after {max_retries} attempts: {e}")
                raise RuntimeError(f"Failed to extract hints for entity '{entity_label}' after {max_retries} attempts. Last error: {e}")
    
    # Should never reach here due to raise above, but just in case
    raise RuntimeError(f"Failed to extract hints for entity '{entity_label}' after {max_retries} attempts")


# KG building has been moved to a separate pipeline step
# This module ONLY handles extraction (hints generation)


def _write_extraction_completion_marker(marker_file: str) -> None:
    """Write the step marker while preserving its non-fatal failure semantics."""
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info("  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")


def _run_extractions_entity_first(
    doi_hash: str,
    config: dict,
    top_entities: list,
    marker_file: str,
) -> bool:
    """Process every main iteration and enrichment for one entity at a time."""
    successful_writes: list[int] = []
    all_ok = True
    for entity in top_entities:
        child_config = dict(config)
        child_config["_entity_first_entity_safe"] = _safe_name(
            entity.get("label", "")
        )
        child_config["_entity_first_successful_writes"] = successful_writes
        if not run_step(doi_hash, child_config):
            all_ok = False

    if not all_ok:
        return False
    if sum(successful_writes) <= 0:
        logger.error(
            "❌ Main ontology extractions produced no hints files; "
            "refusing to create completion marker"
        )
        return False

    _write_extraction_completion_marker(marker_file)
    logger.info(f"✅ Main Ontology Extractions completed for {doi_hash}")
    return True


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the main ontology extractions pipeline step.
    
    This step processes iterations 2+ for all top-level entities.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if all extractions succeeded
    """
    data_dir = config.get("data_dir", "data")
    doi_folder = os.path.join(data_dir, doi_hash)
    
    logger.info(f"▶️  Main Ontology Extractions for {doi_hash}")
    
    meta_config = load_meta_task_config(config.get("meta_task_config", "configs/meta_task/meta_task_config.json"))
    main_ontology = meta_config.get("ontologies", {}).get("main", {})
    ontology_name = get_main_ontology_name(meta_config, default="ontosynthesis")
    mcp_set_name = main_ontology.get("mcp_set_name", "run_created_mcp.json")
    mcp_tools = main_ontology.get("mcp_list", ["llm_created_mcp"])
    
    # Override with test MCP config if provided
    if "test_mcp_config" in config:
        test_mcp_config = config["test_mcp_config"]
        logger.info(f"  🧪 Using test MCP config: {test_mcp_config}")
        mcp_set_name = test_mcp_config
    
    logger.info(f"  📋 Ontology: {ontology_name}")
    logger.info(f"  🔧 MCP Config: {mcp_set_name}")
    
    # Load iterations config
    iterations_config_path = get_iterations_config_path(ontology_name)
    iterations_config = load_iterations_config(ontology_name)
    if not iterations_config:
        logger.error("❌ Failed to load iterations configuration")
        return False
    
    all_iterations = list(iterations_config.get("iterations", []))
    iterations = list(all_iterations)
    only_iterations = {
        int(value)
        for value in config.get("only_extraction_iterations", [])
        if str(value).strip().isdigit()
    }
    if only_iterations:
        iterations = [
            iteration
            for iteration in iterations
            if int(iteration.get("iteration_number") or -1) in only_iterations
        ]
    logger.info(f"  📊 Found {len(iterations)} iterations to process")
    
    # Load top entities
    top_entities = load_top_entities(doi_hash, data_dir)
    if not top_entities:
        logger.error("❌ No top entities found")
        return False
    top_entity_manifest = list(top_entities)

    selected_entity_safe = config.get("only_entity_safe") or config.get(
        "_entity_first_entity_safe"
    )
    if selected_entity_safe:
        top_entities = [
            entity
            for entity in top_entities
            if _safe_name(entity.get("label", "")) == selected_entity_safe
        ]
        if not top_entities:
            logger.error(
                "❌ Selected entity not found for entity-first extraction: %s",
                selected_entity_safe,
            )
            return False
    
    logger.info(f"  🎯 Processing {len(top_entities)} top-level entities")

    # Check if step is already completed. Marker is only trusted if the current
    # per-entity iteration set has actually produced all expected hints files.
    marker_file = os.path.join(doi_folder, ".main_ontology_extractions_done")
    if (
        not selected_entity_safe
        and not only_iterations
        and os.path.exists(marker_file)
    ):
        if _expected_hint_files_exist(doi_hash, iterations, top_entities, data_dir, iterations_config_path):
            logger.info(f"  ⏭️  Main ontology extractions already completed (marker exists)")
            return True
        logger.warning("  🔁 Marker exists but required hints are missing; re-running main ontology extractions")

    if not selected_entity_safe:
        return _run_extractions_entity_first(
            doi_hash=doi_hash,
            config=config,
            top_entities=top_entities,
            marker_file=marker_file,
        )
    
    # Load paper content
    paper_content, paper_source_paths = load_paper_content_with_sources(doi_hash, data_dir)
    if not paper_content:
        logger.error("❌ Failed to load paper content")
        return False
    try:
        tbox_path = Path(project_root) / "data" / "ontologies" / f"{ontology_name}.ttl"
        global_context_resolution = resolve_global_context(
            source_text=paper_content,
            tbox_contract=tbox_path.read_text(encoding="utf-8"),
            model=get_extraction_model("advanced_model"),
            cache_path=Path(doi_folder) / "global_procedure_context.json",
        )
        global_context_brief = render_global_context_brief(
            global_context_resolution
        )
    except Exception as exc:
        logger.warning(
            "⚠️  Global procedure context unresolved; "
            "continuing extraction without a shared-context brief: %s",
            exc,
        )
        global_context_brief = ""
    
    # Get skip extraction flags from config
    skip_iter2 = config.get("skip_iter2_extraction", False)
    skip_iter3 = config.get("skip_iter3_extraction", False)
    skip_iter4 = config.get("skip_iter4_extraction", False)
    successful_hint_writes = 0
    encountered_errors = False
    
    # Process each iteration
    for iteration in iterations:
        iter_num = iteration.get("iteration_number")
        iter_name = iteration.get("name", f"iteration_{iter_num}")
        per_entity = iteration.get("per_entity", False)
        use_agent = iteration.get("use_agent", False)
        has_pre_extraction = iteration.get("has_pre_extraction", False)
        
        logger.info(f"\n  🔄 Iteration {iter_num}: {iter_name}")
        
        # Check if this iteration should be skipped
        if iter_num == 2 and skip_iter2:
            logger.info(f"    ⏭️  Skipping iteration 2 extraction (--skip-iter2-extraction)")
            continue
        if iter_num == 3 and skip_iter3:
            logger.info(f"    ⏭️  Skipping iteration 3 extraction (--skip-iter3-extraction)")
            continue
        if iter_num == 4 and skip_iter4:
            logger.info(f"    ⏭️  Skipping iteration 4 extraction (--skip-iter4-extraction)")
            continue
        
        if not per_entity:
            logger.warning(f"    ⚠️  Iteration {iter_num} is not per-entity, skipping")
            continue
        
        # Get paths and config
        pre_extraction_prompt_path = iteration.get("pre_extraction_prompt")
        extraction_prompt_path = iteration.get("extraction_prompt")
        model_key = iteration.get("model_config_key", f"iter{iter_num}_hints")
        pre_extraction_model_key = iteration.get(
            "pre_extraction_model_key", f"iter{iter_num}_pre_extraction"
        )
        if config.get("skip_parent_extraction_when_targeting_sub_iterations", False):
            has_pre_extraction = False
            pre_extraction_prompt_path = None
            extraction_prompt_path = None
            logger.info(
                "    ⏭️  Preserving baseline iteration %s hints while rerunning "
                "selected sub-iterations",
                iter_num,
            )
        
        # Process each entity
        for entity in top_entities:
            entity_label = entity.get("label", "")
            entity_uri = entity.get("uri", "")
            identity_dossier = dict(entity.get("identity_dossier") or {})
            safe = _safe_name(entity_label)
            
            logger.info(f"  📌 Entity: {entity_label}")
            
            # Get output paths from config (with fallback to defaults)
            outputs = iteration.get("outputs", {})
            hint_file_template = outputs.get("hints_file", f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt")
            hint_file = resolve_file_path(hint_file_template, doi_hash, safe, data_dir)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(hint_file), exist_ok=True)
            accumulated_hints, accumulated_hint_paths = (
                _load_accumulated_prior_hints(
                    iterations=all_iterations,
                    current_iteration=int(iter_num),
                    doi_hash=doi_hash,
                    entity_safe=safe,
                    data_dir=data_dir,
                )
            )
            
            # Step 1: Pre-extraction (if needed)
            source_text = paper_content
            procedure_inheritance_brief = ""
            if has_pre_extraction and pre_extraction_prompt_path:
                pre_extraction_prompt = load_prompt(pre_extraction_prompt_path)
                if not pre_extraction_prompt:
                    logger.error(
                        "    ❌ Pre-extraction prompt is empty: %s",
                        pre_extraction_prompt_path,
                    )
                    encountered_errors = True
                    continue
                pre_extraction_prompt = inject_global_context_brief(
                    pre_extraction_prompt, global_context_brief
                )
                experimental_contract = str(
                    config.get("experimental_property_contract_block") or ""
                ).strip()
                if experimental_contract:
                    pre_extraction_prompt = (
                        pre_extraction_prompt
                        + "\n\n"
                        + experimental_contract
                    )
                try:
                    inheritance_resolution = resolve_procedure_inheritance(
                        source_text=paper_content,
                        target_procedure_ref=entity_uri or f"label:{entity_label}",
                        target_procedure_label=entity_label,
                        tbox_contract=pre_extraction_prompt,
                        model=get_extraction_model(pre_extraction_model_key),
                        target_identity_dossier=identity_dossier,
                        top_entity_manifest=top_entity_manifest,
                        cache_path=(
                            Path(data_dir)
                            / doi_hash
                            / "procedure_inheritance"
                            / f"{safe}.json"
                        ),
                    )
                    procedure_inheritance_brief = (
                        render_procedure_inheritance_brief(inheritance_resolution)
                    )
                    if inheritance_resolution.get("fail_open") or (
                        inheritance_resolution.get("status") == "unresolved"
                    ):
                        logger.warning(
                            "    ⚠️  Procedure inheritance unresolved for '%s' after %s "
                            "attempt(s); continuing with source-only extraction: %s",
                            entity_label,
                            inheritance_resolution.get("resolution_attempts") or 1,
                            "; ".join(
                                str(reason)
                                for reason in (
                                    inheritance_resolution.get("unresolved_reasons")
                                    or ["panel disagreement"]
                                )
                            ),
                        )
                except Exception as e:
                    logger.warning(
                        "    ⚠️  Procedure inheritance failed for '%s'; "
                        "continuing with source-only extraction: %s",
                        entity_label,
                        e,
                    )
                    procedure_inheritance_brief = ""
                if config.get("reuse_pre_extraction_artifacts", False):
                    try:
                        source_text = _load_reused_pre_extraction(
                            data_dir=data_dir,
                            doi_hash=doi_hash,
                            entity_label=entity_label,
                        )
                        logger.info(
                            "    ♻️  Reused frozen pre-extraction checkpoint for '%s'",
                            entity_label,
                        )
                    except Exception as e:
                        logger.error(f"    ❌ Frozen pre-extraction reuse failed: {e}")
                        encountered_errors = True
                        continue
                else:
                    logger.info(f"    🔍 Pre-extraction for iteration {iter_num}")
                    try:
                        pre_validation = (
                            iteration.get("pre_extraction_validation") or {}
                        )
                        pre_retry_count = (
                            8
                            if bool(
                                (pre_validation.get("closed_ledger") or {}).get(
                                    "enabled"
                                )
                            )
                            else 5
                        )
                        pre_freshness = [
                            iterations_config_path,
                            pre_extraction_prompt_path,
                            __file__,
                            *paper_source_paths,
                            *accumulated_hint_paths,
                        ]
                        pre_extracted_text = asyncio.run(run_pre_extraction(
                            doi_hash, entity_label, entity_uri, paper_content,
                            pre_extraction_prompt, pre_extraction_model_key, iter_num, data_dir,
                            freshness_paths=pre_freshness,
                            identity_dossier=identity_dossier,
                            accumulated_hints=accumulated_hints,
                            pre_extraction_validation=pre_validation,
                            max_retries=pre_retry_count,
                            procedure_inheritance_brief=procedure_inheritance_brief,
                        ))
                        if pre_extracted_text:
                            source_text = pre_extracted_text
                    except Exception as e:
                        logger.error(f"    ❌ Pre-extraction failed: {e}")
                        encountered_errors = True
                        continue
            
            # Step 2: Extraction (hints generation)
            if extraction_prompt_path:
                logger.info(f"    📝 Extraction for iteration {iter_num}")
                extraction_prompt = load_prompt(extraction_prompt_path)
                if extraction_prompt:
                    extraction_prompt = inject_global_context_brief(
                        extraction_prompt, global_context_brief
                    )
                    experimental_contract = str(
                        config.get("experimental_property_contract_block") or ""
                    ).strip()
                    if experimental_contract:
                        extraction_prompt = (
                            extraction_prompt
                            + "\n\n"
                            + experimental_contract
                        )
                    iteration_input = ""
                    iteration_input_template = (
                        (iteration.get("inputs") or {}).get("file_path")
                        if isinstance(iteration.get("inputs"), dict)
                        else None
                    )
                    if iteration_input_template:
                        iteration_input_path = resolve_file_path(
                            str(iteration_input_template),
                            doi_hash,
                            safe,
                            data_dir,
                        )
                        try:
                            iteration_input = Path(iteration_input_path).read_text(
                                encoding="utf-8"
                            )
                        except FileNotFoundError:
                            logger.warning(
                                "    ⚠️  Configured iteration input is missing for "
                                f"'{entity_label}': {iteration_input_path}"
                            )
                    # Determine whether this configured iteration uses an agent.
                    extraction_uses_agent = use_agent and (
                        iteration.get("extraction_mcp_tools") is not None or 
                        iteration.get("mcp_tools") is not None
                    )
                    
                    # Get MCP configuration for extraction (if using agent)
                    # Use extraction-specific config if available, otherwise fall back to general config
                    extraction_mcp_set = iteration.get("extraction_mcp_set_name") or iteration.get("mcp_set_name") if extraction_uses_agent else None
                    extraction_mcp_tools = iteration.get("extraction_mcp_tools") or iteration.get("mcp_tools") if extraction_uses_agent else None
                    
                    # If test MCP config is provided, override the set name for generated
                    # ontology tools while leaving external extraction tools unchanged.
                    if (
                        "test_mcp_config" in config
                        and extraction_mcp_tools
                        and set(extraction_mcp_tools).issubset(set(mcp_tools or []))
                    ):
                        extraction_mcp_set = config["test_mcp_config"]
                    
                    try:
                        hint_freshness = [
                            iterations_config_path,
                            *_prompt_contract_dependency_paths(extraction_prompt_path),
                            __file__,
                            *paper_source_paths,
                        ]
                        if has_pre_extraction and pre_extraction_prompt_path:
                            hint_freshness.extend(
                                _prompt_contract_dependency_paths(
                                    pre_extraction_prompt_path
                                )
                            )
                        hint_freshness.extend(accumulated_hint_paths)
                        effective_source_text = _compose_downstream_iteration_source(
                            data_dir=data_dir,
                            doi_hash=doi_hash,
                            entity_label=entity_label,
                            iteration_input=iteration_input,
                            fallback_source=source_text,
                        )
                        hints = asyncio.run(run_extraction(
                            doi_hash, entity_label, entity_uri, effective_source_text,
                            extraction_prompt, model_key, hint_file, iter_num,
                            use_agent=extraction_uses_agent,
                            mcp_tools=extraction_mcp_tools,
                            mcp_set_name=extraction_mcp_set,
                            freshness_paths=hint_freshness,
                            extraction_validation=(
                                {
                                    **dict(iteration.get("extraction_validation") or {}),
                                    **(
                                        {
                                            "presence_coverage_audit": dict(
                                                config.get("presence_coverage_audit")
                                                or {}
                                            )
                                        }
                                        if config.get("presence_coverage_audit")
                                        else {}
                                    ),
                                }
                            ),
                            iteration_input=iteration_input,
                            accumulated_hints=accumulated_hints,
                            identity_dossier=identity_dossier,
                            enforce_closed_ledger_projection=has_pre_extraction,
                            hint_representation=str(
                                iteration.get("hint_representation")
                                or "ref-entity-relations.v1"
                            ),
                            procedure_inheritance_brief=procedure_inheritance_brief,
                        ))
                    except Exception as e:
                        logger.error(f"    ❌ Extraction failed: {e}")
                        encountered_errors = True
                        continue
                    if hints and str(hints).strip() and os.path.exists(hint_file):
                        try:
                            if os.path.getsize(hint_file) > 0:
                                successful_hint_writes += 1
                        except Exception:
                            successful_hint_writes += 1
        
        # Execute only sub-iterations present in the compiled artifact. Semantic
        # main-only profiles compile without this field.
        sub_iterations = (
            []
            if config.get("skip_extraction_sub_iterations", False)
            else iteration.get("sub_iterations", [])
        )
        only_sub_iterations = {
            str(value)
            for value in config.get("only_extraction_sub_iterations", [])
        }
        if only_sub_iterations:
            sub_iterations = [
                item
                for item in sub_iterations
                if str(item.get("iteration_number")) in only_sub_iterations
            ]
        for sub_iter in sub_iterations:
            sub_iter_num = sub_iter.get("iteration_number")
            sub_iter_name = sub_iter.get("name", f"iteration_{sub_iter_num}")
            enriches = sub_iter.get("enriches")
            
            logger.info(f"\n  🔄 Sub-iteration {sub_iter_num}: {sub_iter_name} (enriches iter {enriches})")
            
            sub_extraction_prompt_path = sub_iter.get("extraction_prompt")
            sub_model_key = sub_iter.get("model_config_key", f"iter{sub_iter_num}_enrichment")
            
            if not sub_extraction_prompt_path:
                logger.warning(f"    ⚠️  No extraction prompt for sub-iteration {sub_iter_num}")
                continue
            
            sub_extraction_prompt = load_prompt(sub_extraction_prompt_path)
            if not sub_extraction_prompt:
                continue
            sub_extraction_prompt = inject_global_context_brief(
                sub_extraction_prompt, global_context_brief
            )
            
            # Process each entity for enrichment
            for entity in top_entities:
                entity_label = entity.get("label", "")
                identity_dossier = dict(entity.get("identity_dossier") or {})
                safe = _safe_name(entity_label)
                
                logger.info(f"  📌 Entity: {entity_label}")
                
                # Get input/output paths from config
                sub_inputs = sub_iter.get("inputs", {})
                sub_outputs = sub_iter.get("outputs", {})
                
                # Resolve done marker path
                done_marker_template = sub_outputs.get("done_marker", f"mcp_run/iter{enriches}_{sub_iter_num}_done_{{entity_safe}}.marker")
                done_marker = resolve_file_path(done_marker_template, doi_hash, safe, data_dir)

                # Resolve base hints before the done-marker check so older completed
                # runs can mechanically recover a previously omitted file_path output.
                base_hints_template = sub_inputs.get("base_hints", f"mcp_run/iter{enriches}_hints_{{entity_safe}}.txt")
                base_hint_file = resolve_file_path(base_hints_template, doi_hash, safe, data_dir)

                force_sub_iteration = bool(
                    config.get("force_extraction_sub_iterations", False)
                )
                if os.path.exists(done_marker) and not force_sub_iteration:
                    declared_template = str(
                        sub_outputs.get("file_path") or ""
                    ).strip()
                    declared_file = (
                        resolve_file_path(
                            declared_template,
                            doi_hash,
                            safe,
                            data_dir,
                        )
                        if declared_template
                        else ""
                    )
                    if (
                        declared_file
                        and not os.path.exists(declared_file)
                        and os.path.exists(base_hint_file)
                    ):
                        with open(base_hint_file, "r", encoding="utf-8") as handle:
                            recovered_hints = handle.read()
                        _write_declared_sub_iteration_file(
                            sub_outputs=sub_outputs,
                            merged_hint_text=recovered_hints,
                            doi_hash=doi_hash,
                            entity_safe=safe,
                            data_dir=data_dir,
                        )
                        logger.info(
                            "    ✅ Recovered missing configured sub-iteration output "
                            "from merged hints: %s",
                            declared_file,
                        )
                    if not declared_file or os.path.exists(declared_file):
                        logger.info(f"    ⏭️  Sub-iteration {sub_iter_num} already completed")
                        continue
                
                if not os.path.exists(base_hint_file):
                    logger.warning(f"    ⚠️  Base hints file not found: {base_hint_file}")
                    continue
                
                base_hint_snapshot_file = _iter_base_hint_snapshot_path(
                    base_hint_file,
                    enriches=enriches,
                    entity_safe=safe,
                )
                refresh_snapshot = not os.path.exists(base_hint_snapshot_file)
                if not refresh_snapshot:
                    try:
                        refresh_snapshot = os.path.getmtime(base_hint_snapshot_file) < os.path.getmtime(base_hint_file)
                    except Exception:
                        refresh_snapshot = True

                # Keep a stable copy of the authoritative base iter hints so later
                # enrichment passes never read their own patch-style output as input.
                if refresh_snapshot:
                    try:
                        with open(base_hint_file, 'r', encoding='utf-8') as src:
                            base_hints = src.read()
                        with open(base_hint_snapshot_file, 'w', encoding='utf-8') as dst:
                            dst.write(base_hints)
                    except Exception as e:
                        logger.warning(f"    ⚠️  Failed to refresh base hint snapshot: {e}")
                        base_hints = ""
                else:
                    with open(base_hint_snapshot_file, 'r', encoding='utf-8') as f:
                        base_hints = f.read()

                if not base_hints.strip():
                    logger.warning(f"    ⚠️  Base hints snapshot is empty: {base_hint_snapshot_file}")
                    continue
                prior_registry, _ = _load_accumulated_prior_hints(
                    iterations=all_iterations,
                    current_iteration=int(enriches),
                    doi_hash=doi_hash,
                    entity_safe=safe,
                    data_dir=data_dir,
                )
                base_hints_payload: Any
                if base_hints.lstrip().startswith("SEMANTIC_HINTS_V1"):
                    base_hints_payload = {
                        "hint_representation": "semantic-text.v1",
                        "semantic_ledger": base_hints,
                    }
                else:
                    base_hints_payload = json.loads(base_hints)
                enrichment_registry = json.dumps(
                    {
                        "schema_version": "enrichment-identity-registry.v1",
                        "base_iteration_hints": base_hints_payload,
                        "prior_iteration_registry": (
                            json.loads(prior_registry) if prior_registry else None
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                enrichment_registry_payload = json.loads(enrichment_registry)
                
                # Resolve pre-extracted text path
                pre_extracted_template = sub_inputs.get("pre_extracted_text", f"llm_based_results/entity_text_{{entity_safe}}.txt")
                entity_text_path = resolve_file_path(pre_extracted_template, doi_hash, safe, data_dir)
                
                if os.path.exists(entity_text_path):
                    with open(entity_text_path, 'r', encoding='utf-8') as f:
                        source_text = f.read()
                else:
                    source_text = paper_content
                
                # Format enrichment prompt
                enrichment_prompt = bind_runtime_context(
                    sub_extraction_prompt,
                    doi_hash=doi_hash,
                    entity_label=entity_label,
                    entity_uri=str(entity.get("uri") or ""),
                    source_text=source_text,
                    iteration_input=base_hints,
                    accumulated_hints=enrichment_registry,
                    identity_dossier=identity_dossier,
                )
                kg_revision_feedback = str(
                    config.get("_kg_hint_revision_feedback") or ""
                ).strip()
                kg_revision_mode = bool(kg_revision_feedback)
                if kg_revision_feedback:
                    try:
                        revision_payload = json.loads(kg_revision_feedback)
                    except json.JSONDecodeError:
                        revision_payload = {
                            "schema_version": "kg-hint-contract-revision.v1",
                            "violations": [kg_revision_feedback],
                        }
                    typed_refs: dict[str, str] = {}

                    def _collect_typed_refs(value: object) -> None:
                        if isinstance(value, list):
                            for item in value:
                                _collect_typed_refs(item)
                            return
                        if not isinstance(value, dict):
                            return
                        ref = str(value.get("ref") or "").strip()
                        class_local = str(value.get("class") or "").strip()
                        if ref and class_local:
                            typed_refs[ref] = class_local
                        for item in value.values():
                            _collect_typed_refs(item)

                    _collect_typed_refs(enrichment_registry_payload)
                    correction_block = json.dumps(
                        {
                            "mode": "full-authoritative-replacement",
                            "reported_errors": revision_payload,
                            "typed_ref_registry": dict(sorted(typed_refs.items())),
                            "required_action": (
                                "Return the complete corrected hint payload. Preserve valid "
                                "content and remove every reported invalid relation. Resolve "
                                "endpoint types only from typed_ref_registry."
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    enrichment_prompt = (
                        "---- PIPELINE KG CONTRACT CORRECTION: BEGIN ----\n"
                        "This is a correction run, not an additive enrichment patch. The "
                        "reported invalid relations already occur in the current hints. "
                        "Omitting them from a delta is insufficient: return one complete "
                        "authoritative replacement payload in which they are absent.\n"
                        + correction_block
                        + "\n---- PIPELINE KG CONTRACT CORRECTION: END ----\n\n"
                        + enrichment_prompt.rstrip()
                        + "\n\n"
                        + "FINAL CORRECTION REMINDER: return the complete corrected payload, "
                        "not a patch. Every relation listed in the correction block must be "
                        "absent. Preserve every exact prior ref/class binding; a ref for one "
                        "class must never be substituted for a different class.\n"
                    )
                enrichment_prompt = _append_ref_entity_output_boundary(
                    enrichment_prompt
                )
                
                # Save enrichment prompt in organized subfolder
                prompts_dir = os.path.join(data_dir, doi_hash, "prompts", f"iter{sub_iter_num}_enrichment")
                os.makedirs(prompts_dir, exist_ok=True)
                prompt_file = os.path.join(prompts_dir, f"{safe}.md")
                try:
                    with open(prompt_file, 'w', encoding='utf-8') as f:
                        f.write(f"# Sub-iteration {sub_iter_num} Enrichment Prompt\n\n")
                        f.write(f"**Entity**: {entity_label}\n\n")
                        f.write(f"**Enriches**: Iteration {enriches}\n\n")
                        f.write(f"**Model**: {get_extraction_model(sub_model_key)}\n\n")
                        f.write("---\n\n")
                        f.write(enrichment_prompt)
                    logger.info(f"    💾 Saved enrichment prompt to: {prompt_file}")
                except Exception as e:
                    logger.warning(f"    ⚠️  Failed to save enrichment prompt: {e}")
                
                logger.info(f"    🔍 Running enrichment for sub-iteration {sub_iter_num}")
                
                # Run enrichment extraction with retry logic
                max_retries = 3
                enriched_content = None
                enrichment_validation_feedback = ""

                def _collect_registry_refs(value: object) -> set[str]:
                    if isinstance(value, list):
                        return set().union(
                            *(_collect_registry_refs(item) for item in value),
                            set(),
                        )
                    if not isinstance(value, dict):
                        return set()
                    refs = {
                        str(value.get("ref") or "").strip()
                        if str(value.get("ref") or "").strip()
                        else ""
                    }
                    refs.discard("")
                    for item in value.values():
                        refs.update(_collect_registry_refs(item))
                    return refs

                available_base_refs = sorted(
                    _collect_registry_refs(enrichment_registry_payload)
                )
                for attempt in range(max_retries):
                    try:
                        async def _run_enrichment():
                            model_name = get_extraction_model(sub_model_key)
                            llm = LLMCreator(
                                model=model_name,
                                model_config=ModelConfig(temperature=0, top_p=1.0),
                                remote_model=True,
                            ).setup_llm()
                            
                            effective_enrichment_prompt = enrichment_prompt
                            if enrichment_validation_feedback:
                                effective_enrichment_prompt += (
                                    "\n\nYour previous output was rejected by the structural "
                                    "materializability validator. Correct every issue below and "
                                    "return only the complete corrected JSON payload:\n"
                                    f"{enrichment_validation_feedback}"
                                )
                            result = await retry_async_on_transport(
                                lambda: llm.ainvoke(effective_enrichment_prompt),
                                logger=logger,
                                what=f"enrichment llm '{entity_label}'",
                            )
                            enriched_content = _normalize_llm_content(result)
                            return enriched_content
                        
                        logger.info(f"    Enrichment attempt {attempt + 1}/{max_retries}")
                        enriched_content = asyncio.run(_run_enrichment())
                        
                        if enriched_content and enriched_content.strip():
                            try:
                                valid_enrichment, enrichment_errors = validate_hint_payload(
                                    enriched_content,
                                    accumulated_hints=enrichment_registry,
                                    expected_schema="ref-entity-relations.v1",
                                    allowed_entity_iris={
                                        str(entity.get("uri") or "")
                                    },
                                )
                            except ValueError as exc:
                                valid_enrichment, enrichment_errors = False, [str(exc)]
                            if not valid_enrichment:
                                enrichment_validation_feedback = "\n".join(
                                    f"- {error}" for error in enrichment_errors
                                )
                                if available_base_refs:
                                    enrichment_validation_feedback += (
                                        "\n- Available exact refs from base hints: "
                                        + ", ".join(available_base_refs)
                                    )
                                raise ValueError(
                                    "Enrichment hint representation is not materializable:\n"
                                    f"{enrichment_validation_feedback}"
                                )
                            revision_relation_errors = (
                                _kg_revision_relation_errors(
                                    enriched_content,
                                    kg_revision_feedback,
                                )
                                if kg_revision_mode
                                else []
                            )
                            if revision_relation_errors:
                                enrichment_validation_feedback = "\n".join(
                                    f"- {error}"
                                    for error in revision_relation_errors
                                )
                                raise ValueError(
                                    "KG contract correction did not remove the reported "
                                    "relations:\n"
                                    f"{enrichment_validation_feedback}"
                                )
                            candidate_merged_hint_text = (
                                enriched_content
                                if kg_revision_mode
                                else _merge_structured_hint_text(
                                    base_hints,
                                    enriched_content,
                                    identity_keys=[
                                        str(key)
                                        for key in [
                                            "ref",
                                            *(
                                                sub_iter.get(
                                                    "merge_identity_keys", []
                                                )
                                                or []
                                            ),
                                        ]
                                        if str(key).strip()
                                    ],
                                )
                            )
                            if candidate_merged_hint_text is not None:
                                try:
                                    valid_merged, merged_errors = validate_hint_payload(
                                        candidate_merged_hint_text,
                                        accumulated_hints=prior_registry,
                                        expected_schema="ref-entity-relations.v1",
                                        allowed_entity_iris={
                                            str(entity.get("uri") or "")
                                        },
                                    )
                                except ValueError as exc:
                                    valid_merged, merged_errors = False, [str(exc)]
                                if not valid_merged:
                                    enrichment_validation_feedback = "\n".join(
                                        f"- {error}" for error in merged_errors
                                    )
                                    if available_base_refs:
                                        enrichment_validation_feedback += (
                                            "\n- Available exact refs from base hints: "
                                            + ", ".join(available_base_refs)
                                        )
                                    raise ValueError(
                                        "Merged enrichment hints are not materializable:\n"
                                        f"{enrichment_validation_feedback}"
                                    )
                            logger.info(f"    ✅ Enrichment succeeded on attempt {attempt + 1}")
                            break
                        else:
                            logger.warning(f"    ⚠️  Empty enrichment result on attempt {attempt + 1}")
                            if attempt < max_retries - 1:
                                wait_time = 5 * (attempt + 1)
                                logger.info(f"    Waiting {wait_time}s before retry...")
                                import time
                                time.sleep(wait_time)
                    except Exception as e:
                        logger.error(f"    ❌ Enrichment attempt {attempt + 1}/{max_retries} failed: {e}")
                        if attempt < max_retries - 1:
                            wait_time = 5 * (attempt + 1)
                            logger.info(f"    Waiting {wait_time}s before retry...")
                            import time
                            time.sleep(wait_time)
                        else:
                            raise RuntimeError(f"Enrichment failed after {max_retries} attempts. Last error: {e}")
                
                if not enriched_content or not enriched_content.strip():
                    logger.error(f"    ❌ Enrichment returned empty content after {max_retries} attempts")
                    continue
                
                # Get output hints file path (legacy configs often point this back to
                # the base iter hints file; in that case we preserve the base JSON and
                # store patch-style enrichments separately instead of overwriting it).
                output_hints_template = sub_outputs.get("hints_file", base_hints_template)
                output_hints_file = resolve_file_path(output_hints_template, doi_hash, safe, data_dir)
                patch_output_file = _sub_iteration_patch_output_path(
                    base_hint_file,
                    enriches=enriches,
                    sub_iter_num=sub_iter_num,
                    entity_safe=safe,
                )

                merged_hint_text = (
                    enriched_content
                    if kg_revision_mode
                    else _merge_structured_hint_text(
                        base_hints,
                        enriched_content,
                        identity_keys=[
                            str(key)
                            for key in [
                                "ref",
                                *(sub_iter.get("merge_identity_keys", []) or []),
                            ]
                            if str(key).strip()
                        ],
                    )
                )
                if merged_hint_text is not None:
                    try:
                        valid_merged, merged_errors = validate_hint_payload(
                            merged_hint_text,
                            accumulated_hints=prior_registry,
                            expected_schema="ref-entity-relations.v1",
                            allowed_entity_iris={str(entity.get("uri") or "")},
                        )
                    except ValueError as exc:
                        valid_merged, merged_errors = False, [str(exc)]
                    if not valid_merged:
                        raise ValueError(
                            "Merged enrichment hints are not materializable:\n- "
                            + "\n- ".join(merged_errors)
                        )
                wrote_hint_artifact = False

                if merged_hint_text is not None:
                    os.makedirs(os.path.dirname(output_hints_file), exist_ok=True)
                    with open(output_hints_file, 'w', encoding='utf-8') as f:
                        f.write(merged_hint_text)
                    declared_output_file = _write_declared_sub_iteration_file(
                        sub_outputs=sub_outputs,
                        merged_hint_text=merged_hint_text,
                        doi_hash=doi_hash,
                        entity_safe=safe,
                        data_dir=data_dir,
                    )
                    wrote_hint_artifact = True
                    logger.info(
                        "    ✅ Merged structured enrichment into iter%s hints for '%s'",
                        enriches,
                        entity_label,
                    )
                    if declared_output_file:
                        logger.info(
                            "    ✅ Wrote configured sub-iteration output: %s",
                            declared_output_file,
                        )
                else:
                    os.makedirs(os.path.dirname(patch_output_file), exist_ok=True)
                    with open(patch_output_file, 'w', encoding='utf-8') as f:
                        f.write(enriched_content)
                    wrote_hint_artifact = True
                    if output_hints_file == base_hint_file and _looks_like_patch_enrichment_output(enriched_content):
                        logger.info(
                            "    💾 Preserved authoritative iter%s hints and saved patch-style enrichment to: %s",
                            enriches,
                            os.path.basename(patch_output_file),
                        )
                    else:
                        logger.info(
                            "    💾 Saved non-mergeable enrichment output to: %s",
                            os.path.basename(patch_output_file),
                        )

                if wrote_hint_artifact:
                    successful_hint_writes += 1
                
                # Save response in responses folder for tracking
                responses_dir = os.path.join(data_dir, doi_hash, "responses", f"iter{sub_iter_num}_enrichment")
                os.makedirs(responses_dir, exist_ok=True)
                response_file = os.path.join(responses_dir, f"{safe}.md")
                with open(response_file, 'w', encoding='utf-8') as f:
                    f.write(f"# Sub-iteration {sub_iter_num} Enrichment Response\n\n")
                    f.write(f"**Entity**: {entity_label}\n\n")
                    f.write(f"**Enriches**: Iteration {enriches}\n\n")
                    f.write(f"**Model**: {get_extraction_model(sub_model_key)}\n\n")
                    f.write("---\n\n")
                    f.write(enriched_content)
                
                # Create done marker
                os.makedirs(os.path.dirname(done_marker), exist_ok=True)
                with open(done_marker, 'w', encoding='utf-8') as f:
                    f.write("done")
                
                logger.info(f"    ✅ Enrichment completed for sub-iteration {sub_iter_num}")
    
    successful_writes = config.get("_entity_first_successful_writes")
    if isinstance(successful_writes, list):
        successful_writes.append(successful_hint_writes)
        return not encountered_errors and successful_hint_writes > 0

    if encountered_errors or successful_hint_writes <= 0:
        logger.error("❌ Main ontology extractions produced no hints files; refusing to create completion marker")
        return False

    # Create completion marker
    _write_extraction_completion_marker(marker_file)
    
    logger.info(f"✅ Main Ontology Extractions completed for {doi_hash}")
    return True


if __name__ == "__main__":
    # Example usage for standalone testing
    if len(sys.argv) > 1:
        test_doi_hash = sys.argv[1]
        test_config = {
            "data_dir": "data"
        }
        print(f"Running main ontology extractions step for DOI hash: {test_doi_hash}")
        success = run_step(test_doi_hash, test_config)
        print(f"Main ontology extractions step {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.main_ontology_extractions.extract <doi_hash>")

