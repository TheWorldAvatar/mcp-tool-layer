#!/usr/bin/env bash
# Export a portable mini_marie tree (no duplicate folder in repo — output goes to dist/).
# Creates dist/mini_marie_portable/ ready to copy to another machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT="${1:-${PROJECT_ROOT}/dist/mini_marie_portable}"

echo "Project root: ${PROJECT_ROOT}"
echo "Export target: ${OUT}"

copy_tree() {
  local src="$1" dst="$2"
  mkdir -p "${dst}"
  rsync -a \
    --exclude '__pycache__' \
    --exclude '.ipynb_checkpoints' \
    --exclude 'competency_runs' \
    --exclude 'workflow_runs' \
    --exclude 'headless_runs' \
    --exclude '*.pyc' \
    "${src}/" "${dst}/"
}

rm -rf "${OUT}"
mkdir -p "${OUT}"

copy_tree "${PROJECT_ROOT}/mini_marie" "${OUT}/mini_marie"

mkdir -p "${OUT}/configs"
for cfg in mini_marie_mcps.json mof_twa.json marie_twa.json twa_city.json sg_old.json kg_catalog.json; do
  cp -f "${PROJECT_ROOT}/configs/${cfg}" "${OUT}/configs/${cfg}"
done

mkdir -p "${OUT}/models"
for model in BaseAgent.py ModelConfig.py MCPConfig.py LLMCreator.py TokenCalculator.py locations.py; do
  cp -f "${PROJECT_ROOT}/models/${model}" "${OUT}/models/${model}"
done
touch "${OUT}/models/__init__.py"

mkdir -p "${OUT}/src/utils"
cp -f "${PROJECT_ROOT}/src/utils/global_logger.py" "${OUT}/src/utils/global_logger.py"
touch "${OUT}/src/__init__.py" "${OUT}/src/utils/__init__.py"

copy_tree "${PROJECT_ROOT}/evaluation/data/merged_tll" "${OUT}/evaluation/data/merged_tll"

for f in requirements-mini-marie.txt requirements-docker.txt requirements-gui.txt requirements-kgqa.txt \
         Dockerfile docker-compose.yml Makefile .dockerignore .env.example; do
  cp -f "${PROJECT_ROOT}/${f}" "${OUT}/${f}"
done
copy_tree "${PROJECT_ROOT}/docker" "${OUT}/docker"
mkdir -p "${OUT}/data" "${OUT}/raw_data"
touch "${OUT}/data/.gitkeep" "${OUT}/raw_data/.gitkeep"

if [[ -f "${PROJECT_ROOT}/.streamlit/config.toml" ]]; then
  mkdir -p "${OUT}/.streamlit"
  cp -f "${PROJECT_ROOT}/.streamlit/config.toml" "${OUT}/.streamlit/config.toml"
fi

echo "Portable tree written to ${OUT}"
echo "On target machine: cd ${OUT} && ./mini_marie/scripts/setup_linux.sh"
