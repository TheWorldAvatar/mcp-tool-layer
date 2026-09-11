"""Run main-ontology extraction iterations from generated prompts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.extraction_runtime.artifact_root import load_iterations_config, load_prompt
from src.extraction_runtime.domain_binding import RuntimeDomain
from src.extraction_runtime.llm import invoke_text, write_text
from src.extraction_runtime.models_map import get_extraction_model
from models.locked_llm import LOCKED_EXTRACTION_MODEL
from src.extraction_runtime.steps.tbox_slim.run import runtime_extraction_model
from src.extraction_runtime.names import entity_artifact_name
from src.extraction_runtime.paper import load_paper_content_with_sources
from src.extraction_runtime.steps.main_extraction.inheritance import (
    inject_global_context_brief,
    render_global_context_brief,
    render_procedure_inheritance_brief,
    resolve_global_context,
    resolve_procedure_inheritance,
)
from src.extraction_runtime.steps.main_extraction.ledger import run_pre_extraction
from src.extraction_runtime.hint_payload import validate_hint_payload
from src.extraction_runtime.judges.closed_ledger import (
    _CLOSED_LEDGER_RETRY_SPAN_PRESERVATION,
    _run_operation_projection_panel,
    bounded_sidecar_path,
)
from src.extraction_runtime.judges.llm import setup_judge_llm
from src.extraction_runtime.judges.semantic import judge_extraction_semantics
from src.extraction_runtime.judges.tbox_contract import run_tbox_contract_critic
from src.extraction_runtime.locked_mechanisms import (
    assert_extraction_revision_locked,
    is_closed_ledger_source,
    is_semantic_hints,
    payload_retry_attempts,
    revision_feedback_fingerprint,
    same_revision_feedback,
    skip_extraction_judges,
    skip_if_already_revised,
    write_extraction_revision_receipt,
)
from src.extraction_runtime.steps.main_extraction.prompts import (
    SEMANTIC_HINT_REPRESENTATION,
    append_complete_inheritance_context,
    append_complete_target_passage,
    append_ref_entity_output_boundary,
    append_semantic_hint_output_boundary,
    bind_runtime_context,
    format_required_tool_feedback,
    inject_procedure_inheritance_brief,
    inject_required_tool_contract,
    merge_extraction_validation,
    required_executed_tool_groups,
    required_tool_activity_errors,
)
from src.extraction_runtime.tolerate import warn_continue
from src.extraction_runtime.steps.top_entity.identity import load_top_entities_json


def _hint_path(data_dir: str, doi_hash: str, iter_num: int, entity_safe: str) -> Path:
    return Path(data_dir) / doi_hash / "mcp_run" / f"iter{iter_num}_hints_{entity_safe}.txt"


def _load_accumulated_hints(
    *,
    iterations: list[dict],
    current_iteration: int,
    doi_hash: str,
    entity_safe: str,
    data_dir: str,
) -> str:
    chunks: list[str] = []
    for iteration in iterations:
        number = int(iteration.get("iteration_number") or -1)
        if number < 0 or number >= current_iteration:
            continue
        path = _hint_path(data_dir, doi_hash, number, entity_safe)
        if path.is_file() and path.stat().st_size > 0:
            chunks.append(f"# Iteration {number}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(chunks)


def _default_hint_representation(config: dict) -> str:
    domain = config.get("domain")
    if isinstance(domain, RuntimeDomain) and domain.workflow_profile == "complex":
        return SEMANTIC_HINT_REPRESENTATION
    return "ref-entity-relations.v1"


def _enrichment_tools(config: dict) -> tuple[str, list[str]]:
    domain = config.get("domain")
    if not isinstance(domain, RuntimeDomain) or not domain.allows_external_enrichment():
        return "", []
    return domain.mcp_capability("external_enrichment")


async def _run_extraction_llm_or_agent(
    *,
    prompt: str,
    model_name: str,
    use_agent: bool,
    mcp_set_name: str | None,
    mcp_tools: list[str] | None,
) -> tuple[str, dict]:
    if use_agent and mcp_tools and mcp_set_name:
        from models.ModelConfig import ModelConfig
        from src.extraction_runtime.agent.client import BaseAgent

        agent = BaseAgent(
            model_name=model_name,
            model_config=ModelConfig(temperature=0.0, top_p=1.0),
            mcp_set_name=mcp_set_name,
            mcp_tools=mcp_tools,
        )
        reply, metadata = await agent.run(
            prompt,
            recursion_limit=600,
            react_history_projection=False,
            react_argument_firewall=False,
        )
        return reply, dict(metadata or {})
    return invoke_text(prompt, model_name=model_name, top_p=1.0), {}


def _write_audit_warnings(hint_file: Path, warnings: list[dict]) -> None:
    if not warnings:
        return
    path = bounded_sidecar_path(
        str(hint_file.parent),
        hint_file.stem,
        ".audit_exhaustion_warning.json",
    )
    path.write_text(
        json.dumps(
            {"schema_version": "audit-exhaustion-warning.v1", "warnings": warnings},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _extraction_judges_run(*, semantic_mode: bool, closed_ledger_projection: bool) -> list[str]:
    judges = ["format"]
    if semantic_mode and closed_ledger_projection:
        judges.append("operation_projection")
    if not semantic_mode:
        judges.append("hint_payload")
        if closed_ledger_projection:
            judges.append("semantic_projection")
        judges.append("tbox_contract")
    return judges


async def _run_extraction_with_judges(
    *,
    prompt: str,
    prompt_template: str,
    model_name: str,
    use_agent: bool,
    mcp_set_name: str | None,
    mcp_tools: list[str] | None,
    representation: str,
    source_text: str,
    closed_ledger_revision: bool,
    hint_file: Path,
    extraction_validation: dict | None,
    entity_label: str,
    entity_uri: str,
    iter_num: int,
    accumulated_hints: str,
    skip_judges: bool | None = None,
) -> str:
    semantic_mode = representation == SEMANTIC_HINT_REPRESENTATION
    last_valid = ""
    last_feedback_kind = ""
    history: list[str] = []
    warnings: list[dict] = []
    required_groups = required_executed_tool_groups(extraction_validation)
    if skip_judges is None:
        skip_judges = skip_extraction_judges()
    one_shot = skip_judges
    max_retries = payload_retry_attempts()
    closed_ledger_projection = is_closed_ledger_source(source_text)
    previous_fingerprint: frozenset[str] | None = None
    used_attempts = 0
    _ = closed_ledger_revision

    def _stop_after_rejection(kind: str, rejection: str, *, stallable: bool) -> bool:
        """Keep a schema-valid draft when the judge repeats itself or the budget ends."""
        nonlocal last_feedback_kind, previous_fingerprint
        fingerprint = revision_feedback_fingerprint(rejection)
        exhausted = attempt >= max_retries - 1
        stalled = stallable and same_revision_feedback(fingerprint, previous_fingerprint)
        if exhausted or stalled:
            warnings.append(
                {
                    "kind": f"{kind}_exhausted" if exhausted else f"{kind}_stalled",
                    "message": rejection,
                    "candidate_attempt": attempt + 1,
                }
            )
            warn_continue(
                kind.replace("_", " "),
                (
                    "audit budget exhausted; keeping last schema-valid candidate"
                    if exhausted
                    else "repeated the same judge rejection; keeping last schema-valid candidate"
                ),
            )
            return True
        history.append(rejection)
        last_feedback_kind = kind
        previous_fingerprint = fingerprint
        return False

    for attempt in range(max_retries):
        used_attempts = attempt + 1
        effective = prompt
        if history:
            if last_feedback_kind == "required_tools":
                effective = prompt + "\n\n" + history[-1] + "\n"
            else:
                effective = (
                    prompt
                    + "\n\nVALIDATION FEEDBACK FROM ALL PREVIOUS ATTEMPTS "
                    "(oldest to newest):\n"
                    + "\n\n".join(
                        f"ATTEMPT {index} FAILURE:\n{feedback}"
                        for index, feedback in enumerate(history, start=1)
                    )
                    + "\nReturn corrected extraction hints only. Fix every recorded "
                    "issue and preserve all source-supported facts accepted by earlier "
                    "feedback; do not regress an earlier correction.\n"
                    + _CLOSED_LEDGER_RETRY_SPAN_PRESERVATION
                    + "\n"
                )
        hints, metadata = await _run_extraction_llm_or_agent(
            prompt=effective,
            model_name=model_name,
            use_agent=use_agent,
            mcp_set_name=mcp_set_name,
            mcp_tools=mcp_tools,
        )
        if not str(hints or "").strip():
            print(
                f"    [WARN] Empty extraction output "
                f"(attempt {attempt + 1}/{max_retries}); retrying"
            )
            history.append("LLM returned empty extraction output")
            last_feedback_kind = "empty"
            continue
        if use_agent and required_groups and not one_shot:
            tool_errors = required_tool_activity_errors(metadata, extraction_validation)
            if tool_errors:
                activity = (metadata or {}).get("tool_activity") or {}
                feedback = format_required_tool_feedback(
                    tool_errors,
                    groups=required_groups,
                    executed_tool_names=list(activity.get("executed_tool_names") or []),
                )
                last_valid = hints or last_valid
                if _stop_after_rejection("required_tools", feedback, stallable=False):
                    break
                continue
        if semantic_mode:
            if not is_semantic_hints(hints):
                history.append(
                    "Semantic hints must begin with the exact marker SEMANTIC_HINTS_V1"
                )
                last_feedback_kind = "semantic_header"
                continue
            last_valid = hints
            if one_shot:
                write_extraction_revision_receipt(
                    hint_file,
                    attempts=attempt + 1,
                    max_attempts=max_retries,
                    accepted=True,
                    judges=["one_shot"],
                )
                return hints
            if closed_ledger_projection:
                audit_llm = setup_judge_llm(model_name)
                try:
                    report = await _run_operation_projection_panel(
                        audit_llm=audit_llm,
                        original_prompt=prompt,
                        source_text=source_text,
                        candidate_text=hints,
                    )
                except Exception as exc:
                    rejection = f"Closed-ledger operation projection failed: {exc}"
                    if _stop_after_rejection(
                        "operation_projection", rejection, stallable=True
                    ):
                        break
                    continue
                sidecar = hint_file.with_suffix(
                    hint_file.suffix + ".operation_projection.json"
                )
                sidecar.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if not bool((report.get("acceptance") or {}).get("accepted")):
                    rejection = (
                        "Closed-ledger operation projection rejected the extraction:\n- "
                        + "\n- ".join(report.get("feedback") or [])
                    )
                    if _stop_after_rejection(
                        "operation_projection", rejection, stallable=True
                    ):
                        break
                    continue
        else:
            try:
                ok_payload, hint_errors = validate_hint_payload(
                    hints,
                    accumulated_hints=accumulated_hints,
                    expected_schema="ref-entity-relations.v1",
                    allowed_entity_iris={entity_uri} if entity_uri else None,
                )
            except ValueError as exc:
                ok_payload, hint_errors = False, [str(exc)]
            if not ok_payload:
                print(
                    f"    [WARN] Extraction payload is not materializable "
                    f"(attempt {attempt + 1}/{max_retries}); retrying"
                )
                history.append(
                    "Extraction hint representation is not materializable:\n- "
                    + "\n- ".join(hint_errors)
                )
                last_feedback_kind = "hint_payload"
                continue
            last_valid = hints
            if one_shot:
                write_extraction_revision_receipt(
                    hint_file,
                    attempts=attempt + 1,
                    max_attempts=max_retries,
                    accepted=True,
                    judges=["one_shot"],
                )
                return hints
            if closed_ledger_projection:
                try:
                    ledger_reference: object = json.loads(source_text)
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
                    extracted_content=hints,
                    reference_content=ledger_reference,
                    models=[model_name],
                    prior_feedback=history,
                )
                if not bool((projection_report.get("acceptance") or {}).get("accepted")):
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
                    if _stop_after_rejection(
                        "semantic_projection", rejection, stallable=True
                    ):
                        break
                    continue
            critic_feedback, critic_warnings = await run_tbox_contract_critic(
                model_name=model_name,
                original_prompt=prompt,
                source_text=source_text,
                candidate_text=hints,
            )
            warnings.extend(critic_warnings)
            if critic_feedback:
                rejection = (
                    "LLM T-Box contract critic rejected the draft:\n- "
                    + "\n- ".join(critic_feedback)
                )
                if _stop_after_rejection("tbox_contract", rejection, stallable=True):
                    break
                continue
        write_extraction_revision_receipt(
            hint_file,
            attempts=attempt + 1,
            max_attempts=max_retries,
            accepted=not warnings,
            judges=_extraction_judges_run(
                semantic_mode=semantic_mode,
                closed_ledger_projection=closed_ledger_projection,
            ),
        )
        _write_audit_warnings(hint_file, warnings)
        return hints
    if last_valid.strip():
        write_extraction_revision_receipt(
            hint_file,
            attempts=used_attempts or max_retries,
            max_attempts=max_retries,
            accepted=False,
            judges=_extraction_judges_run(
                semantic_mode=semantic_mode,
                closed_ledger_projection=closed_ledger_projection,
            ),
        )
    _write_audit_warnings(hint_file, warnings)
    return last_valid


def _run_one_entity(
    doi_hash: str,
    config: dict,
    entity: dict,
    iterations: list[dict],
    all_iterations: list[dict],
    paper_content: str,
    global_context_brief: str,
) -> int:
    ontology_name = str(config.get("ontology_name") or "")
    entity_label = str(entity.get("label") or "")
    entity_uri = str(entity.get("uri") or "")
    identity_dossier = dict(entity.get("identity_dossier") or {})
    safe = entity_artifact_name(entity_label)
    data_dir = config.get("data_dir", "data")
    writes = 0
    enrichment_set, enrichment_tools = _enrichment_tools(config)

    for iteration in iterations:
        iter_num = int(iteration.get("iteration_number") or 0)
        if iter_num <= 1:
            continue
        if config.get(f"skip_iter{iter_num}_extraction"):
            raise ValueError(
                f"extraction revision is locked on; skip_iter{iter_num}_extraction is forbidden"
            )
        hint_file = _hint_path(data_dir, doi_hash, iter_num, safe)
        if skip_if_already_revised("extraction hints", hint_file):
            writes += 1
            continue
        prompt_path = str(iteration.get("extraction_prompt") or "")
        if not prompt_path:
            prompt_path = f"prompts/{ontology_name}/EXTRACTION_ITER_{iter_num}.md"
        prompt_template = load_prompt(prompt_path)
        if not prompt_template:
            print(f"    [WARN] Empty extraction prompt: {prompt_path}; skipping this iteration")
            continue
        prompt_template = inject_global_context_brief(prompt_template, global_context_brief)
        accumulated = _load_accumulated_hints(
            iterations=all_iterations,
            current_iteration=iter_num,
            doi_hash=doi_hash,
            entity_safe=safe,
            data_dir=data_dir,
        )
        source_text = paper_content
        inheritance_brief = ""
        has_pre = bool(
            iteration.get("has_pre_extraction")
            or iteration.get("requires_pre_extraction")
            or iteration.get("pre_extraction_prompt")
        )
        if has_pre:
            pre_path = str(iteration.get("pre_extraction_prompt") or "")
            if not pre_path:
                pre_path = f"prompts/{ontology_name}/PRE_EXTRACTION_ITER_{iter_num}.md"
            pre_prompt = load_prompt(pre_path)
            if not pre_prompt:
                print(
                    f"    [WARN] Empty pre-extraction prompt: {pre_path}; "
                    "continuing without a closed ledger"
                )
            else:
                pre_prompt = inject_global_context_brief(pre_prompt, global_context_brief)
                try:
                    inheritance = resolve_procedure_inheritance(
                        source_text=paper_content,
                        target_procedure_ref=entity_uri or f"label:{entity_label}",
                        target_procedure_label=entity_label,
                        tbox_contract=pre_prompt,
                        model=get_extraction_model(
                            iteration.get("pre_extraction_model_key") or "advanced_model",
                            default=LOCKED_EXTRACTION_MODEL,
                        ),
                        target_identity_dossier=identity_dossier,
                        top_entity_manifest=load_top_entities_json(doi_hash, data_dir),
                        cache_path=Path(data_dir)
                        / doi_hash
                        / "procedure_inheritance"
                        / f"{safe}.json",
                    )
                    inheritance_brief = render_procedure_inheritance_brief(inheritance)
                except Exception as exc:
                    print(f"    [WARN] Procedure inheritance failed: {exc}")
                source_text = run_pre_extraction(
                    doi_hash=doi_hash,
                    entity_label=entity_label,
                    entity_uri=entity_uri,
                    paper_content=paper_content,
                    prompt_template=pre_prompt,
                    model_key=str(
                        iteration.get("pre_extraction_model_key") or "iter3_pre_extraction"
                    ),
                    data_dir=data_dir,
                    identity_dossier=identity_dossier,
                    accumulated_hints=accumulated,
                    procedure_inheritance_brief=inheritance_brief,
                    iter_num=iter_num,
                    skip_judges=skip_extraction_judges(config),
                )
                if not is_closed_ledger_source(source_text):
                    warn_continue(
                        "pre-extraction",
                        "closed-ledger revision did not produce a valid ledger; "
                        "skipping main extraction for this entity",
                    )
                    continue

        representation = str(
            iteration.get("hint_representation") or _default_hint_representation(config)
        )
        use_agent = bool(iteration.get("use_agent")) and bool(enrichment_tools)
        mcp_set, mcp_tools = (enrichment_set, enrichment_tools) if use_agent else (None, None)
        validation = merge_extraction_validation(
            iteration.get("extraction_validation"),
            mcp_set,
        )
        prompt = bind_runtime_context(
            prompt_template,
            doi_hash=doi_hash,
            entity_label=entity_label,
            entity_uri=entity_uri,
            source_text=source_text,
            accumulated_hints=accumulated,
            identity_dossier=identity_dossier,
        )
        prompt = inject_procedure_inheritance_brief(prompt, inheritance_brief)
        prompt = (
            append_semantic_hint_output_boundary(prompt)
            if representation == SEMANTIC_HINT_REPRESENTATION
            else append_ref_entity_output_boundary(prompt)
        )
        prompt = append_complete_target_passage(prompt, source_text)
        prompt = append_complete_inheritance_context(prompt, inheritance_brief)
        prompt = inject_required_tool_contract(prompt, validation, use_agent=use_agent)
        prompt_dir = Path(data_dir) / doi_hash / "prompts" / f"iter{iter_num}_extraction"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / f"{safe}.md").write_text(prompt, encoding="utf-8")
        model_name = runtime_extraction_model(config)
        hints = asyncio.run(
            _run_extraction_with_judges(
                prompt=prompt,
                prompt_template=prompt_template,
                model_name=model_name,
                use_agent=use_agent,
                mcp_set_name=mcp_set,
                mcp_tools=mcp_tools,
                representation=representation,
                source_text=source_text,
                closed_ledger_revision=has_pre,
                hint_file=hint_file,
                extraction_validation=validation,
                entity_label=entity_label,
                entity_uri=entity_uri,
                iter_num=iter_num,
                accumulated_hints=accumulated,
                skip_judges=skip_extraction_judges(config),
            )
        )
        if not str(hints or "").strip():
            print(
                f"    [WARN] Empty hints for {entity_label} iter {iter_num}; "
                "continuing remaining iterations"
            )
            continue
        write_text(str(hint_file), hints)
        writes += 1
        print(f"    [OK] Wrote {hint_file.name}")
    return writes


def run_step(doi_hash: str, config: dict) -> bool:
    assert_extraction_revision_locked(config)
    data_dir = config.get("data_dir", "data")
    ontology_name = str(config.get("ontology_name") or "").strip()
    print(f">> Main Ontology Extractions: {doi_hash}")
    iterations_config = load_iterations_config(ontology_name)
    iterations = list(iterations_config.get("iterations") or [])
    if not iterations:
        print("[WARN] iterations.json is missing or empty; continuing")
        return True
    top_entities = load_top_entities_json(doi_hash, data_dir)
    if not top_entities:
        print("[WARN] No top entities found; continuing")
        return True
    selected = config.get("only_entity_safe") or config.get("_entity_first_entity_safe")
    marker = Path(data_dir) / doi_hash / ".main_ontology_extractions_done"
    if not selected:
        all_ok = True
        writes = 0
        for entity in top_entities:
            label = str(entity.get("label") or "")
            child = dict(config)
            child["_entity_first_entity_safe"] = entity_artifact_name(label)
            try:
                ok = run_step(doi_hash, child)
            except Exception as exc:
                print(f"    [WARN] Entity {label!r} failed; continuing remaining entities: {exc}")
                ok = False
            if ok:
                writes += 1
            else:
                all_ok = False
        if writes <= 0:
            print(f"[WARN] Main Ontology Extractions wrote no hints for {doi_hash}")
            return True
        if all_ok:
            marker.write_text("completed\n", encoding="utf-8")
            print(f"[OK] Main Ontology Extractions completed for {doi_hash}")
            return True
        print(
            f"[WARN] Main Ontology Extractions partial for {doi_hash}: "
            f"{writes}/{len(top_entities)} entities wrote hints; continuing pipeline"
        )
        return True

    paper_content, _sources = load_paper_content_with_sources(doi_hash, data_dir)
    if not paper_content:
        print("[WARN] Failed to load paper content; continuing")
        return True
    global_brief = ""
    try:
        domain = config.get("domain")
        tbox = ""
        if isinstance(domain, RuntimeDomain) and domain.config.primary_tbox.is_file():
            tbox = domain.config.primary_tbox.read_text(encoding="utf-8")
        resolution = resolve_global_context(
            source_text=paper_content,
            tbox_contract=tbox,
            model=get_extraction_model("advanced_model", default=LOCKED_EXTRACTION_MODEL),
            cache_path=Path(data_dir) / doi_hash / "global_procedure_context.json",
        )
        global_brief = render_global_context_brief(resolution)
    except Exception as exc:
        print(f"[WARN] Global procedure context unresolved: {exc}")

    entities = [
        entity
        for entity in top_entities
        if entity_artifact_name(entity.get("label", "")) == selected
    ]
    if not entities:
        print(f"[WARN] Selected entity not found: {selected}; continuing")
        return True
    writes = _run_one_entity(
        doi_hash,
        config,
        entities[0],
        iterations,
        iterations,
        paper_content,
        global_brief,
    )
    return writes > 0
