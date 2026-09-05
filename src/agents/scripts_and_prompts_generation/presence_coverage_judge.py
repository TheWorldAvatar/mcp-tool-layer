"""Presence-coverage gate: an LLM decides whether required work is present.

Code does not interpret hints, classify fields, or match graph occurrences.
It only loads inputs, calls the model, validates the JSON shape, and formats
rebuild-oriented feedback without previous-attempt instance IRIs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)
from src.agents.scripts_and_prompts_generation.llm_framework_integrity_microjudge import (
    parse_semantic_hint_items,
)

SCHEMA_VERSION = "presence-coverage.v1"
DEFAULT_MODEL = "gpt-4o"
_INSTANCE_IRI = re.compile(r"(?:https?://|urn:)[^\s`'\"<>]+", re.I)


def catalog_for_groups(groups: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Normalize caller-supplied MCP groups. Names are not expanded here."""
    normalized: list[dict[str, Any]] = []
    for raw in groups or []:
        if isinstance(raw, dict):
            name = str(raw.get("name") or "").strip()
            any_of = [
                str(item).strip()
                for item in (raw.get("any_of") or [])
                if str(item).strip()
            ]
            if not any_of and name:
                any_of = [name]
            if not name and not any_of:
                continue
            normalized.append(
                {
                    "name": name or any_of[0],
                    "purpose": str(raw.get("purpose") or "").strip(),
                    "any_of": any_of,
                    "hint_markers": [
                        str(item).strip()
                        for item in (raw.get("hint_markers") or [])
                        if str(item).strip()
                    ],
                    "applies": str(raw.get("applies") or "").strip(),
                }
            )
            continue
        name = str(raw).strip()
        if name:
            normalized.append(
                {
                    "name": name,
                    "purpose": "",
                    "any_of": [name],
                    "hint_markers": [],
                    "applies": "",
                }
            )
    return normalized


def _strip_instance_iris(value: str) -> str:
    return _INSTANCE_IRI.sub("<omitted-instance>", str(value or "")).strip()


def _load_abox_turtle(
    *,
    abox_turtle: str | None = None,
    abox_path: Path | None = None,
) -> str:
    if abox_path is not None:
        return Path(abox_path).read_text(encoding="utf-8")
    if abox_turtle is not None:
        return str(abox_turtle)
    raise ValueError("provide abox_turtle or abox_path")


def hint_item_ledger(hints_text: str) -> list[dict[str, Any]]:
    """Split hints into a closed item list. This is parsing, not presence judgment."""
    ledger: list[dict[str, Any]] = []
    for item in parse_semantic_hint_items(str(hints_text or "")):
        if item.item_id == "source-item-1" and not item.class_hint and not item.fields:
            continue
        ledger.append(
            {
                "item_id": item.item_id,
                "class_hint": item.class_hint,
                "marker": item.marker,
                "fields": [{"key": key, "value": value} for key, value in item.fields],
                "evidence": item.evidence,
            }
        )
    return ledger


def _tool_activity_summary(activity: dict[str, Any] | None) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for output in (activity or {}).get("tool_outputs") or []:
        if not isinstance(output, dict):
            continue
        payload = output.get("structured_content")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(str(output.get("content") or ""))
            except (TypeError, json.JSONDecodeError):
                payload = {}
        status = str(
            (payload or {}).get("status") or output.get("status") or ""
        ).strip()
        summary.append(
            {
                "name": str(output.get("name") or "").strip(),
                "status": status,
                "ok": (payload or {}).get("ok"),
            }
        )
    if summary:
        return summary
    names = []
    for key in ("executed_tool_names", "executed_tool_name_set"):
        names.extend(str(name).strip() for name in (activity or {}).get(key) or [])
    return [{"name": name, "status": "executed", "ok": True} for name in names if name]


