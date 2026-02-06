#!/usr/bin/env python3
"""
llm_smoke_test.py

One-shot, low-cost "real LLM" smoke test for this repo's generation stack.

Goal:
  - Make a single LLM call (cheap) to generate a tiny Python file
  - Verify the generated code compiles
  - Write the file under tmp/ (gitignored) so it doesn't pollute the repo

This is intended to catch issues like:
  - missing API env vars / wrong base URL
  - model parameter incompatibilities (max_tokens vs max_completion_tokens)
  - provider-side failures
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    create_openai_client,
    validate_python_syntax,
    _token_limit_kwargs,  # repo-local helper
)


def build_prompt() -> str:
    return (
        "Return ONLY valid Python code (no markdown, no explanation).\n"
        "Write a tiny module with:\n"
        "- function add(a: int, b: int) -> int\n"
        "- function main() that prints add(2, 3)\n"
        "- if __name__ == '__main__': main()\n"
    )


async def run(model: str, out_path: Path) -> None:
    client = create_openai_client()
    prompt = build_prompt()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You write correct Python code."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        **_token_limit_kwargs(model, 256),
    )

    code = (resp.choices[0].message.content or "").strip()
    ok, err = validate_python_syntax(code, str(out_path))
    if not ok:
        raise SystemExit(f"[FAIL] LLM returned invalid Python:\n{err}\n\n---\n{code}\n---\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(code + "\n", encoding="utf-8")
    print(f"[OK] LLM smoke test passed. Wrote: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Cheap real-LLM smoke test (1 call).")
    ap.add_argument("--model", default="gpt-5.2", help="Model name to call (default: gpt-5.2)")
    ap.add_argument("--out", default="tmp/llm_smoke/generated_smoke.py", help="Output file path (default: tmp/llm_smoke/generated_smoke.py)")
    args = ap.parse_args()

    out_path = Path(args.out)
    asyncio.run(run(args.model, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


