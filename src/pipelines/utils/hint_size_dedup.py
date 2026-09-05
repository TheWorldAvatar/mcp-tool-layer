"""Size-alarm hint rewrite: collapse consecutive exact repeats via one LLM call.

Generic: no field-name special cases and no domain vocabulary. A ledger only
enters this path when its character count exceeds the alarm threshold. The
rewriter may drop consecutive exact copies inside one field value; every
distinct token stays.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.utils.global_logger import get_logger

logger = get_logger("pipeline", "hint_size_dedup")

DEFAULT_THRESHOLD = 32768
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_TIMEOUT_SECONDS = 180

_HEADING_LINE = re.compile(r"^[^\n]+")
_FIELD_LINE = re.compile(r"^(\s*-?\s*)([^:\n]+:\s*)(.*)$")


@dataclass(frozen=True)
class HintSizeDedupResult:
    text: str
    applied: bool
    reason: str
    before_chars: int
    after_chars: int
    model: str = DEFAULT_MODEL
    threshold: int = DEFAULT_THRESHOLD
    details: dict[str, Any] = field(default_factory=dict)


def resolve_hint_size_threshold(raw: Any = None) -> int:
    if raw is not None:
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    env = os.environ.get("HINT_SIZE_DEDUP_THRESHOLD", "").strip()
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def resolve_hint_size_model(raw: Any = None) -> str:
    if raw:
        text = str(raw).strip()
        if text:
            return text
    env = os.environ.get("HINT_SIZE_DEDUP_MODEL", "").strip()
    return env or DEFAULT_MODEL


def _strip_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _first_line(text: str) -> str:
    match = _HEADING_LINE.match((text or "").lstrip())
    return match.group(0).strip() if match else ""


def _field_line_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if ":" in line)


def _item_count(text: str) -> int:
    heading = _first_line(text)
    if not heading.startswith("SEMANTIC_HINTS_V1"):
        return 0
    try:
        from src.agents.scripts_and_prompts_generation.llm_framework_integrity_microjudge import (
            parse_semantic_hint_items,
        )
    except Exception:
        return 0
    return len(parse_semantic_hint_items(text))


def _structure_ok(original: str, rewritten: str) -> str:
    if not rewritten.strip():
        return "empty_rewrite"
    if _first_line(original) != _first_line(rewritten):
        return "heading_changed"
    original_items = _item_count(original)
    rewritten_items = _item_count(rewritten)
    if original_items and rewritten_items < original_items:
        return "item_count_dropped"
    if _field_line_count(rewritten) < _field_line_count(original):
        return "field_line_count_dropped"
    return ""


def _collapse_delimited_consecutive(value: str, *, min_run: int = 3) -> str:
    """Collapse back-to-back identical tokens; keep non-consecutive repeats."""
    best = value
    for sep in ("; ", ";", " | ", "|", "\n"):
        if value.count(sep) < min_run - 1:
            continue
        parts = value.split(sep)
        if len(parts) < min_run:
            continue
        kept: list[str] = []
        max_run = 1
        run = 0
        last = None
        for part in parts:
            token = part.strip() if sep != "\n" else part
            key = token.strip()
            if last is not None and key == last:
                run += 1
                max_run = max(max_run, run)
                continue
            kept.append(token)
            last = key
            run = 1
        if max_run < min_run or len(kept) >= len(parts):
            continue
        rebuilt = sep.join(kept)
        if len(rebuilt) < len(best):
            best = rebuilt
    return best


def collapse_consecutive_repeats(text: str, *, min_run: int = 3) -> str:
    """Field-scoped consecutive-run collapse used only if the LLM rewrite fails."""
    lines = (text or "").splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        ended = line.endswith("\n")
        body = line[:-1] if ended else line
        match = _FIELD_LINE.match(body)
        if not match or len(match.group(3)) < 512:
            out.append(line)
            continue
        collapsed = _collapse_delimited_consecutive(match.group(3), min_run=min_run)
        rebuilt = f"{match.group(1)}{match.group(2)}{collapsed}"
        out.append(rebuilt + ("\n" if ended else ""))
    return "".join(out)


def _build_dedup_prompt(text: str, *, threshold: int) -> str:
    return (
        "A structured extraction ledger exceeded the pipeline size alarm "
        f"({threshold} characters).\n"
        "Rewrite it by collapsing consecutive exact repeats inside a single "
        "field value.\n\n"
        "Rules:\n"
        "- A consecutive exact repeat is the same token or phrase appearing "
        "back-to-back in one field value.\n"
        "- Keep the first copy of that run. Keep every distinct token. Do not "
        "impose a count cap on distinct tokens.\n"
        "- Do not drop, merge, reorder, or rewrite items, field names, "
        "headings, or unique lexical values.\n"
        "- Do not summarize. Do not invent values. Do not judge whether a "
        "unique token is useful.\n"
        "- If a field has no consecutive exact repeat, copy it unchanged.\n"
        "- Preserve the original ledger format and first heading line.\n\n"
        "Return only the rewritten ledger. Do not wrap it in markdown fences.\n\n"
        "LEDGER:\n<<<\n"
        f"{text}\n"
        ">>>\n"
    )


def _invoke_rewritten_ledger(model: str, prompt: str, *, timeout_seconds: int) -> str:
    from models.LLMCreator import LLMCreator
    from models.ModelConfig import ModelConfig
    from src.agents.scripts_and_prompts_generation.level1_code_repair import (
        _response_text,
    )
    from src.agents.scripts_and_prompts_generation.llm_invocation_runtime import (
        invoke_with_hard_timeout,
    )

    llm = LLMCreator(
        model=model,
        remote_model=True,
        model_config=ModelConfig(
            timeout=timeout_seconds,
            temperature=0.0,
            top_p=0.1,
        ),
    ).setup_llm()
    response = invoke_with_hard_timeout(
        lambda: llm.invoke(prompt),
        timeout_seconds=timeout_seconds,
    )
    return _strip_fences(_response_text(response))


def _persist_rewrite(path: str, original: str, rewritten: str, result: HintSizeDedupResult) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".pre_size_dedup.txt")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    target.write_text(rewritten, encoding="utf-8")
    sidecar = target.with_name(target.name + ".size_dedup.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "hint-size-dedup.v1",
                "applied": result.applied,
                "reason": result.reason,
                "before_chars": result.before_chars,
                "after_chars": result.after_chars,
                "model": result.model,
                "threshold": result.threshold,
                "details": result.details,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def maybe_dedup_oversized_hint(
    text: str,
    *,
    threshold: Any = None,
    model: Any = None,
    invoke: Callable[..., str] | None = None,
    artifact_path: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> HintSizeDedupResult:
    """Rewrite a ledger only when it exceeds the size alarm."""
    original = str(text or "")
    limit = resolve_hint_size_threshold(threshold)
    chosen_model = resolve_hint_size_model(model)
    before = len(original)
    if os.environ.get("HINT_SIZE_DEDUP_DISABLED", "").strip() in {"1", "true", "yes"}:
        return HintSizeDedupResult(
            text=original,
            applied=False,
            reason="disabled",
            before_chars=before,
            after_chars=before,
            model=chosen_model,
            threshold=limit,
        )
    if before <= limit:
        return HintSizeDedupResult(
            text=original,
            applied=False,
            reason="below_threshold",
            before_chars=before,
            after_chars=before,
            model=chosen_model,
            threshold=limit,
        )

    prompt = _build_dedup_prompt(original, threshold=limit)
    caller = invoke or _invoke_rewritten_ledger
    details: dict[str, Any] = {"alarm_chars": before, "threshold": limit}
    rewritten = ""
    try:
        rewritten = caller(
            chosen_model,
            prompt,
            timeout_seconds=timeout_seconds,
        )
        rewritten = _strip_fences(str(rewritten or ""))
    except Exception as exc:
        details["llm_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("    Size-alarm hint rewrite failed; using fallback: %s", exc)

    reason = _structure_ok(original, rewritten) if rewritten else "llm_empty_or_failed"
    if not reason and len(rewritten) >= before:
        reason = "rewrite_not_smaller"

    if reason:
        fallback = collapse_consecutive_repeats(original)
        fallback_reason = _structure_ok(original, fallback)
        if not fallback_reason and len(fallback) < before:
            result = HintSizeDedupResult(
                text=fallback,
                applied=True,
                reason="fallback_consecutive_collapse",
                before_chars=before,
                after_chars=len(fallback),
                model=chosen_model,
                threshold=limit,
                details={**details, "llm_reject": reason},
            )
            logger.warning(
                "    Size-alarm LLM rewrite rejected (%s); collapsed consecutive repeats %s→%s",
                reason,
                before,
                result.after_chars,
            )
        else:
            result = HintSizeDedupResult(
                text=original,
                applied=False,
                reason=reason,
                before_chars=before,
                after_chars=before,
                model=chosen_model,
                threshold=limit,
                details=details,
            )
            logger.warning(
                "    Size-alarm hint rewrite rejected (%s); leaving original %s-char ledger",
                reason,
                before,
            )
    else:
        result = HintSizeDedupResult(
            text=rewritten,
            applied=True,
            reason="llm_dedup",
            before_chars=before,
            after_chars=len(rewritten),
            model=chosen_model,
            threshold=limit,
            details=details,
        )
        logger.info(
            "    Size-alarm hint rewrite applied (%s→%s chars, model=%s)",
            before,
            result.after_chars,
            chosen_model,
        )

    if artifact_path and result.applied:
        _persist_rewrite(artifact_path, original, result.text, result)
    return result