def build_presence_coverage_prompt(
    *,
    hints_text: str,
    abox_turtle: str,
    tool_activity: dict[str, Any] | None = None,
    mcp_catalog: list[dict[str, Any]] | None = None,
    ontology_contract: dict[str, Any] | None = None,
    hint_items: list[dict[str, Any]] | None = None,
) -> str:
    """Domain-neutral presence prompt. The model, not code, decides obligations."""
    ledger = hint_items if hint_items is not None else hint_item_ledger(hints_text)
    return (
        "You are the presence-coverage auditor for one iteration attempt.\n"
        "Decide only whether the required work is present. Do not judge whether a "
        "written value is scientifically correct, canonical, or preferred.\n\n"
        "Exhaustive accounting:\n"
        "- HINT ITEM LEDGER is a closed list. Return exactly one source_items "
        "entry for every ledger item_id. Copy each item_id verbatim.\n"
        "- Do not sample. Do not stop after the first failure. Do not omit an "
        "item because it looks similar to another, shares a quantity clause, or "
        "looks already satisfied.\n"
        "- status=fail when that item's own required work is absent from the "
        "relevant occurrence. status=pass only when that item's work is present.\n\n"
        "How to judge each ledger item:\n"
        "- Inspect the candidate A-Box and the tool-activity summary.\n"
        "- The whole ledger item is in scope: structured fields and the heading or "
        "evidence prose. If the item mentions a quantity, duration, temperature, "
        "rate, volume, mass, or other measurable fact, that information is an "
        "obligation even when it is not written as a key:value field.\n"
        "- If the hint mentions such a fact and the relevant A-Box occurrence does "
        "not carry that information, status=fail. A label-only node is not enough.\n"
        "- A fact is present when the relevant occurrence carries the required "
        "property, link, or quantity-bearing literal. A wrong value still counts as "
        "present.\n"
        "- Distinct occurrences required by the hints are not interchangeable. A "
        "same-label node from another ownership layer does not satisfy a different "
        "occurrence.\n"
        "- Do not invent facts, links, or quantities that this item never mentions.\n"
        "- Do not use domain-specific hard-coded class or property names. Use only "
        "the names that appear in the supplied hints, A-Box, contract, or catalog.\n"
        "- If the configured tool catalog is empty, tool_missing must be [].\n"
        "- If the catalog is non-empty, decide from the hints whether those groups "
        "applied; a group is done only when a listed tool has a successful "
        "non-error result.\n\n"
        "Repair text rules:\n"
        "- The failed attempt will be rolled back. Previous-attempt instance IRIs "
        "will not exist. Never put an instance IRI in any field.\n"
        "- For status=fail, describe rebuild_target and missing_work by hint "
        "identity, role, and source label. Quote the missing hint-mentioned "
        "quantity or fact in missing_work. For status=pass, leave those empty.\n"
        "- Tell the agent to rebuild first, then include the missing work in the "
        "same create call that materializes that occurrence.\n\n"
        "Return only one JSON object with exactly these keys:\n"
        "{"
        '"accepted":false,'
        '"summary":"",'
        '"source_items":[{'
        '"item_id":"",'
        '"status":"pass|fail",'
        '"rebuild_target":"",'
        '"missing_work":"",'
        '"same_layer_only":true'
        "}],"
        '"tool_missing":[{"group":"","any_of":[],"reason":""}]'
        "}\n"
        "- source_items length must equal the ledger length.\n"
        "- accepted=true only when every source_item is pass and tool_missing is empty.\n\n"
        f"HINT ITEM LEDGER:\n{json.dumps(ledger, ensure_ascii=False)}\n\n"
        f"CURRENT SOURCE HINTS:\n{hints_text}\n\n"
        "ONTOLOGY CONTRACT:\n"
        f"{json.dumps(ontology_contract or {}, ensure_ascii=False, sort_keys=True)}\n\n"
        "CONFIGURED TOOL CATALOG:\n"
        f"{json.dumps(catalog_for_groups(mcp_catalog or []), ensure_ascii=False)}\n\n"
        "TOOL ACTIVITY SUMMARY:\n"
        f"{json.dumps(_tool_activity_summary(tool_activity), ensure_ascii=False)}\n\n"
        f"CANDIDATE A-BOX (Turtle):\n{abox_turtle}\n"
    )


