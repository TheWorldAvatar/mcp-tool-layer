"""Procedure inheritance and optional global-context briefs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.extraction_runtime.llm import invoke_json

BRIEF_BEGIN = "---- PROCEDURE_INHERITANCE_BRIEF: BEGIN ----"
BRIEF_END = "---- PROCEDURE_INHERITANCE_BRIEF: END ----"
GLOBAL_BEGIN = "---- GLOBAL_PROCEDURE_CONTEXT: BEGIN ----"
GLOBAL_END = "---- GLOBAL_PROCEDURE_CONTEXT: END ----"


def render_procedure_inheritance_brief(resolution: dict[str, Any]) -> str:
    if not resolution or resolution.get("fail_open"):
        return ""
    payload = {
        "status": resolution.get("status") or "resolved",
        "dependencies": resolution.get("dependencies") or [],
        "effective_workflow": resolution.get("effective_workflow") or [],
        "modifications": resolution.get("modifications") or [],
    }
    if not payload["effective_workflow"] and not payload["dependencies"]:
        return ""
    return (
        f"{BRIEF_BEGIN}\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + f"\n{BRIEF_END}\n"
    )


def inheritance_brief_payload(brief: str) -> dict[str, Any]:
    start = brief.find("{")
    end = brief.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("procedure inheritance brief has no JSON object")
    payload = json.loads(brief[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("procedure inheritance brief has invalid structured payload")
    return payload


def resolve_procedure_inheritance(
    *,
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str,
    model: str,
    target_identity_dossier: dict | None = None,
    top_entity_manifest: list | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    cache_key = hashlib.sha256(
        "\n".join(
            [
                target_procedure_ref,
                target_procedure_label,
                source_text[:4000],
                tbox_contract[:2000],
            ]
        ).encode("utf-8")
    ).hexdigest()
    if cache_path and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                return cached.get("resolution") or {"fail_open": True, "status": "unresolved"}
        except Exception:
            pass

    prompt = (
        "Decide whether the target procedure inherits a workflow from another "
        "procedure in the source (same-as / following / analogously / as described for).\n"
        "Return JSON only:\n"
        '{"inheritance_present": false, "dependencies": [], "effective_workflow": [], '
        '"modifications": [], "unresolved_reasons": []}\n'
        "If inheritance is present, fill dependencies and an effective_workflow of atoms "
        "with atom_id, operation, and source_evidence.\n\n"
        f"TARGET LABEL: {target_procedure_label}\n"
        f"TARGET REF: {target_procedure_ref}\n"
        f"IDENTITY DOSSIER:\n{json.dumps(target_identity_dossier or {}, ensure_ascii=False)}\n"
        f"TOP ENTITIES:\n{json.dumps(top_entity_manifest or [], ensure_ascii=False)}\n"
        f"T-BOX CONTRACT:\n{tbox_contract[:8000]}\n"
        f"SOURCE:\n{source_text[:20000]}\n"
    )
    try:
        vote = invoke_json(prompt, model_name=model)
        resolution = {
            "status": "resolved" if vote.get("inheritance_present") else "absent",
            "fail_open": False,
            "dependencies": vote.get("dependencies") or [],
            "effective_workflow": vote.get("effective_workflow") or [],
            "modifications": vote.get("modifications") or [],
            "unresolved_reasons": vote.get("unresolved_reasons") or [],
            "resolution_attempts": 1,
        }
    except Exception as exc:
        resolution = {
            "status": "unresolved",
            "fail_open": True,
            "unresolved_reasons": [str(exc)],
            "resolution_attempts": 1,
            "dependencies": [],
            "effective_workflow": [],
            "modifications": [],
        }
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"cache_key": cache_key, "resolution": resolution},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return resolution


def resolve_global_context(
    *,
    source_text: str,
    tbox_contract: str,
    model: str,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    if cache_path and cache_path.is_file():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    prompt = (
        "Extract shared procedure context that applies to every top entity "
        "(common solvents, default temperatures, shared workup). Return JSON only:\n"
        '{"shared_context": [], "rationale": ""}\n\n'
        f"T-BOX:\n{tbox_contract[:4000]}\nSOURCE:\n{source_text[:16000]}\n"
    )
    try:
        resolution = invoke_json(prompt, model_name=model)
    except Exception as exc:
        resolution = {"shared_context": [], "rationale": str(exc)}
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(resolution, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return resolution


def render_global_context_brief(resolution: dict[str, Any]) -> str:
    items = resolution.get("shared_context") or []
    if not items:
        return ""
    body = json.dumps(items, ensure_ascii=False, indent=2)
    return f"{GLOBAL_BEGIN}\n{body}\n{GLOBAL_END}\n"


def inject_global_context_brief(prompt: str, brief: str) -> str:
    if not str(brief or "").strip() or GLOBAL_BEGIN in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + brief + "\n"
