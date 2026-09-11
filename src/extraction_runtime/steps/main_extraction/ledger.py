"""Closed-ledger PRE extraction for the ordered workflow slot."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.extraction_runtime.judges.closed_ledger import (
    _CLOSED_LEDGER_RETRY_SPAN_PRESERVATION,
    _MIN_EXTRACTION_CHARS,
    _closed_ledger_feedback_fingerprint,
    _format_closed_ledger_feedback_history,
    _prune_untyped_closed_ledger_evidence,
    _run_closed_ledger_audit_panel,
    _run_type_selection_judge,
    _should_stop_closed_ledger_retry,
    _validate_closed_ledger_shape,
    bounded_sidecar_path,
)
from src.extraction_runtime.judges.llm import setup_judge_llm
from src.extraction_runtime.llm import response_text, strip_code_fences
from src.extraction_runtime.models_map import get_extraction_model
from models.locked_llm import LOCKED_EXTRACTION_MODEL
from src.extraction_runtime.names import entity_artifact_name
from src.extraction_runtime.steps.main_extraction.prompts import (
    append_closed_ledger_output_boundary,
    append_complete_inheritance_context,
    append_complete_target_passage,
    bind_runtime_context,
    inject_procedure_inheritance_brief,
)
from src.extraction_runtime.locked_mechanisms import (
    CLOSED_LEDGER_AUDIT_VOTES,
    CLOSED_LEDGER_FORMAT_RETRIES,
    PRE_CLOSED_LEDGER_REVISION_ATTEMPTS,
    is_closed_ledger_source,
    skip_extraction_judges,
    skip_if_already_revised,
    write_extraction_revision_receipt,
)
from src.extraction_runtime.tolerate import warn_continue


def validate_closed_ledger_shape(candidate_text: str) -> list[str]:
    return _validate_closed_ledger_shape(candidate_text, "")


def salvage_closed_ledger(candidate_text: str) -> str | None:
    """Keep typed evidence rows so an imperfect ledger can still proceed."""
    try:
        payload = json.loads(strip_code_fences(candidate_text))
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), list):
        return None
    kept = []
    for row in payload["evidence"]:
        if not isinstance(row, dict):
            continue
        if not str(row.get("verbatim_quote") or "").strip():
            continue
        types = row.get("candidate_types")
        if not isinstance(types, list) or not any(str(value or "").strip() for value in types):
            continue
        cleaned = dict(row)
        if not isinstance(cleaned.get("candidate_properties"), dict):
            cleaned["candidate_properties"] = {}
        kept.append(cleaned)
    if not kept:
        return None
    payload["evidence"] = kept
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _persist_ledger(output: Path, text: str) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return text


def _write_warning(responses_dir: Path, stem: str, payload: dict) -> None:
    warning = bounded_sidecar_path(str(responses_dir), stem, ".closed_ledger_warning.json")
    warning.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run_pre_extraction_with_judges(
    *,
    prompt: str,
    paper_content: str,
    model_name: str,
    entity_label: str,
    safe_stem: str,
    closed_ledger_enabled: bool,
    closed_ledger_cfg: dict,
    procedure_inheritance_brief: str,
    responses_dir: Path,
    max_retries: int,
    skip_judges: bool | None = None,
) -> tuple[str, int]:
    closed_ledger_enabled = True
    if skip_judges is None:
        skip_judges = skip_extraction_judges()
    one_shot = skip_judges
    llm = setup_judge_llm(model_name)
    if one_shot:
        history: list[str] = []
        last_draft = ""
        used_attempts = 0
        for attempt in range(max(1, max_retries)):
            used_attempts = attempt + 1
            effective = prompt
            if history:
                effective = (
                    prompt
                    + "\n\nCLOSED-LEDGER VALIDATION FEEDBACK FROM ALL PREVIOUS ATTEMPTS "
                    "(every item remains mandatory):\n"
                    + _format_closed_ledger_feedback_history(history)
                    + "\nRebuild and return the complete corrected ledger JSON only. "
                    "Preserve every earlier correction while fixing the latest failure.\n"
                )
            candidate = response_text(await llm.ainvoke(effective))
            last_draft = candidate
            if (
                not str(candidate or "").strip()
                or len(candidate.strip()) < _MIN_EXTRACTION_CHARS
            ):
                print(
                    f"    [WARN] Empty pre-extraction output "
                    f"(attempt {used_attempts}/{max_retries}); retrying"
                )
                history.append(
                    f"LLM returned empty or too-short content for pre-extraction of '{entity_label}'"
                )
                continue
            candidate = _prune_untyped_closed_ledger_evidence(candidate)
            shape_errors = _validate_closed_ledger_shape(candidate, paper_content)
            if shape_errors:
                salvaged = salvage_closed_ledger(candidate)
                if salvaged and not _validate_closed_ledger_shape(salvaged, paper_content):
                    return salvaged, used_attempts
                print(
                    f"    [WARN] Pre-extraction ledger shape invalid "
                    f"(attempt {used_attempts}/{max_retries}); retrying"
                )
                history.append(
                    "Closed-ledger structural validation failed:\n- "
                    + "\n- ".join(shape_errors[:20])
                )
                continue
            return candidate, used_attempts
        salvaged = salvage_closed_ledger(last_draft)
        return (salvaged or last_draft or ""), used_attempts
    audit_llm = setup_judge_llm(model_name)
    format_model = str(closed_ledger_cfg.get("audit_format_model") or model_name).strip()
    format_llm = setup_judge_llm(format_model)
    vote_count = CLOSED_LEDGER_AUDIT_VOTES
    format_retries = CLOSED_LEDGER_FORMAT_RETRIES
    fail_open_format = True
    nonblocking = True
    history: list[str] = []
    previous_fingerprint: frozenset[str] | None = None
    last_draft = ""
    used_attempts = 0
    for attempt in range(max(1, max_retries)):
        used_attempts = attempt + 1
        warning: dict | None = None
        effective = prompt
        if history:
            effective = (
                prompt
                + "\n\nCLOSED-LEDGER VALIDATION FEEDBACK FROM ALL PREVIOUS ATTEMPTS "
                "(every item remains mandatory):\n"
                + _format_closed_ledger_feedback_history(history)
                + "\nRebuild and return the complete corrected ledger JSON only. "
                "Preserve every earlier correction while fixing the latest failure. "
                f"{_CLOSED_LEDGER_RETRY_SPAN_PRESERVATION} "
                "Do not merely explain the corrections.\n"
            )
            effective = append_complete_target_passage(effective, last_draft)
        candidate = response_text(await llm.ainvoke(effective))
        last_draft = candidate
        if not str(candidate or "").strip():
            history.append(f"LLM returned empty content for pre-extraction of '{entity_label}'")
            continue
        if len(candidate.strip()) < _MIN_EXTRACTION_CHARS:
            history.append(
                f"LLM returned suspiciously short content ({len(candidate)} chars) "
                f"for pre-extraction of '{entity_label}'"
            )
            continue
        if closed_ledger_enabled:
            candidate = _prune_untyped_closed_ledger_evidence(candidate)
            shape_errors = _validate_closed_ledger_shape(candidate, paper_content)
            if shape_errors:
                history.append(
                    "Closed-ledger structural validation failed:\n- "
                    + "\n- ".join(shape_errors[:20])
                )
                continue
            audit_feedback: list[str] = []
            try:
                audit_feedback = await _run_closed_ledger_audit_panel(
                    audit_llm=audit_llm,
                    format_llm=format_llm,
                    original_prompt=prompt,
                    source_text=paper_content,
                    candidate_text=candidate,
                    prior_feedback=history,
                    vote_count=vote_count,
                    format_retries=format_retries,
                    inheritance_brief=procedure_inheritance_brief,
                    format_trace_dir=str(responses_dir),
                    format_trace_stem=f"{safe_stem}.candidate_{attempt + 1}",
                )
            except ValueError as exc:
                if not fail_open_format:
                    history.append(str(exc))
                    continue
                warning = {
                    "kind": "non_type_audit_format_exhausted",
                    "message": str(exc),
                    "candidate_attempt": attempt + 1,
                    "format_retries": format_retries,
                }
                warn_continue("closed-ledger audit format", str(exc))
            try:
                type_feedback = await _run_type_selection_judge(
                    audit_llm=audit_llm,
                    original_prompt=prompt,
                    source_text=paper_content,
                    candidate_text=candidate,
                    format_retries=format_retries,
                    trace_dir=str(responses_dir),
                    trace_stem=f"{safe_stem}.candidate_{attempt + 1}",
                )
                audit_feedback = list(dict.fromkeys([*audit_feedback, *type_feedback]))
            except ValueError as exc:
                if not fail_open_format:
                    history.append(str(exc))
                    continue
                warning = {
                    "kind": "type_selection_audit_format_exhausted",
                    "message": str(exc),
                    "candidate_attempt": attempt + 1,
                    "actual_attempts_per_atom": format_retries,
                }
                warn_continue("type-selection audit format", str(exc))
            if audit_feedback:
                rejection = (
                    "Closed-ledger semantic audit rejected the draft:\n- "
                    + "\n- ".join(audit_feedback)
                )
                fingerprint = _closed_ledger_feedback_fingerprint(audit_feedback)
                stalled = _should_stop_closed_ledger_retry(
                    current_fingerprint=fingerprint,
                    previous_fingerprint=previous_fingerprint,
                    attempt=attempt,
                    max_retries=max_retries,
                    nonblocking=nonblocking,
                )
                if stalled:
                    warning = {
                        "kind": (
                            "semantic_audit_exhausted"
                            if attempt >= max_retries - 1
                            else "semantic_audit_stalled"
                        ),
                        "message": rejection,
                        "candidate_attempt": attempt + 1,
                        "semantic_attempt_budget": max_retries,
                        "feedback_fingerprint": sorted(fingerprint),
                        "feedback_history": [*history, rejection],
                    }
                    warn_continue(
                        "closed-ledger semantic audit",
                        "keeping structurally valid ledger",
                    )
                    _write_warning(responses_dir, safe_stem, warning)
                    return candidate, used_attempts
                previous_fingerprint = fingerprint
                history.append(rejection)
                continue
            if warning is not None:
                _write_warning(responses_dir, safe_stem, warning)
        return candidate, used_attempts
    salvaged = salvage_closed_ledger(last_draft)
    if salvaged:
        warn_continue("pre-extraction", "used salvaged typed evidence after retries")
        return salvaged, used_attempts
    if last_draft.strip():
        warn_continue("pre-extraction", "judges failed; keeping last draft")
        return last_draft, used_attempts
    warn_continue("pre-extraction", "judges failed after locked revision budget")
    return "", used_attempts


def run_pre_extraction(
    *,
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    paper_content: str,
    prompt_template: str,
    model_key: str,
    data_dir: str,
    identity_dossier: dict | None = None,
    accumulated_hints: str = "",
    procedure_inheritance_brief: str = "",
    iter_num: int = 3,
    skip_judges: bool | None = None,
) -> str:
    safe = entity_artifact_name(entity_label)
    output = Path(data_dir) / doi_hash / "pre_extraction" / f"entity_text_{safe}.txt"
    if skip_if_already_revised("pre-extraction ledger", output):
        return output.read_text(encoding="utf-8")

    prompt = bind_runtime_context(
        prompt_template,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        source_text=paper_content,
        accumulated_hints=accumulated_hints,
        identity_dossier=identity_dossier,
    )
    prompt = inject_procedure_inheritance_brief(prompt, procedure_inheritance_brief)
    prompt = append_closed_ledger_output_boundary(prompt, procedure_inheritance_brief)
    prompt = append_complete_inheritance_context(prompt, procedure_inheritance_brief)
    prompt_dir = Path(data_dir) / doi_hash / "prompts" / f"iter{iter_num}_pre_extraction"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / f"{safe}.md").write_text(prompt, encoding="utf-8")
    model = get_extraction_model(model_key, default=LOCKED_EXTRACTION_MODEL)
    responses_dir = Path(data_dir) / doi_hash / "responses" / f"iter{iter_num}_pre_extraction"
    responses_dir.mkdir(parents=True, exist_ok=True)
    if skip_judges is None:
        skip_judges = skip_extraction_judges()
    one_shot = skip_judges
    candidate, used_attempts = asyncio.run(
        _run_pre_extraction_with_judges(
            prompt=prompt,
            paper_content=paper_content,
            model_name=model,
            entity_label=entity_label,
            safe_stem=safe,
            closed_ledger_enabled=True,
            closed_ledger_cfg={},
            procedure_inheritance_brief=procedure_inheritance_brief,
            responses_dir=responses_dir,
            max_retries=PRE_CLOSED_LEDGER_REVISION_ATTEMPTS,
            skip_judges=skip_judges,
        )
    )
    if not str(candidate or "").strip() or not is_closed_ledger_source(candidate):
        warn_continue("pre-extraction", "closed-ledger revision did not yield a valid ledger")
        return candidate or ""
    (responses_dir / f"{safe}.md").write_text(candidate, encoding="utf-8")
    persisted = _persist_ledger(output, candidate)
    write_extraction_revision_receipt(
        output,
        attempts=used_attempts,
        max_attempts=PRE_CLOSED_LEDGER_REVISION_ATTEMPTS,
        accepted=True,
        judges=["one_shot"] if one_shot else [
            "closed_ledger_shape",
            "closed_ledger_audit",
            "type_selection",
        ],
    )
    return persisted
