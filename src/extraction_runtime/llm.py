"""Plain LLM calls used by extraction steps that do not open an MCP agent."""

from __future__ import annotations

import json
import re
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from models.locked_llm import LOCKED_LLM_SEED, LOCKED_TEMPERATURE


def strip_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or item))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def invoke_text(
    prompt: str,
    *,
    model_name: str,
    temperature: float = LOCKED_TEMPERATURE,
    top_p: float = 0.01,
) -> str:
    del temperature, top_p
    llm = LLMCreator(
        model=model_name,
        remote_model=True,
        model_config=ModelConfig(
            temperature=LOCKED_TEMPERATURE,
            top_p=0.01,
            seed=LOCKED_LLM_SEED,
        ),
    ).setup_llm()
    return response_text(llm.invoke(prompt))


def invoke_json(
    prompt: str,
    *,
    model_name: str,
    temperature: float = LOCKED_TEMPERATURE,
) -> dict[str, Any]:
    raw = strip_code_fences(invoke_text(prompt, model_name=model_name, temperature=temperature))
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON payload must be an object")
    return payload


def write_text(path: str, content: str) -> None:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
