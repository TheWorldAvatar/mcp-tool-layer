"""
Offline CQ_MORE03: NIST + CoRE + Tobassco via cached atomics + multi_join_rows.

Requires full-tier cache for the three atomic tools (warm first).
"""

from __future__ import annotations

import sys

from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, db_path
from mini_marie.mop_mof.mof.competency_workflow_engine import load_workflow, run_competency_workflow

REQUIRED_TOOLS = (
    "get_nist_exp_adsorption_rows",
    "get_core_name_chemistry_rows",
    "get_tobassco_func_groups_by_mofid",
)


def main() -> int:
    wf = load_workflow("CQ_MORE03_NIST_ADSORPTION_CHEM")
    cache = CompetencyCache()
    try:
        missing = [
            t for t in REQUIRED_TOOLS if not cache.has_full(t, {})
        ]
        if missing:
            print(f"SKIP: missing full cache for {missing} ({db_path()})")
            print("Warm: python -m mini_marie.mop_mof.mof.warm_competency_cache --workflow CQ_MORE03_NIST_ADSORPTION_CHEM")
            return 0
    finally:
        cache.close()

    result = run_competency_workflow(wf, mode="offline", use_cache=True)
    trace = result.get("call_trace") or []
    mj = next((s for s in trace if s.get("transform") == "multi_join_rows"), None)
    rows = (result.get("variables") or {}).get("result_rows") or []

    print(
        f"status={result['status']} multi_join_rows={mj and mj.get('row_count')} "
        f"result_rows={len(rows)}"
    )
    if mj:
        chain = mj.get("join_chain") or []
        print(f"  chain: {chain!r}")
    if result["status"] != "pass" or not rows:
        return 1
    sample = rows[0]
    print(f"  sample keys: {sorted(sample.keys())[:12]}...")
    print("CQ_MORE03 OFFLINE MULTI_JOIN OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