def _validated_presence_report(
    data: dict[str, Any],
    *,
    expected_item_ids: list[str],
) -> dict[str, Any]:
    required = {"accepted", "summary", "source_items", "tool_missing"}
    if set(data) != required:
        raise ValueError(
            "presence report top-level keys differ from the required schema: "
            f"expected={sorted(required)}, actual={sorted(data)}"
        )
    if not isinstance(data["accepted"], bool):
        raise ValueError("accepted must be boolean")
    source_items = data["source_items"]
    tool_missing = data["tool_missing"]
    if not isinstance(source_items, list) or not isinstance(tool_missing, list):
        raise ValueError("source_items and tool_missing must be lists")
    expected = [str(item_id).strip() for item_id in expected_item_ids if str(item_id).strip()]
    expected_set = set(expected)
    cleaned_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(source_items):
        if not isinstance(raw, dict):
            raise ValueError(f"source_items[{index}] must be an object")
        item_id = _strip_instance_iris(str(raw.get("item_id") or ""))
        if item_id not in expected_set or item_id in seen:
            continue
        seen.add(item_id)
        status = str(raw.get("status") or "").strip().casefold()
        if status not in {"pass", "fail"}:
            raise ValueError(f"source_items[{index}].status must be pass or fail")
        row = {
            "item_id": item_id,
            "status": status,
            "rebuild_target": _strip_instance_iris(str(raw.get("rebuild_target") or "")),
            "missing_work": _strip_instance_iris(str(raw.get("missing_work") or "")),
            "same_layer_only": bool(raw.get("same_layer_only")),
        }
        cleaned_items.append(row)
    unaccounted = [item_id for item_id in expected if item_id not in seen]
    for item_id in unaccounted:
        cleaned_items.append(
            {
                "item_id": item_id,
                "status": "fail",
                "rebuild_target": "the occurrence created for this ledger item",
                "missing_work": (
                    "This ledger item was not judged. Treat its presence as incomplete "
                    "and include its source-supported work in the create call."
                ),
                "same_layer_only": True,
            }
        )
    cleaned_missing = [
        {
            "item_id": row["item_id"],
            "rebuild_target": row["rebuild_target"],
            "missing_work": row["missing_work"],
            "same_layer_only": row["same_layer_only"],
        }
        for row in cleaned_items
        if row["status"] == "fail"
    ]
    cleaned_present = [row for row in cleaned_items if row["status"] == "pass"]
    cleaned_tools: list[dict[str, Any]] = []
    for index, raw in enumerate(tool_missing):
        if not isinstance(raw, dict):
            raise ValueError(f"tool_missing[{index}] must be an object")
        any_of = [
            str(item).strip()
            for item in (raw.get("any_of") or [])
            if str(item).strip()
        ]
        cleaned_tools.append(
            {
                "group": str(raw.get("group") or "").strip(),
                "any_of": any_of,
                "reason": str(raw.get("reason") or "").strip(),
            }
        )
    should_accept = not cleaned_missing and not cleaned_tools
    return {
        "accepted": should_accept,
        "summary": str(data.get("summary") or "").strip(),
        "source_items": cleaned_items,
        "missing": cleaned_missing,
        "present": cleaned_present,
        "unaccounted_item_ids": unaccounted,
        "tool_missing": cleaned_tools,
    }


