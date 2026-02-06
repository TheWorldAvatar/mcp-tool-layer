#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

MODEL="${1:-gpt-5.2}"

echo "[INFO] This will make ONE small LLM call using your configured REMOTE_* env vars."
echo "[INFO] Model: ${MODEL}"

"$PYTHON_BIN" -m src.agents.scripts_and_prompts_generation.llm_smoke_test --model "$MODEL"


