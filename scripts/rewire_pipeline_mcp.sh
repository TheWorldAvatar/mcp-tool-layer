#!/usr/bin/env bash
set -euo pipefail

# Rewire which generated ontology MCP module the pipeline uses (no regeneration).
#
# Examples:
#   bash scripts/rewire_pipeline_mcp.sh --ontology ontosynthesis --tree candidate
#   bash scripts/rewire_pipeline_mcp.sh --ontology ontosynthesis --tree production
#   bash scripts/rewire_pipeline_mcp.sh --ontology ontosynthesis --tree candidate --update-meta-task
#
# Notes:
# - Updates configs/run_created_mcp.json by default (backs up the file first).
# - Uses python to edit JSON safely.

python scripts/rewire_pipeline_mcp.py "$@"


