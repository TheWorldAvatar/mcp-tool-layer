"""Resolve explicit shared procedure context once and inject it everywhere.

Semantic content decisions are delegated to an LLM planner and two independent
auditors. Deterministic code only validates transport, caches the accepted
resolution, and renders an idempotent runtime brief.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)


SCHEMA_VERSION = "global-procedure-context.v1"
AUDIT_SCHEMA_VERSION = "global-procedure-context-audit.v1"
BRIEF_BEGIN = "---- GLOBAL_PROCEDURE_CONTEXT: BEGIN ----"
BRIEF_END = "---- GLOBAL_PROCEDURE_CONTEXT: END ----"
INHERITANCE_RULE = (
    "propagate_to_all_compatible_operations_in_declared_scope_unless_overridden"
)
_CONTEXT_KEYS = {
    "context_id",
    "context_class_iri",
    "target_property_iri",
    "canonical_value",
    "source_evidence",
    "declared_scope",
    "scope_kind",
    "inheritance_rule",
    "exceptions",
}
_ROOT_KEYS = {
    "schema_version",
    "contexts",
    "unresolved_references",
    "rationale",
}
_AUDIT_KEYS = {
    "schema_version",
    "accepted",
    "gaps",
    "rationale",
}


class GlobalContextResolutionError(RuntimeError):
    """Raised when explicit shared context cannot be resolved safely."""


def _require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{path} must be a {'string' if allow_empty else 'non-empty string'}")
    return value


def _require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError(f"{path} must be a bounded array")
    return [_require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _project_keys(data: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("payload must be an object")
    return {key: data[key] for key in keys if key in data}


def _nonempty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value[:1000]:
        if isinstance(item, str) and item.strip():
            cleaned.append(item)
    return cleaned


def _coerce_context_entry(context: Any) -> dict[str, Any] | None:
    """Keep a context only when every required field is already filled."""
    if not isinstance(context, dict):
        return None
    item = _project_keys(context, _CONTEXT_KEYS)
    required = (
        "context_class_iri",
        "target_property_iri",
        "canonical_value",
        "source_evidence",
        "declared_scope",
        "scope_kind",
    )
    for key in required:
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
    item["inheritance_rule"] = INHERITANCE_RULE
    item["exceptions"] = _nonempty_strings(item.get("exceptions"))
    item["context_id"] = "G001"
    return item


def _coerce_global_context_resolution(data: Any) -> dict[str, Any]:
    """Repair transport only: drop extras, drop incomplete rows, keep a valid table."""
    projected = _project_keys(data, _ROOT_KEYS)
    raw_contexts = projected.get("contexts")
    contexts: list[dict[str, Any]] = []
    if isinstance(raw_contexts, list):
        for context in raw_contexts[:1000]:
            item = _coerce_context_entry(context)
            if item is None:
                continue
            item["context_id"] = f"G{len(contexts) + 1:03d}"
            contexts.append(item)
    rationale = projected.get("rationale")
    if not isinstance(rationale, str):
        rationale = ""
    return {
        "schema_version": SCHEMA_VERSION,
        "contexts": contexts,
        "unresolved_references": _nonempty_strings(
            projected.get("unresolved_references")
        ),
        "rationale": rationale,
    }


def validate_global_context_resolution(data: dict[str, Any]) -> dict[str, Any]:
    """Validate fixed transport only; never infer or reinterpret context."""
    data = _coerce_global_context_resolution(data)
    if set(data) != _ROOT_KEYS:
        raise ValueError("global context resolution keys differ from required schema")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("global context schema_version is invalid")
    contexts = data["contexts"]
    if not isinstance(contexts, list) or len(contexts) > 1000:
        raise ValueError("contexts must be a bounded array")
    normalized: list[dict[str, Any]] = []
    expected_ids = [f"G{index:03d}" for index in range(1, len(contexts) + 1)]
    for index, context in enumerate(contexts):
        path = f"contexts[{index}]"
        if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
            raise ValueError(f"{path} keys differ from required schema")
        item = dict(context)
        for key in (
            "context_id",
            "context_class_iri",
            "target_property_iri",
            "canonical_value",
            "source_evidence",
            "declared_scope",
            "scope_kind",
            "inheritance_rule",
        ):
            _require_string(item[key], f"{path}.{key}")
        if item["context_id"] != expected_ids[index]:
            raise ValueError("context_id values must be contiguous G001, G002, ...")
        if item["inheritance_rule"] != INHERITANCE_RULE:
            raise ValueError(f"{path}.inheritance_rule is invalid")
        item["exceptions"] = _require_string_list(item["exceptions"], f"{path}.exceptions")
        normalized.append(item)
    unresolved = _require_string_list(
        data["unresolved_references"], "unresolved_references"
    )
    _require_string(data["rationale"], "rationale", allow_empty=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "contexts": normalized,
        "unresolved_references": unresolved,
        "rationale": data["rationale"],
    }


def _validate_audit(data: dict[str, Any]) -> dict[str, Any]:
    data = _project_keys(data, _AUDIT_KEYS)
    data.setdefault("schema_version", AUDIT_SCHEMA_VERSION)
    data.setdefault("accepted", False)
    data.setdefault("gaps", [])
    data.setdefault("rationale", "")
    if set(data) != _AUDIT_KEYS:
        raise ValueError("global context audit keys differ from required schema")
    if data["schema_version"] != AUDIT_SCHEMA_VERSION:
        raise ValueError("global context audit schema_version is invalid")
    if not isinstance(data["accepted"], bool):
        raise ValueError("global context audit accepted must be boolean")
    gaps = _nonempty_strings(data["gaps"])
    if data["accepted"] == bool(gaps):
        raise ValueError("accepted audits require no gaps; rejected audits require gaps")
    _require_string(data["rationale"], "rationale", allow_empty=True)
    return {**data, "gaps": gaps}


def _contract_text(tbox_contract: str | dict[str, Any]) -> str:
    if isinstance(tbox_contract, dict):
        return json.dumps(tbox_contract, ensure_ascii=False, sort_keys=True)
    return str(tbox_contract)


def build_global_context_prompt(
    *, source_text: str, tbox_contract: str | dict[str, Any], feedback: list[str] | None = None
) -> str:
    """Build a domain-neutral planner prompt over the complete source."""
    repair = ""
    if feedback:
        repair = (
            "\n\nPRIOR AUDIT GAPS TO REPAIR:\n"
            + json.dumps(feedback, ensure_ascii=False, indent=2)
        )
    return (
        "You are a global procedure-context resolver. Use semantic reasoning over the "
        "complete source and active T-Box. Do not use keyword rules, regular expressions, "
        "substring matching, or string similarity as evidence.\n\n"
        "Identify every explicit shared-context assertion whose source-declared scope "
        "covers more than one compatible operation and whose T-Box class/property "
        "explicitly permits scoped inheritance. Examples of possible context categories "
        "are determined only by the supplied T-Box; do not assume any domain category. "
        "A statement quantified over a procedure, procedure family, section, or document "
        "is explicit evidence for operations inside that semantic scope, not an inference "
        "from silence. Exclude local-only facts, narrative background, characterization "
        "conditions, and statements whose scope cannot be resolved. Preserve explicit "
        "exceptions and narrower overrides. Never extend context outside its declared "
        "scope.\n\n"
        "Number accepted contexts G001, G002, ... in source order. Use exact full IRIs "
        "from the T-Box. canonical_value must be the T-Box-compatible normalized value. "
        "source_evidence must quote the complete source statement verbatim. declared_scope "
        "must state the exact semantically governed scope. scope_kind is a concise generic "
        "category such as document, section, procedure-family, procedure, or operation-set. "
        f"inheritance_rule must always be `{INHERITANCE_RULE}`. If no T-Box-authorized "
        "shared context exists, return an empty contexts array. Put genuinely unresolved "
        "references in unresolved_references; never guess.\n\n"
        "Return JSON only with exactly this schema:\n"
        "{\n"
        f'  "schema_version": "{SCHEMA_VERSION}",\n'
        '  "contexts": [{\n'
        '    "context_id": "G001",\n'
        '    "context_class_iri": "",\n'
        '    "target_property_iri": "",\n'
        '    "canonical_value": "",\n'
        '    "source_evidence": "",\n'
        '    "declared_scope": "",\n'
        '    "scope_kind": "",\n'
        f'    "inheritance_rule": "{INHERITANCE_RULE}",\n'
        '    "exceptions": []\n'
        "  }],\n"
        '  "unresolved_references": [],\n'
        '  "rationale": ""\n'
        "}\n\n"
        f"ACTIVE T-BOX:\n<<<TBOX\n{_contract_text(tbox_contract)}\nTBOX\n>>>\n\n"
        f"COMPLETE SOURCE:\n<<<SOURCE\n{source_text}\nSOURCE\n>>>"
        + repair
    )


def _build_audit_prompt(
    *,
    source_text: str,
    tbox_contract: str | dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    return (
        "You are one independent global-context completeness auditor. You cannot see "
        "another auditor. Use semantic reasoning over the complete source and active "
        "T-Box; do not use keyword, regex, substring, or string-similarity rules. Check "
        "that the candidate includes every and only T-Box-authorized explicit shared "
        "context, quotes its evidence, resolves its declared scope, preserves exceptions "
        "and overrides, uses the correct class/property/value, and never propagates beyond "
        "scope. An empty candidate is acceptable only if the source contains no qualifying "
        "shared context. Return JSON only with exactly:\n"
        "{\n"
        f'  "schema_version": "{AUDIT_SCHEMA_VERSION}",\n'
        '  "accepted": true,\n'
        '  "gaps": [],\n'
        '  "rationale": ""\n'
        "}\n\n"
        f"ACTIVE T-BOX:\n<<<TBOX\n{_contract_text(tbox_contract)}\nTBOX\n>>>\n\n"
        f"COMPLETE SOURCE:\n<<<SOURCE\n{source_text}\nSOURCE\n>>>\n\n"
        "CANDIDATE:\n"
        + json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True)
    )


def _invoke_validated(
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    validator: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    current = prompt
    errors: list[str] = []
    for _ in range(3):
        try:
            result = invoke(model, current, max_attempts=3)
        except Exception as exc:
            errors.append(str(exc))
            current = (
                prompt
                + "\n\nYour prior response was not parseable JSON: "
                + str(exc)[:500]
                + "\nReturn only one complete JSON object with the requested keys."
            )
            continue
        try:
            payload = result.data if isinstance(result.data, dict) else {}
            return validator(payload)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current = (
                prompt
                + "\n\nYour prior response failed mechanical schema validation: "
                + str(exc)
                + "\nRepair only the JSON transport/schema defect and return the complete object."
            )
    raise GlobalContextResolutionError("; ".join(errors))


def _empty_global_context_resolution(*, rationale: str) -> dict[str, Any]:
    """Valid empty table: no shared context, extraction may continue."""
    return validate_global_context_resolution(
        {
            "schema_version": SCHEMA_VERSION,
            "contexts": [],
            "unresolved_references": [],
            "rationale": rationale,
        }
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cache_key(
    *, source_text: str, tbox_contract: str | dict[str, Any], model: str
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_text": source_text,
        "tbox_contract": tbox_contract,
        "model": model,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _write_cache(
    path: Path,
    cache_key: str,
    resolution: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".global-context.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            payload = {"cache_key": cache_key, "resolution": resolution}
            if extra:
                payload.update(extra)
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def resolve_global_context(
    *,
    source_text: str,
    tbox_contract: str | dict[str, Any],
    model: str,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
    cache_path: str | Path | None = None,
) -> dict[str, Any]:
    """Plan once; unresolved refs stay empty and never abort extraction."""
    key = _cache_key(source_text=source_text, tbox_contract=tbox_contract, model=model)
    cache = Path(cache_path) if cache_path is not None else None
    if cache is not None:
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload.get("cache_key") == key:
                return validate_global_context_resolution(payload["resolution"])
        except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError, ValueError):
            pass

    feedback: list[str] = []
    for _ in range(3):
        planner_prompt = build_global_context_prompt(
            source_text=source_text,
            tbox_contract=tbox_contract,
            feedback=feedback,
        )
        try:
            candidate = _invoke_validated(
                invoke, model, planner_prompt, validate_global_context_resolution
            )
        except GlobalContextResolutionError as exc:
            empty = _empty_global_context_resolution(
                rationale=(
                    "Planner JSON failed mechanical schema validation after repairs; "
                    "continuing with an empty shared-context table. "
                    + str(exc)
                )
            )
            logger.warning(
                "⚠️  Global context planner schema exhausted; "
                "failing open to an empty contexts table: %s",
                exc,
            )
            if cache is not None:
                _write_cache(
                    cache,
                    key,
                    empty,
                    extra={
                        "fail_open": {
                            "kind": "planner_schema_exhausted",
                            "message": str(exc),
                        }
                    },
                )
            return empty
        if candidate["unresolved_references"]:
            logger.warning(
                "⚠️  Global context left unresolved references empty rather "
                "than guessing; extraction continues: %s",
                "; ".join(candidate["unresolved_references"]),
            )
            if cache is not None:
                _write_cache(
                    cache,
                    key,
                    candidate,
                    extra={
                        "fail_open": {
                            "kind": "unresolved_references",
                            "message": "; ".join(
                                candidate["unresolved_references"]
                            ),
                        }
                    },
                )
            return candidate
        audit_prompt = _build_audit_prompt(
            source_text=source_text,
            tbox_contract=tbox_contract,
            candidate=candidate,
        )
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                audits = list(
                    pool.map(
                        lambda _: _invoke_validated(
                            invoke, model, audit_prompt, _validate_audit
                        ),
                        range(2),
                    )
                )
        except GlobalContextResolutionError as exc:
            logger.warning(
                "⚠️  Global context audit schema exhausted; "
                "keeping the structurally valid planner table: %s",
                exc,
            )
            if cache is not None:
                _write_cache(
                    cache,
                    key,
                    candidate,
                    extra={
                        "fail_open": {
                            "kind": "audit_schema_exhausted",
                            "message": str(exc),
                        }
                    },
                )
            return candidate
        if all(audit["accepted"] for audit in audits):
            if cache is not None:
                _write_cache(cache, key, candidate)
            return candidate
        feedback = [
            gap
            for audit in audits
            for gap in audit["gaps"]
        ]
    empty = _empty_global_context_resolution(
        rationale=(
            "Shared-context auditors rejected every planner candidate after 3 "
            "rounds. Continuing with an empty table so missing inheritance "
            "cannot veto extraction. Last audit gaps: "
            + "; ".join(feedback)
        )
    )
    logger.warning(
        "⚠️  Global context semantic audit exhausted; "
        "failing open to an empty contexts table."
    )
    if cache is not None:
        _write_cache(
            cache,
            key,
            empty,
            extra={
                "fail_open": {
                    "kind": "semantic_audit_exhausted",
                    "message": "; ".join(feedback),
                }
            },
        )
    return empty


def render_global_context_brief(resolution: dict[str, Any]) -> str:
    validated = validate_global_context_resolution(resolution)
    return (
        f"{BRIEF_BEGIN}\n"
        "This complete-source context ledger is authoritative runtime evidence. Before "
        "per-entity work, resolve which entries cover the exact target. In PRE extraction, "
        "copy every applicable source statement into scope_resolution.source_dependencies. "
        "In every extraction iteration, attach applicable inherited context to every "
        "compatible owned occurrence unless narrower explicit evidence overrides it. In "
        "every KG iteration, preserve the ledger and materialize only iteration-owned "
        "facts; inherited properties remain operation-local and must not create standalone "
        "context operations. Never apply an entry outside declared_scope.\n"
        + json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{BRIEF_END}"
    )


def inject_global_context_brief(prompt: str, brief: str) -> str:
    """Append one authoritative brief, replacing any stale injected copy."""
    if not brief.strip():
        return prompt
    start = prompt.find(BRIEF_BEGIN)
    end = prompt.find(BRIEF_END)
    if start >= 0 and end >= start:
        prompt = prompt[:start].rstrip() + prompt[end + len(BRIEF_END) :]
    return prompt.rstrip() + "\n\n" + brief.strip() + "\n"


def load_global_context_brief(cache_path: str | Path) -> str:
    """Load a previously resolved cache artifact without invoking an LLM."""
    try:
        payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        resolution = payload["resolution"]
    except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError):
        return ""
    return render_global_context_brief(resolution)