def format_presence_coverage_feedback(report: dict[str, Any]) -> str:
    """Rebuild-oriented retry text. Never name a previous-attempt IRI."""
    if report.get("accepted"):
        return ""
    lines = [
        "PRESENCE COVERAGE FEEDBACK:",
        "The previous attempt was rolled back. Previous-attempt instance IRIs are gone;",
        "do not look them up, reuse them, or patch them.",
        "This gate checks presence only; it does not judge whether a written value is correct.",
        "First rebuild the iteration-owned graph from the restored baseline and the current hints.",
        "Only after that rebuild, make sure every obligation below is present on the newly created occurrences.",
    ]
    missing_facts = (report.get("hint_coverage") or {}).get("missing") or report.get(
        "missing"
    ) or []
    if missing_facts:
        lines.append("")
        lines.append("Rebuild obligations (create-time presence, not a later patch):")
        for row in missing_facts:
            item_id = _strip_instance_iris(str(row.get("item_id") or "hint item"))
            target = _strip_instance_iris(
                str(row.get("rebuild_target") or row.get("linked_label") or "")
            )
            work = _strip_instance_iris(
                str(row.get("missing_work") or row.get("property") or "")
            )
            if target and work:
                lines.append(f"- Hint item `{item_id}`: when creating {target}, {work}.")
            elif work:
                lines.append(f"- Hint item `{item_id}`: {work}.")
            else:
                lines.append(
                    f"- Hint item `{item_id}`: include the missing source-supported "
                    "work in the create call for that occurrence."
                )
            if row.get("same_layer_only") or row.get("source") == "hint_prose_quantity":
                lines.append(
                    "  A same-label occurrence from another ownership layer does not "
                    "satisfy this hint item."
                )
    missing_tools = (report.get("tool_coverage") or {}).get("missing") or report.get(
        "tool_missing"
    ) or []
    if missing_tools:
        lines.append("")
        lines.append("Missing applicable tool results:")
        for row in missing_tools:
            any_of = ", ".join(f"`{name}`" for name in (row.get("any_of") or []) if name)
            group = str(row.get("group") or "configured group")
            reason = str(row.get("reason") or "").strip()
            extra = f" {reason}" if reason else ""
            if any_of:
                lines.append(
                    f"- `{group}`: after the rebuild is underway, call at least "
                    f"one of {any_of} and keep a successful non-error result.{extra}"
                )
            else:
                lines.append(
                    f"- `{group}`: after the rebuild is underway, complete the "
                    f"configured tool work.{extra}"
                )
    lines.extend(
        [
            "",
            "ACTION FOR THIS RETRY:",
            "1. Rebuild the full iteration-owned graph from the restored baseline and hints.",
            "2. While creating each listed occurrence, include the missing property in the create call.",
            "3. Do not address a missing property by editing or targeting an IRI from the failed attempt.",
            "4. After the rebuild includes every listed obligation, export or return the output.",
        ]
    )
    return "\n".join(lines)


def judge_tool_coverage(
    *,
    hints_text: str,
    tool_activity: dict[str, Any] | None,
    catalog: list[dict[str, Any]] | None = None,
    require_configured_groups: bool = False,
) -> dict[str, Any]:
    """Mechanical check of configured tool traces. This is not graph judgment."""
    catalog = catalog_for_groups(catalog or [])
    if require_configured_groups:
        groups = [
            group
            for group in catalog
            if str(group.get("applies") or "").casefold() != "fallback"
        ]
    else:
        text = str(hints_text or "").casefold()
        groups = []
        for group in catalog:
            applies = str(group.get("applies") or "").strip().casefold()
            if applies in {"always", "when_configured"}:
                groups.append(group)
                continue
            markers = [
                str(item).strip()
                for item in (group.get("hint_markers") or [])
                if str(item).strip()
            ]
            if markers and any(marker.casefold() in text for marker in markers):
                groups.append(group)
    successful = {
        str(row.get("name") or "").strip()
        for row in _tool_activity_summary(tool_activity)
        if str(row.get("name") or "").strip()
        and row.get("ok") is not False
        and str(row.get("status") or "").casefold()
        not in {"rejected", "error", "failed", "failure"}
    }
    missing = []
    satisfied = []
    for group in groups:
        any_of = [str(name).strip() for name in (group.get("any_of") or []) if str(name).strip()]
        hit = next((name for name in any_of if name in successful), "")
        if hit:
            satisfied.append({"group": group.get("name"), "tool": hit})
        else:
            missing.append(
                {
                    "group": group.get("name"),
                    "purpose": group.get("purpose"),
                    "any_of": any_of,
                    "reason": "applicable_tool_not_called_or_no_success_result",
                }
            )
    return {
        "accepted": not missing,
        "applicable_groups": [group.get("name") for group in groups],
        "successful_tools": sorted(successful),
        "satisfied": satisfied,
        "missing": missing,
    }


