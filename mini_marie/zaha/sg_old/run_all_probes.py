"""Run all sg-old probe passes and write a combined summary."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root

OUT = mini_marie_cache_root() / "sg_old" / "probe_run_summary.json"

STEPS = [
    ("probe_and_cache", ["python", "-m", "mini_marie.zaha.sg_old.probe_and_cache", "--skip-download"]),
    ("probe_ontop_deep", ["python", "-m", "mini_marie.zaha.sg_old.probe_ontop_deep"]),
    ("probe_gap_questions", ["python", "-m", "mini_marie.zaha.sg_old.probe_gap_questions"]),
    ("probe_sg_old_expand", ["python", "-m", "mini_marie.zaha.sg_old.probe_sg_old_expand"]),
    ("probe_kb_numerics", ["python", "-m", "mini_marie.zaha.sg_old.probe_kb_numerics"]),
    ("probe_kb_dispersion", ["python", "-m", "mini_marie.zaha.sg_old.probe_kb_dispersion"]),
    ("probe_round3", ["python", "-m", "mini_marie.zaha.sg_old.probe_round3"]),
    ("probe_value_gaps", ["python", "-m", "mini_marie.zaha.sg_old.probe_value_gaps"]),
    ("probe_round4", ["python", "-m", "mini_marie.zaha.sg_old.probe_round4"]),
]


def main() -> int:
    results = []
    t0 = time.perf_counter()
    for name, cmd in STEPS:
        print(f"\n>>> {name}", flush=True)
        t1 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        results.append(
            {
                "step": name,
                "exit_code": proc.returncode,
                "seconds": round(time.perf_counter() - t1, 1),
                "stdout_tail": proc.stdout[-1500:] if proc.stdout else "",
                "stderr_tail": proc.stderr[-500:] if proc.stderr else "",
            }
        )
        if proc.returncode != 0:
            print(f"WARN {name} exit {proc.returncode}", flush=True)
    summary = {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_seconds": round(time.perf_counter() - t0, 1),
        "steps": results,
        "artifacts": [
            "ontop_deep_probe.json",
            "gap_probe.json",
            "expand_probe.json",
            "kb_numerics_probe.json",
            "kb_dispersion_probe.json",
            "probe_round3.json",
            "value_gaps_probe.json",
            "probe_round4.json",
            "data_existence_report.json",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 0 if all(s["exit_code"] == 0 for s in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
