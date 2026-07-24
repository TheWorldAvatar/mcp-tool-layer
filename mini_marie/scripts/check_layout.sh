#!/usr/bin/env bash
# Verify mini_marie project layout (no Python required).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${ROOT}"

missing=0
require() {
  if [[ ! -e "$1" ]]; then
    echo "MISSING: $1" >&2
    missing=$((missing + 1))
  else
    echo "OK: $1"
  fi
}

require mini_marie/__init__.py
require mini_marie/cache_paths.py
require mini_marie/docs/DEPLOYMENT.md
require mini_marie/docs/CACHE_STARTUP.md
require configs/mini_marie_mcps.json
require configs/mof_twa.json
require configs/marie_twa.json
require configs/twa_city.json
require configs/sg_old.json
require configs/kg_catalog.json
require models/BaseAgent.py
require src/utils/global_logger.py
require evaluation/data/merged_tll
require Dockerfile
require docker-compose.yml
require docker/entrypoint.sh
require requirements-mini-marie.txt

if [[ "${missing}" -gt 0 ]]; then
  echo "${missing} required path(s) missing" >&2
  exit 1
fi
echo "Layout check passed."
