"""Extract the generated top class members from paper markdown."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.extraction_runtime.artifact_root import load_prompt, prompts_dir
from src.extraction_runtime.judges.llm import setup_judge_llm
from src.extraction_runtime.judges.semantic import judge_extraction_semantics
from src.extraction_runtime.judges.top_entity import (
    _append_conservative_top_class_gate,
    _format_top_entity_feedback_history,
    _revise_top_entities_against_tbox,
    _run_top_class_membership_judge,
    _run_top_class_omission_judge,
    _top_entity_semantic_audit_errors,
)
from src.extraction_runtime.llm import response_text
from src.extraction_runtime.steps.tbox_slim.run import runtime_extraction_model
from src.extraction_runtime.paper import load_paper_content
from src.extraction_runtime.steps.top_entity.identity import (
    persist_and_validate_top_class_selection,
    resolve_top_class,
    top_entity_line_prefixes,
)
from src.extraction_runtime.locked_mechanisms import (
    TOP_ENTITY_REVISION_ATTEMPTS,
    assert_extraction_revision_locked,
    extraction_revision_complete,
    skip_extraction_judges,
    write_extraction_revision_receipt,
)
from src.extraction_runtime.tolerate import warn_continue
from src.extraction_runtime.steps.top_entity.membership import (
    bind_paper_content,
    keep_valid_top_entity_lines,
    normalize_top_entity_output,
    validate_top_entity_lines,
)


def _iter1_prompt(ontology_name: str) -> str:
    directory = prompts_dir(ontology_name)
    for name in ("EXTRACTION_ITER_1.md", "extraction_iter_1.md"):
        path = directory / name
        if path.is_file():
            return load_prompt(path)
    raise FileNotFoundError(f"Iteration 1 extraction prompt not found under {directory}")


async def _extract_top_entities_with_judges(
    *,
    prompt: str,
    paper: str,
    prefixes: list[str],
    class_iri: str,
    class_local: str,
    class_comment: str,
    model_name: str,
    doi_dir: Path,
    skip_judges: bool = False,
) -> str:
    llm = setup_judge_llm(model_name)
    if skip_judges:
        last_content = ""
        feedback_history: list[str] = []
        for attempt in range(TOP_ENTITY_REVISION_ATTEMPTS):
            effective = prompt + _format_top_entity_feedback_history(feedback_history)
            raw = response_text(await llm.ainvoke(effective))
            content = normalize_top_entity_output(raw, line_prefixes=prefixes)
            ok_lines, line_errors = validate_top_entity_lines(content, list(prefixes))
            if ok_lines:
                return content
            salvaged = keep_valid_top_entity_lines(content, prefixes)
            if salvaged.strip():
                return salvaged
            last_content = content
            print(
                f"    [WARN] Empty top-entity listing "
                f"(attempt {attempt + 1}/{TOP_ENTITY_REVISION_ATTEMPTS}); retrying"
            )
            feedback_history.append(
                "Top-entity listing was empty or invalid:\n- "
                + "\n- ".join(line_errors or ["no valid top-entity lines"])
            )
        return last_content
    membership_llm = setup_judge_llm(model_name)
    omission_llm = setup_judge_llm(model_name)
    feedback_history: list[str] = []
    last_valid = ""
    for attempt in range(TOP_ENTITY_REVISION_ATTEMPTS):
        effective = prompt + _format_top_entity_feedback_history(feedback_history)
        raw = response_text(await llm.ainvoke(effective))
        content = normalize_top_entity_output(raw, line_prefixes=prefixes)
        content = await _revise_top_entities_against_tbox(
            llm=llm,
            candidate_text=content,
            source_text=paper,
            top_class_iri=class_iri,
            top_class_comment=class_comment,
            line_prefixes=list(prefixes),
            identifier_code_regex=None,
        )
        content, omission_report = await _run_top_class_omission_judge(
            llm=omission_llm,
            candidate_text=content,
            source_text=paper,
            top_class_iri=class_iri,
            top_class_comment=class_comment,
            line_prefixes=list(prefixes),
        )
        omission_report["model"] = model_name
        (doi_dir / f"top_entities.omission_judge.attempt_{attempt + 1}.json").write_text(
            json.dumps(omission_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        content, membership_report = await _run_top_class_membership_judge(
            llm=membership_llm,
            candidate_text=content,
            source_text=paper,
            top_class_iri=class_iri,
            top_class_comment=class_comment,
        )
        membership_report["model"] = model_name
        (doi_dir / f"top_entities.membership_judge.attempt_{attempt + 1}.json").write_text(
            json.dumps(membership_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ok_lines, line_errors = validate_top_entity_lines(content, list(prefixes))
        if not ok_lines:
            salvaged = keep_valid_top_entity_lines(content, prefixes)
            if salvaged.strip():
                content = salvaged
                ok_lines = True
            else:
                feedback_history.append("; ".join(line_errors[:3]))
                continue
        if content.strip():
            last_valid = content
        semantic_report = await asyncio.to_thread(
            judge_extraction_semantics,
            document_text=paper,
            ontology_contract={
                "top_class": {
                    "iri": class_iri,
                    "local_name": class_local,
                    "class_contract": class_comment,
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
        semantic_errors = _top_entity_semantic_audit_errors(
            semantic_report,
            top_class_comment=class_comment,
        )
        semantic_report["top_scope_acceptance"] = {
            "accepted": not semantic_errors,
            "grounded_contract_errors": semantic_errors,
            "policy": "exact_top_class_contract_evidence",
        }
        (doi_dir / f"top_entities.semantic_audit.attempt_{attempt + 1}.json").write_text(
            json.dumps(semantic_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if semantic_errors:
            feedback_history.append(
                "Top-entity semantic audit rejected the candidate:\n"
                + json.dumps(
                    {"top_scope_acceptance": semantic_report.get("top_scope_acceptance")},
                    ensure_ascii=False,
                )
            )
            continue
        return content
    if last_valid.strip():
        warn_continue("top-entity judges", "kept last structurally valid listing")
        return last_valid
    return ""


def run_step(doi_hash: str, config: dict) -> bool:
    assert_extraction_revision_locked(config)
    data_dir = config.get("data_dir", "data")
    ontology_name = str(config.get("ontology_name") or "").strip()
    print(f">> Top Entity Extraction: {doi_hash}")
    try:
        top = resolve_top_class(config)
    except Exception as exc:
        print(f"[WARN] Generated top class is missing: {exc}; continuing")
        return True
    class_iri = top["class_iri"]
    class_local = top["class_local"]
    class_comment = str(top.get("comment") or "")
    prefixes = top_entity_line_prefixes(class_local, class_iri)
    doi_dir = Path(data_dir) / doi_hash
    output_file = doi_dir / "top_entities.txt"
    paper = load_paper_content(doi_hash, data_dir)
    if not paper.strip():
        print("[WARN] No usable paper content for top-entity extraction; continuing")
        return True
    if extraction_revision_complete(output_file):
        existing = output_file.read_text(encoding="utf-8")
        ok, _ = validate_top_entity_lines(existing, prefixes)
        if ok:
            persist_and_validate_top_class_selection(
                doi_dir=doi_dir,
                class_local=class_local,
                class_iri=class_iri,
            )
            print(f"[SKIP] top-entity listing already revised: {output_file}")
            return True
        print(
            "[INFO] top-entity listing revision receipt exists but the listing "
            "is invalid; re-running locked extraction revision"
        )
    elif output_file.is_file() and output_file.stat().st_size > 0:
        print(
            "[INFO] top-entity listing exists without an extraction-revision "
            "receipt; re-running locked extraction revision"
        )
    try:
        prompt = bind_paper_content(_iter1_prompt(ontology_name), paper)
    except Exception as exc:
        print(f"[WARN] Could not load ITER 1 prompt: {exc}; continuing")
        return True
    if class_iri and class_iri not in prompt:
        prompt += (
            "\n\n---- PIPELINE-INJECTED TOP CLASS: BEGIN ----\n"
            f"Selected top class local: {class_local}\n"
            f"Selected top class IRI: {class_iri}\n"
            f"{class_comment}\n"
            "---- PIPELINE-INJECTED TOP CLASS: END ----\n"
        )
    prompt = _append_conservative_top_class_gate(prompt)
    doi_dir.mkdir(parents=True, exist_ok=True)
    (doi_dir / "iter1_full_prompt.md").write_text(prompt, encoding="utf-8")
    model = runtime_extraction_model(config)
    try:
        normalized = asyncio.run(
            _extract_top_entities_with_judges(
                prompt=prompt,
                paper=paper,
                prefixes=prefixes,
                class_iri=class_iri,
                class_local=class_local,
                class_comment=class_comment,
                model_name=model,
                doi_dir=doi_dir,
                skip_judges=skip_extraction_judges(config),
            )
        )
    except Exception as exc:
        warn_continue("top-entity judges", str(exc))
        normalized = ""
    if not str(normalized or "").strip():
        print("[WARN] Top-entity listing is empty after retries; continuing")
        return True
    output_file.write_text(normalized, encoding="utf-8")
    skip_judges = skip_extraction_judges(config)
    write_extraction_revision_receipt(
        output_file,
        attempts=1 if skip_judges else TOP_ENTITY_REVISION_ATTEMPTS,
        max_attempts=TOP_ENTITY_REVISION_ATTEMPTS,
        accepted=True,
        judges=["one_shot"] if skip_judges else [
            "tbox_revise",
            "omission",
            "membership",
            "semantic_audit",
        ],
    )
    if not persist_and_validate_top_class_selection(
        doi_dir=doi_dir,
        class_local=class_local,
        class_iri=class_iri,
    ):
        warn_continue("top-class selection", "lineage is incomplete")
    print(f"[OK] Top Entity Extraction completed: {doi_hash}")
    return True
