#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

echo "[INFO] Bootstrapping repo folders..."
"$PYTHON_BIN" scripts/bootstrap_repo.py

echo "[INFO] Running built-in generation validation (no LLM calls)..."
"$PYTHON_BIN" -m src.agents.scripts_and_prompts_generation.test_generation

echo "[INFO] Running unittest sanity checks (no LLM calls)..."
"$PYTHON_BIN" -m unittest -q tests.test_generation_pipeline

echo "[OK] Generation pipeline sanity tests passed."