def judge_presence_coverage(
    *,
    hints_text: str,
    tool_activity: dict[str, Any] | None = None,
    mcp_catalog: list[dict[str, Any]] | None = None,
    abox_turtle: str | None = None,
    abox_path: Path | None = None,
    ontology_contract: dict[str, Any] | None = None,
    model: str = DEFAULT_MODEL,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
) -> dict[str, Any]:
    """Ask gpt-4o whether hinted work is present. Code does not decide the facts."""
    turtle = _load_abox_turtle(abox_turtle=abox_turtle, abox_path=abox_path)
    ledger = hint_item_ledger(hints_text)
    expected_ids = [str(row["item_id"]) for row in ledger]
    prompt = build_presence_coverage_prompt(
        hints_text=hints_text,
        abox_turtle=turtle,
        tool_activity=tool_activity,
        mcp_catalog=mcp_catalog,
        ontology_contract=ontology_contract,
        hint_items=ledger,
    )
    result = invoke(model or DEFAULT_MODEL, prompt)
    parsed = _validated_presence_report(
        result.data if hasattr(result, "data") else result,
        expected_item_ids=expected_ids,
    )
    if parsed["unaccounted_item_ids"]:
        retry_prompt = (
            prompt
            + "\n\nPrevious response omitted ledger items. Return a complete "
            "source_items list that copies every item_id verbatim. Omitted ids:\n"
            + json.dumps(parsed["unaccounted_item_ids"], ensure_ascii=False)
        )
        result = invoke(model or DEFAULT_MODEL, retry_prompt)
        parsed = _validated_presence_report(
            result.data if hasattr(result, "data") else result,
            expected_item_ids=expected_ids,
        )
    hint_report = {
        "accepted": not parsed["missing"],
        "checked": len(expected_ids),
        "missing": parsed["missing"],
        "present": parsed["present"],
        "source_items": parsed["source_items"],
        "unaccounted_item_ids": parsed["unaccounted_item_ids"],
    }
    tool_report = {
        "accepted": not parsed["tool_missing"],
        "missing": parsed["tool_missing"],
        "satisfied": [],
        "applicable_groups": [
            str(group.get("name") or "")
            for group in catalog_for_groups(mcp_catalog or [])
        ],
        "successful_tools": [
            str(row.get("name") or "")
            for row in _tool_activity_summary(tool_activity)
            if str(row.get("name") or "")
        ],
    }
    observations = []
    for row in parsed["missing"]:
        observations.append({"kind": "missing_hint_fact", **row})
    for row in parsed["tool_missing"]:
        observations.append({"kind": "missing_tool_result", **row})
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": "presence_only_no_correctness",
        "judge": "llm",
        "model": model or DEFAULT_MODEL,
        "accepted": parsed["accepted"],
        "summary": parsed["summary"],
        "hint_coverage": hint_report,
        "tool_coverage": tool_report,
        "missing": parsed["missing"],
        "present": parsed["present"],
        "source_items": parsed["source_items"],
        "unaccounted_item_ids": parsed["unaccounted_item_ids"],
        "tool_missing": parsed["tool_missing"],
        "observations": observations,
    }
