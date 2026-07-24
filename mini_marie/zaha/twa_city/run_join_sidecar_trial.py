"""Trial run: JOIN workflow offline replay with NDJSON sidecar."""

from __future__ import annotations

import json
import time
from pathlib import Path

from mini_marie.zaha.twa_city.workflow_engine import load_run
from mini_marie.zaha.twa_city.workflow_mcp import replay_workflow_offline
from mini_marie.zaha.twa_city.workflow_sidecar import count_ndjson_rows

RUNS = Path(__file__).resolve().parent / "workflow_runs"


def verify_existing(run_id: str = "1781176751") -> int:
    matches = sorted(RUNS.glob(f"WF_TOP10_LOCATIONS_JOIN_offline_{run_id}.json"))
    if not matches:
        matches = sorted(RUNS.glob("WF_TOP10_LOCATIONS_JOIN_offline_*.json"))
    if not matches:
        print("No JOIN offline recording found.")
        return 1
    rec_path = matches[-1]
    rec = load_run(rec_path)
    print("=== verify existing JOIN sidecar run ===")
    print(f"recording: {rec_path.name}")
    print(f"status: {rec.get('status')}")
    print(f"rows_on_disk: {rec.get('rows_on_disk')}")
    print(f"json manifest: {rec_path.stat().st_size / 1024:.1f} KB")
    arts = (rec.get("sidecar") or {}).get("artifacts") or []
    print(f"sidecar artifacts: {len(arts)}")
    for art in arts:
        path = Path(art["path"])
        if not path.exists():
            print(f"  MISSING {path.name}")
            continue
        mb = path.stat().st_size / (1024 * 1024)
        lines = count_ndjson_rows(path)
        label = art.get("name") or art.get("tool") or art.get("step")
        print(f"  [{art['kind']}] {label}: {lines:,} rows, {mb:.1f} MB")
    return 0 if rec.get("status") == "pass" else 1


def main() -> int:
    import sys

    if "--verify" in sys.argv:
        return verify_existing()
    online_files = sorted(RUNS.glob("WF_TOP10_LOCATIONS_JOIN_online_*.json"))
    if not online_files:
        print("No JOIN online recording found.")
        return 1
    online = online_files[-1]
    print("=== JOIN offline sidecar trial ===")
    print(f"online recording: {online.name} ({online.stat().st_size / 1024:.1f} KB)")

    t0 = time.perf_counter()
    tsv = replay_workflow_offline(str(online.resolve()))
    elapsed = time.perf_counter() - t0

    print(tsv.splitlines()[0])
    rec_path = next(
        line.split("\t", 1)[1]
        for line in tsv.splitlines()
        if line.startswith("recording_path\t")
    )
    rec = load_run(Path(rec_path))
    print(f"elapsed: {elapsed:.1f}s")
    print(f"json manifest: {Path(rec_path).stat().st_size / 1024:.1f} KB")
    print(f"rows_on_disk: {rec.get('rows_on_disk')}")
    print(f"workflow status: {rec.get('status')}")

    arts = (rec.get("sidecar") or {}).get("artifacts") or []
    print(f"sidecar artifacts: {len(arts)}")
    for art in arts:
        path = Path(art["path"])
        mb = path.stat().st_size / (1024 * 1024) if path.exists() else 0
        lines = count_ndjson_rows(path) if path.exists() else 0
        label = art.get("name") or art.get("tool") or art.get("step")
        print(f"  [{art['kind']}] {label}: {lines:,} rows, {mb:.1f} MB")

    join_art = next((a for a in arts if a.get("name") == "location_join_rows"), None)
    if join_art:
        with Path(join_art["path"]).open(encoding="utf-8") as handle:
            sample = json.loads(handle.readline())
        print("location_join_rows sample keys:", list(sample.keys())[:8])

    return 0 if rec.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
