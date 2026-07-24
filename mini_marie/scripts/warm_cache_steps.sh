#!/usr/bin/env bash
# Interactive, small-step cache warm-up helper (run from project root via this script).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.venv/bin/activate"
fi
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

pause_step() {
  echo
  echo "----"
  echo "$1"
  echo "Press Enter to continue (Ctrl+C to stop) ..."
  read -r _
}

step_mof() {
  echo "=== MOF competency cache (remote SPARQL) ==="
  pause_step "Step 1/3: Probe one workflow online (creates probe tier + recording)"
  python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ06_TOPOLOGY_ZIF8

  pause_step "Step 2/3: Warm missing atomics for one workflow only"
  python -m mini_marie.mop_mof.mof.warm_competency_cache \
    --workflow CQ06_TOPOLOGY_ZIF8 --missing-only

  pause_step "Step 3/3: Expand to full suite (missing only; may take a while)"
  python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive --missing-only

  echo "MOF warm steps finished. Offline replay (use recording path from step 1):"
  echo "  python -m mini_marie.mop_mof.mof.replay_competency_offline --recording <path-to-online-json>"
}

step_city() {
  echo "=== TWA city cache (Bremen + Kaiserslautern) ==="
  pause_step "Step 0: Check current cache status"
  python -m mini_marie.zaha.twa_city.warm_city_cache --status

  pause_step "Step 1/4: Atomics only — Bremen (fast)"
  python -m mini_marie.zaha.twa_city.warm_city_cache \
    --city bremen --atomics-only --missing-only

  pause_step "Step 2/4: Atomics only — Kaiserslautern"
  python -m mini_marie.zaha.twa_city.warm_city_cache \
    --city kaiserslautern --atomics-only --missing-only

  pause_step "Step 3/4: WKT locations for top 50 buildings — Bremen (minutes)"
  python -m mini_marie.zaha.twa_city.warm_city_cache \
    --city bremen --locations-only --locations-top-n 50 --missing-only

  pause_step "Step 4/4: Status check"
  python -m mini_marie.zaha.twa_city.city_cache_status --city bremen

  echo "City warm steps finished. Optional full comprehensive warm (hours):"
  echo "  python -m mini_marie.zaha.twa_city.warm_city_cache --comprehensive --missing-only"
}

step_chemistry() {
  echo "=== Chemistry cache (Blazegraph; batched) ==="
  pause_step "Step 0: Cache status"
  python -m mini_marie.marie.chemistry.cache_status

  pause_step "Step 1/4: Probe tier only (small LIMIT; safe first touch)"
  python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --probe-only

  pause_step "Step 2/4: Full tier — batch of 5 atomics (resume-friendly)"
  python -m mini_marie.marie.chemistry.warm_chemistry_cache \
    --comprehensive --missing-only --batch 5

  pause_step "Step 3/4: Repeat batch until coverage improves (run manually as needed)"
  echo "  python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --missing-only --batch 5"
  echo "  python -m mini_marie.marie.chemistry.cache_status --coverage"

  pause_step "Step 4/4: Optional corpus — species names status"
  python -m mini_marie.marie.chemistry.warm_species_corpus --status

  echo "Chemistry corpus warm (large; run separately when needed):"
  echo "  python -m mini_marie.marie.chemistry.warm_species_corpus --max-batches 1 --delay 3"
}

step_sg() {
  echo "=== Singapore sg-old Ontop cache ==="
  pause_step "Step 1/3: Names only (small first batch)"
  python -m mini_marie.zaha.sg_old.warm_ontop_cache --names-only --page-size 500

  pause_step "Step 2/3: Building rows (paginated; default page-size 3000)"
  python -m mini_marie.zaha.sg_old.warm_ontop_cache --page-size 3000

  pause_step "Step 3/3: Check via MCP tool or SQLite under data/mini_marie_cache/sg_old/"
  echo "  python -c \"from mini_marie.zaha.sg_old import ontop_operations as o; print(o.get_sg_ontop_cache_status())\""
}

step_mops() {
  echo "=== MOP synthesis (local RDF; no remote warm) ==="
  echo "Label index is built on first MCP query from evaluation/data/merged_tll"
  pause_step "Step 1/1: Trigger index build"
  python -c "from mini_marie.mop_mof.mops.twa_operations import ensure_twa_loaded; g=ensure_twa_loaded(); print(f'triples={len(g)}')"
  echo "Index file: data/mini_marie_cache/twa_label_index.json"
}

menu() {
  cat <<EOF
Cache warm helper — small steps only.

Domains:
  mof        MOF competency SQLite (remote)
  city       Bremen/KL city SQLite (remote)
  chemistry  Blazegraph atomics + corpus (remote, batched)
  sg         Singapore Ontop SQLite (remote, paginated)
  mops       Local merged_tll label index (no network)
  all        Run mof → city → chemistry → sg → mops (long)

See mini_marie/docs/CACHE_STARTUP.md
EOF
}

DOMAIN="${1:-}"
case "${DOMAIN}" in
  mof) step_mof ;;
  city) step_city ;;
  chemistry) step_chemistry ;;
  sg) step_sg ;;
  mops) step_mops ;;
  all)
    step_mof
    step_city
    step_chemistry
    step_sg
    step_mops
    ;;
  ""|help|-h|--help)
    menu
    ;;
  *)
    echo "Unknown domain: ${DOMAIN}" >&2
    menu
    exit 1
    ;;
esac
