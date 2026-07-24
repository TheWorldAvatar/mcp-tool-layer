#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.venv/bin/activate"
fi
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

pass=0
fail=0

check() {
  local name="$1"
  shift
  printf '%-42s' "${name} ..."
  if "$@"; then
    echo "OK"
    pass=$((pass + 1))
  else
    echo "FAIL"
    fail=$((fail + 1))
  fi
}

check "python import mini_marie" python -c "import mini_marie"
check "cache_paths repo_root" python -c "from mini_marie.cache_paths import repo_root; p=repo_root(); assert (p/'mini_marie').is_dir(), p"
check "merged_tll data present" test -d evaluation/data/merged_tll
check "configs present" test -f configs/mini_marie_mcps.json
check "models import" python -c "from models.BaseAgent import BaseAgent"
check "row_filters self-test" python -m mini_marie.test_row_filters
check "mof cache self-test" python -m mini_marie.mop_mof.mof.test_competency_cache
check "city cache self-test" python -m mini_marie.zaha.twa_city.test_city_cache
check "chemistry cache self-test" python -m mini_marie.marie.chemistry.test_chemistry_cache

echo
echo "Results: ${pass} passed, ${fail} failed"
if [[ "${fail}" -gt 0 ]]; then
  exit 1
fi
