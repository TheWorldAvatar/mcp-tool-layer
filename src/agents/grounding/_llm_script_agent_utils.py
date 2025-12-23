#!/usr/bin/env python3
"""
Shared utilities for LLM-based script generation agents (grounding pipeline).

These agents generate scripts A/B/C, so they must:
- keep prompts domain-agnostic (TTL + optional sampling JSON are the only domain knowledge)
- guarantee valid python output (compile/repair loop)
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class LLMGenConfig:
    model: str = "gpt-4.1"
    timeout: int = 600
    temperature: float = 0.0
    top_p: float = 1.0
    max_attempts: int = 4


def generate_python_module_with_repair(
    *,
    prompt: str,
    out_path: Path,
    cfg: LLMGenConfig = LLMGenConfig(),
    require_substrings: Optional[list[str]] = None,
) -> Path:
    llm = LLMCreator(
        model=cfg.model,
        remote_model=True,
        model_config=ModelConfig(temperature=cfg.temperature, top_p=cfg.top_p, timeout=cfg.timeout),
        structured_output=False,
    ).setup_llm()

    resp = llm.invoke(prompt)
    content = getattr(resp, "content", None)
    if not isinstance(content, str):
        content = str(resp) if resp is not None else ""
    code = _strip_code_fences(content)

    attempt = 1
    last_err: Optional[str] = None
    while True:
        try:
            ast.parse(code)
            if require_substrings:
                missing = [s for s in require_substrings if s not in code]
                if missing:
                    raise ValueError(f"Missing required content: {missing}")
            break
        except Exception as e:  # noqa: BLE001 (agent-side)
            last_err = f"{e.__class__.__name__}: {e}"
            if attempt >= cfg.max_attempts:
                raise RuntimeError(f"LLM produced invalid/incomplete python after {cfg.max_attempts} attempts: {last_err}") from e
            repair_prompt = f"""
Your previous output is invalid or incomplete.

Error:
{last_err}

Fix the code. Return ONLY the corrected full Python module code (no markdown).

Important:
- Avoid Python f-strings that include SPARQL curly braces. Use normal strings + .format with doubled braces, or string concatenation.

Previous code:
{code}
""".strip()
            resp = llm.invoke(repair_prompt)
            content = getattr(resp, "content", None)
            if not isinstance(content, str):
                content = str(resp) if resp is not None else ""
            code = _strip_code_fences(content)
            attempt += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(code, encoding="utf-8")
    return out_path


