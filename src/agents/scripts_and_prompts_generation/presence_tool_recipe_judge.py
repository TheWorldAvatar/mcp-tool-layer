"""Translate presence-coverage gaps into inventory-bound MCP call recipes.

This is not a second presence judge. It only maps missing work onto tools that
already appear in the current iteration inventory. Code then drops any tool
name that is not in that inventory.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)

DEFAULT_MODEL = "gpt-4o"
SCHEMA_VERSION = "presence-tool-recipe.v1"
_INSTANCE_IRI = re.compile(r"(?:https?://|urn:)[^\s`'\"<>]+", re.I)
_TOOL_LINE = re.compile(
    r"^\s*[-*]?\s*(?P<name>init_memory|export_memory|create_[A-Za-z0-9]+|"
    r"add_[A-Za-z0-9]+|check_existing_[A-Za-z0-9]+)\s*"
    r"(?:\((?P<sig>[^)]*)\))?",
    re.M,
)


def _strip_instance_iris(value: str) -> str:
    return _INSTANCE_IRI.sub("<omitted-instance>", str(value or "")).strip()


def extract_tool_inventory(
    kg_prompt: str,
    tool_activity: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Read tool names and signatures from the prompt the agent already saw."""
    seen: dict[str, dict[str, str]] = {}
    for match in _TOOL_LINE.finditer(str(kg_prompt or "")):
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        signature = str(match.group("sig") or "").strip()
        current = seen.setdefault(name, {"name": name, "signature": "", "seen_in_attempt": "false"})
        if signature and not current["signature"]:
            current["signature"] = signature
    for output in (tool_activity or {}).get("tool_outputs") or []:
        if not isinstance(output, dict):
            continue
        name = str(output.get("name") or "").strip()
        if not name:
            continue
        current = seen.setdefault(name, {"name": name, "signature": "", "seen_in_attempt": "true"})
        current["seen_in_attempt"] = "true"
    for name in (tool_activity or {}).get("executed_tool_names") or []:
        key = str(name or "").strip()
        if not key:
            continue
        current = seen.setdefault(key, {"name": key, "signature": "", "seen_in_attempt": "true"})
        current["seen_in_attempt"] = "true"
    return [seen[key] for key in sorted(seen)]


def inventory_tool_names(inventory: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("name") or "").strip()
        for row in inventory
        if str(row.get("name") or "").strip()
    }


def build_tool_recipe_prompt(
    *,
    missing: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    hints_text: str,
) -> str:
    return (
        "You translate presence-coverage gaps into MCP tool recipes.\n"
        "You do not re-judge whether the A-Box is complete.\n\n"
        "Rules:\n"
        "- Use only tool names that appear in EXPOSED TOOL INVENTORY.\n"
        "- Prefer create-time parameters when the inventory shows that a creator "
        "accepts the missing work as an argument. If no later setter exists in "
        "the inventory, say so and do not invent one.\n"
        "- The failed attempt was rolled back. Do not use previous-attempt instance "
        "IRIs. Refer to IRIs only as results of tools in this same recipe sequence.\n"
        "- Fill argument values from the hints and missing items, including "
        "quantities that appear only in hint headings or evidence. Do not guess "
        "scientific values.\n"
        "- If no inventory tool can perform the work, return an empty sequence and "
        "explain that in do_not.\n\n"
        "Return only one JSON object with exactly this shape:\n"
        "{"
        '"recipes":[{'
        '"item_id":"",'
        '"sequence":[{"tool":"","arguments":{},"why":""}],'
        '"do_not":""'
        "}]"
        "}\n\n"
        f"MISSING PRESENCE WORK:\n{json.dumps(missing, ensure_ascii=False)}\n\n"
        f"EXPOSED TOOL INVENTORY:\n{json.dumps(inventory, ensure_ascii=False)}\n\n"
        f"CURRENT SOURCE HINTS:\n{hints_text}\n"
    )


def _validated_recipes(
    data: dict[str, Any],
    allowed_tools: set[str],
) -> list[dict[str, Any]]:
    raw_recipes = data.get("recipes") if isinstance(data, dict) else None
    if not isinstance(raw_recipes, list):
        raise ValueError("recipes must be a list")
    cleaned: list[dict[str, Any]] = []
    for raw in raw_recipes:
        if not isinstance(raw, dict):
            continue
        sequence = []
        for step in raw.get("sequence") or []:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            if tool not in allowed_tools:
                continue
            arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            sequence.append(
                {
                    "tool": tool,
                    "arguments": {
                        str(key): _strip_instance_iris(str(value))
                        for key, value in arguments.items()
                    },
                    "why": _strip_instance_iris(str(step.get("why") or "")),
                }
            )
        if not sequence:
            continue
        cleaned.append(
            {
                "item_id": _strip_instance_iris(str(raw.get("item_id") or "")),
                "sequence": sequence,
                "do_not": _strip_instance_iris(str(raw.get("do_not") or "")),
            }
        )
    return cleaned


def format_tool_recipe_feedback(recipes: list[dict[str, Any]]) -> str:
    if not recipes:
        return ""
    lines = [
        "TOOL RECIPES FROM THE CURRENT INVENTORY:",
        "These tool names were taken from the exposed inventory at audit time.",
        "If the same name is still in the live inventory, call it as written.",
        "Do not invent a later setter when the recipe says the value belongs in the create call.",
    ]
    for recipe in recipes:
        item_id = recipe.get("item_id") or "hint item"
        lines.append("")
        lines.append(f"- Hint item `{item_id}`:")
        for index, step in enumerate(recipe.get("sequence") or [], start=1):
            args = json.dumps(step.get("arguments") or {}, ensure_ascii=False)
            why = str(step.get("why") or "").strip()
            extra = f" ({why})" if why else ""
            lines.append(f"  {index}. `{step.get('tool')}` {args}{extra}")
        do_not = str(recipe.get("do_not") or "").strip()
        if do_not:
            lines.append(f"  Do not: {do_not}")
    return "\n".join(lines)


def propose_tool_recipes(
    *,
    missing: list[dict[str, Any]] | None,
    inventory: list[dict[str, Any]] | None,
    hints_text: str,
    model: str = DEFAULT_MODEL,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
) -> dict[str, Any]:
    """Return inventory-filtered recipes for one failed presence audit."""
    missing = [row for row in (missing or []) if isinstance(row, dict)]
    inventory = [row for row in (inventory or []) if isinstance(row, dict)]
    allowed = inventory_tool_names(inventory)
    if not missing or not allowed:
        return {
            "schema_version": SCHEMA_VERSION,
            "model": model or DEFAULT_MODEL,
            "recipes": [],
        }
    prompt = build_tool_recipe_prompt(
        missing=missing,
        inventory=inventory,
        hints_text=hints_text,
    )
    result = invoke(model or DEFAULT_MODEL, prompt)
    data = result.data if hasattr(result, "data") else result
    if not isinstance(data, dict):
        raise ValueError("tool recipe response must be an object")
    recipes = _validated_recipes(data, allowed)
    return {
        "schema_version": SCHEMA_VERSION,
        "model": model or DEFAULT_MODEL,
        "recipes": recipes,
    }
