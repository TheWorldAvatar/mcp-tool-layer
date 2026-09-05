"""Queue-based parallel pipeline launcher.

Workers pull one job at a time from a shared queue. Extraction jobs are
taken first so ``--workers N`` keeps N papers extracting; a finished
extraction is replaced immediately. KG jobs run only when no extraction
is waiting. This replaces the older one-paper-through-KG slot that left
later papers' extraction queued behind other papers' KG.

Example:
    python scripts/run_eval_queue.py \\
        --config scenarios/mops/runs/NEW_RUN/pipeline.resolved.json \\
        --workers 6 \\
        --until main_ontology_extractions \\
        --extraction-iterations 2,3 \\
        --skip-iter4-extraction \\
        --test \\
        --order longest-first \\
        --prior-runtime scenarios/mops/runs/20260819_eval30_steptype-seq30/runtime \\
        --score
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from src.pipelines.utils.machine_paths import (
    configured_runtime_root,
    link_run_dir_runtime,
    resolve_scenario_runtime,
    write_runtime_origin,
)
from src.pipelines.utils.process_watchdog import (
    STALL_EXIT_CODE,
    is_stalled,
    latest_log_mtime,
    wait_process_with_stall,
)

KNOWN_STEPS = (
    "pdf_conversion",
    "section_classification",
    "stitching",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
    "main_kg_building",
    "extensions_extractions",
    "extensions_kg_building",
    "mop_derivation",
)
EXTRACT_PHASE_UNTIL = "main_ontology_extractions"


def truncate_steps(steps: list[str], until: str | None) -> list[str]:
    """Keep configured steps through ``until`` inclusive."""
    if not until:
        return list(steps)
    if until not in KNOWN_STEPS:
        raise ValueError(f"unknown --until step: {until}")
    limit = KNOWN_STEPS.index(until)
    allowed = set(KNOWN_STEPS[: limit + 1])
    truncated = [step for step in steps if step in allowed]
    if not truncated:
        raise ValueError(f"--until {until} removed every configured step")
    return truncated


def split_extract_rest_steps(steps: list[str]) -> tuple[list[str], list[str]] | None:
    """Split a full campaign into main-extraction then the remaining KG/extension steps."""
    if EXTRACT_PHASE_UNTIL not in steps:
        return None
    index = steps.index(EXTRACT_PHASE_UNTIL)
    rest = steps[index + 1 :]
    if not rest:
        return None
    return steps[: index + 1], rest


def parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def load_hash_mapping(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def count_entities(paper_dir: Path) -> int:
    pre = paper_dir / "pre_extraction"
    if pre.is_dir():
        return len(list(pre.glob("entity_text_*.txt")))
    top = paper_dir / "mcp_run" / "iter1_top_entities.json"
    if not top.is_file():
        return 0
    try:
        payload = json.loads(top.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("entities", "top_entities", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def load_prior_elapsed(prior_timings: Path | None, prior_runtime: Path | None) -> dict[str, float]:
    candidates: list[Path] = []
    if prior_timings:
        candidates.append(prior_timings)
    if prior_runtime:
        candidates.append(prior_runtime.parent / "launcher" / "summary.json")
        candidates.append(prior_runtime.parent / "parallel_summary.json")
    for path in candidates:
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        papers = payload.get("papers") or payload.get("paper_elapsed") or {}
        if isinstance(papers, dict):
            elapsed: dict[str, float] = {}
            for key, value in papers.items():
                if isinstance(value, dict) and "elapsed_seconds" in value:
                    elapsed[str(key)] = float(value["elapsed_seconds"])
                elif isinstance(value, (int, float)):
                    elapsed[str(key)] = float(value)
            if elapsed:
                return elapsed
    return {}


def paper_weight(
    doi_hash: str,
    *,
    prior_elapsed: dict[str, float],
    prior_runtime: Path | None,
    current_runtime: Path,
) -> tuple[float, str]:
    if doi_hash in prior_elapsed:
        return prior_elapsed[doi_hash], "prior_elapsed"
    for root in (prior_runtime, current_runtime):
        if root is None:
            continue
        entities = count_entities(root / doi_hash)
        if entities:
            return float(entities), "entity_count"
    return 0.0, "fallback"


def order_hashes(
    hashes: list[str],
    *,
    mode: str,
    prior_elapsed: dict[str, float],
    prior_runtime: Path | None,
    current_runtime: Path,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for doi_hash in hashes:
        weight, source = paper_weight(
            doi_hash,
            prior_elapsed=prior_elapsed,
            prior_runtime=prior_runtime,
            current_runtime=current_runtime,
        )
        ranked.append(
            {"hash": doi_hash, "weight": weight, "weight_source": source}
        )
    if mode == "longest-first":
        ranked.sort(key=lambda row: (-row["weight"], row["hash"]))
    return ranked


def summarize_costs(cost_log: Path) -> dict[str, Any]:
    total = 0.0
    calls = 0
    models: Counter[str] = Counter()
    if cost_log.is_file():
        for line in cost_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") != "completed":
                continue
            calls += 1
            total += float(row.get("actual_cost_usd") or 0.0)
            models[str(row.get("model") or "unknown")] += 1
    return {
        "calls": calls,
        "actual_cost_usd": round(total, 6),
        "models": dict(models),
        "source": str(cost_log).replace("\\", "/"),
    }


def collect_paper_index(runtime: Path, hashes: list[str]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for doi_hash in hashes:
        paper = runtime / doi_hash
        mcp = paper / "mcp_run"
        output = paper / "ontosynthesis_output"
        index[doi_hash] = {
            "entities": count_entities(paper),
            "done_main_ontology": (paper / ".main_ontology_extractions_done").is_file(),
            "done_main_kg": (paper / ".main_kg_building_done").is_file(),
            "iter2_hints": len(list(mcp.glob("iter2_hints_*.txt"))) if mcp.is_dir() else 0,
            "iter3_hints": len(list(mcp.glob("iter3_hints_*.txt"))) if mcp.is_dir() else 0,
            "iter4_hints": len(list(mcp.glob("iter4_hints_*.txt"))) if mcp.is_dir() else 0,
            "entity_ttls": (
                len([p for p in output.glob("*.ttl") if p.name.lower() != "top.ttl"])
                if output.is_dir()
                else 0
            ),
            "done_extensions": (paper / ".extensions_extractions_done").is_file(),
            "done_extensions_kg": (paper / ".extensions_kg_building_done").is_file(),
            "done_mop_derivation": (paper / ".mop_derivation_done").is_file(),
        }
    return index


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def resolve_hashes(
    *,
    runtime: Path,
    seed_from: Path | None,
    requested: list[str],
) -> list[str]:
    if requested:
        return list(dict.fromkeys(requested))
    mapping = load_hash_mapping(runtime / "doi_to_hash.json")
    if not mapping and seed_from is not None:
        mapping = load_hash_mapping(seed_from / "doi_to_hash.json")
    hashes = list(mapping.values())
    if not hashes:
        raise ValueError(
            "no DOI hashes found; pass --hash, or provide runtime/doi_to_hash.json"
        )
    return hashes


def seed_runtime(seed_from: Path, runtime: Path, hashes: list[str]) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    mapping = load_hash_mapping(seed_from / "doi_to_hash.json")
    if mapping:
        filtered = {doi: value for doi, value in mapping.items() if value in set(hashes)}
        write_json(runtime / "doi_to_hash.json", filtered or mapping)
    for doi_hash in hashes:
        source = seed_from / doi_hash
        target = runtime / doi_hash
        if not source.is_dir() or target.exists():
            continue
        shutil.copytree(source, target)


def drop_iteration_artifacts(runtime: Path, hashes: list[str], iterations: list[int]) -> None:
    prefixes = tuple(f"iter{num}_" for num in iterations)
    drop_dirs = set()
    if 3 in iterations:
        drop_dirs.update({"pre_extraction", "procedure_inheritance"})
    for doi_hash in hashes:
        paper = runtime / doi_hash
        if not paper.is_dir():
            continue
        (paper / ".main_ontology_extractions_done").unlink(missing_ok=True)
        for name in drop_dirs:
            path = paper / name
            if path.exists():
                shutil.rmtree(path)
        mcp = paper / "mcp_run"
        if mcp.is_dir():
            for path in mcp.iterdir():
                if path.name.startswith(prefixes):
                    path.unlink()
        for folder in ("prompts", "responses"):
            base = paper / folder
            if not base.is_dir():
                continue
            for child in list(base.iterdir()):
                if any(child.name.startswith(f"iter{num}_") for num in iterations):
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()


def build_worker_config(
    *,
    base_config: dict[str, Any],
    run_dir: Path,
    runtime: Path,
    steps: list[str],
    extraction_iterations: list[int],
    skip_iter4: bool,
    config_name: str = "pipeline.queue.json",
) -> Path:
    config = dict(base_config)
    config["data_dir"] = str(runtime).replace("\\", "/")
    config["steps"] = steps
    if extraction_iterations:
        config["only_extraction_iterations"] = extraction_iterations
    if skip_iter4:
        config["skip_iter4_extraction"] = True
    scenario = dict(config.get("scenario") or {})
    scenario.setdefault("run_id", run_dir.name)
    scenario["evaluation_dir"] = str((run_dir / "evaluation").resolve()).replace("\\", "/")
    config["scenario"] = scenario
    path = run_dir / config_name
    write_json(path, config)
    return path


def append_jsonl(path: Path, row: dict[str, Any], lock: threading.Lock) -> None:
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def run_one_paper(
    *,
    repo: Path,
    python: str,
    config_path: Path,
    doi_hash: str,
    worker_id: int,
    log_dir: Path,
    extra_args: list[str],
    env: dict[str, str],
    stall_seconds: float = 900,
    max_paper_seconds: float = 3600,
) -> dict[str, Any]:
    log = log_dir / f"w{worker_id:02d}.log"
    err = log_dir / f"w{worker_id:02d}.err.log"
    cmd = [
        python,
        "generic_main.py",
        "--config",
        str(config_path),
        "--hash",
        doi_hash,
        "--resume-existing-runtime",
        *extra_args,
    ]
    started = time.time()
    with log.open("a", encoding="utf-8") as stdout, err.open("a", encoding="utf-8") as stderr:
        stdout.write(f"\n===== START {doi_hash} =====\n")
        stdout.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo),
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        code = wait_process_with_stall(
            proc,
            log_paths=[log, err],
            started=started,
            stall_seconds=stall_seconds,
            max_seconds=max_paper_seconds,
            poll_seconds=15 if stall_seconds >= 60 else 0.05,
        )
        if code == STALL_EXIT_CODE:
            stdout.write(
                f"===== STALL {doi_hash} killed "
                f"(no log for {stall_seconds:.0f}s or max {max_paper_seconds:.0f}s) =====\n"
            )
        stdout.write(f"===== END {doi_hash} exit={code} =====\n")
    return {
        "hash": doi_hash,
        "worker": worker_id,
        "exit_code": code,
        "elapsed_seconds": round(time.time() - started, 3),
        "stalled": code == STALL_EXIT_CODE,
    }


def score_run(repo: Path, runtime: Path, output: Path, python: str) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            python,
            "scripts/score_extraction_hints_against_gt.py",
            "--run-root",
            str(runtime),
            "--output",
            str(output),
        ],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
    )
    totals: dict[str, Any] = {}
    if output.is_file():
        totals = json.loads(output.read_text(encoding="utf-8")).get("totals") or {}
    return {
        "exit_code": proc.returncode,
        "output": str(output).replace("\\", "/"),
        "totals": {
            key: totals.get(key)
            for key in ("type_tp", "type_fp", "type_fn", "type_metrics")
            if key in totals
        },
        "stderr": (proc.stderr or "")[-2000:],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the DOI pipeline from a shared one-paper queue."
    )
    parser.add_argument("--config", required=True, type=Path, help="Pipeline JSON")
    parser.add_argument("--run-dir", type=Path, help="Override run directory (default: config parent)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--until", choices=KNOWN_STEPS, help="Stop after this step")
    parser.add_argument("--extraction-iterations", help="Comma list, e.g. 2,3")
    parser.add_argument("--skip-iter2-extraction", action="store_true")
    parser.add_argument("--skip-iter3-extraction", action="store_true")
    parser.add_argument("--skip-iter4-extraction", action="store_true")
    parser.add_argument("--test", action="store_true", help="Use generated MCP tools")
    parser.add_argument("--order", choices=("source", "longest-first"), default="longest-first")
    parser.add_argument("--prior-runtime", type=Path, help="Earlier runtime used to rank long papers first")
    parser.add_argument("--prior-timings", type=Path, help="Earlier launcher/summary.json")
    parser.add_argument("--seed-from", type=Path, help="Copy selected hash folders into this runtime")
    parser.add_argument(
        "--drop-iter-artifacts",
        help="After seeding, drop iterN hints/responses, e.g. 2,3",
    )
    parser.add_argument("--hash", action="append", dest="hashes", default=[])
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument(
        "--stall-seconds",
        type=float,
        default=900,
        help="Kill a paper and take the next one after this many seconds with no log output",
    )
    parser.add_argument(
        "--max-paper-seconds",
        type=float,
        default=3600,
        help="Hard wall-clock limit per paper; 0 disables",
    )
    parser.add_argument(
        "--max-case-cost-usd",
        type=float,
        default=10.0,
        help=(
            "Stop new LLM calls in each paper process after this actual-cost cap; "
            "0 disables (default: 10 USD)"
        ),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=0,
        help="Cap including fill-in workers (default: workers + 4)",
    )
    parser.add_argument(
        "--extract-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Keep --workers papers extracting; when one main extraction finishes, "
            "immediately start the next paper. KG waits until no extraction is queued. "
            "Use --no-extract-first to hold a slot through KG (old behavior)."
        ),
    )
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args(argv)


def should_spawn_fillin(
    *,
    queued: int,
    in_progress: list[dict[str, Any]],
    now: float,
    stall_seconds: float,
    target_workers: int,
    max_workers: int,
    current_worker_count: int,
) -> bool:
    """Spawn another worker when a stall is holding a slot and work remains."""
    if queued <= 0 or current_worker_count >= max_workers:
        return False
    live = sum(
        1
        for item in in_progress
        if not is_stalled(
            started=float(item["started"]),
            last_log=item.get("last_log"),
            now=now,
            stall_seconds=stall_seconds,
        )
    )
    return live < target_workers


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    config_path = args.config if args.config.is_absolute() else repo / args.config
    base_config = json.loads(config_path.read_text(encoding="utf-8"))
    run_dir = args.run_dir or config_path.parent
    run_dir = run_dir if run_dir.is_absolute() else repo / run_dir
    run_id = str((base_config.get("scenario") or {}).get("run_id") or run_dir.name)
    runtime = resolve_scenario_runtime(
        repo=repo,
        run_dir=run_dir,
        run_id=run_id,
        configured_data_dir=base_config.get("data_dir") or (run_dir / "runtime"),
    )
    machine_root = configured_runtime_root(repo)
    if machine_root is not None:
        runtime.mkdir(parents=True, exist_ok=True)
        write_runtime_origin(run_dir=run_dir, runtime=runtime, runtime_root=machine_root)
        link_run_dir_runtime(run_dir, runtime)
        print(
            f"[INFO] Runtime root: {machine_root} -> {runtime}",
            flush=True,
        )
    launcher = run_dir / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)

    steps = truncate_steps(list(base_config.get("steps") or KNOWN_STEPS), args.until)
    extraction_iterations = parse_int_list(args.extraction_iterations)
    drop_iters = parse_int_list(args.drop_iter_artifacts)
    hashes = resolve_hashes(
        runtime=runtime,
        seed_from=args.seed_from,
        requested=args.hashes,
    )
    prior_elapsed = load_prior_elapsed(args.prior_timings, args.prior_runtime)
    ranked = order_hashes(
        hashes,
        mode=args.order,
        prior_elapsed=prior_elapsed,
        prior_runtime=args.prior_runtime,
        current_runtime=runtime,
    )
    target_workers = max(1, args.workers)
    max_workers = args.max_workers if args.max_workers > 0 else target_workers + 4
    stall_seconds = float(args.stall_seconds)
    max_paper_seconds = float(args.max_paper_seconds)
    phases = split_extract_rest_steps(steps) if args.extract_first else None
    plan = {
        "workers": target_workers,
        "max_workers": max_workers,
        "extract_first": bool(phases),
        "extract_steps": (phases[0] if phases else steps),
        "rest_steps": (phases[1] if phases else []),
        "stall_seconds": stall_seconds,
        "max_paper_seconds": max_paper_seconds,
        "until": args.until or steps[-1],
        "steps": steps,
        "order": args.order,
        "hashes": [row["hash"] for row in ranked],
        "ranking": ranked,
        "runtime": str(runtime).replace("\\", "/"),
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    if args.seed_from:
        seed_runtime(args.seed_from, runtime, hashes)
        if drop_iters:
            drop_iteration_artifacts(runtime, hashes, drop_iters)
    extract_steps = phases[0] if phases else steps
    rest_steps = phases[1] if phases else []
    queue_config = build_worker_config(
        base_config=base_config,
        run_dir=run_dir,
        runtime=runtime,
        steps=extract_steps if phases else steps,
        extraction_iterations=extraction_iterations,
        skip_iter4=args.skip_iter4_extraction,
        config_name="pipeline.queue.extract.json" if phases else "pipeline.queue.json",
    )
    rest_config = (
        build_worker_config(
            base_config=base_config,
            run_dir=run_dir,
            runtime=runtime,
            steps=rest_steps,
            extraction_iterations=extraction_iterations,
            skip_iter4=args.skip_iter4_extraction,
            config_name="pipeline.queue.rest.json",
        )
        if phases
        else queue_config
    )
    plan["config"] = str(queue_config).replace("\\", "/")
    if phases:
        plan["rest_config"] = str(rest_config).replace("\\", "/")
    write_json(launcher / "plan.json", plan)

    extra_args: list[str] = []
    if args.test:
        extra_args.append("--test")
    if args.until == "top_entity_kg_building" or steps[-1] == "top_entity_kg_building":
        extra_args.append("--iter1")
    if args.skip_iter2_extraction:
        extra_args.append("--skip-iter2-extraction")
    if args.skip_iter3_extraction:
        extra_args.append("--skip-iter3-extraction")
    if args.skip_iter4_extraction:
        extra_args.append("--skip-iter4-extraction")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(repo)
    if args.artifact_root:
        env["TWA_GENERATED_ARTIFACT_ROOT"] = str(args.artifact_root.resolve())
        env["TWA_REQUIRE_GENERATED_ARTIFACT_ROOT"] = "1"

    jobs_path = launcher / "jobs.jsonl"
    if jobs_path.exists():
        jobs_path.unlink()
    lock = threading.Lock()
    papers: dict[str, Any] = {}
    in_progress: dict[str, dict[str, Any]] = {}
    extract_work: Queue[str] = Queue()
    rest_work: Queue[str] = Queue()
    for row in ranked:
        extract_work.put(row["hash"])

    def queued_count() -> int:
        return extract_work.qsize() + rest_work.qsize()

    def claim_job() -> tuple[str, str] | None:
        with lock:
            try:
                return "extract", extract_work.get_nowait()
            except Empty:
                pass
            try:
                return "rest", rest_work.get_nowait()
            except Empty:
                pass
            if in_progress or extract_work.qsize() or rest_work.qsize():
                return None
        return None

    def worker(worker_id: int) -> None:
        while True:
            claimed = claim_job()
            if claimed is None:
                with lock:
                    idle = not in_progress and extract_work.empty() and rest_work.empty()
                if idle:
                    return
                time.sleep(1)
                continue
            phase, doi_hash = claimed
            job_key = f"{doi_hash}:{phase}"
            log_paths = [
                launcher / f"w{worker_id:02d}.log",
                launcher / f"w{worker_id:02d}.err.log",
            ]
            with lock:
                in_progress[job_key] = {
                    "worker": worker_id,
                    "hash": doi_hash,
                    "phase": phase,
                    "started": time.time(),
                    "last_log": None,
                    "logs": log_paths,
                }
            append_jsonl(
                jobs_path,
                {
                    "event": "start",
                    "hash": doi_hash,
                    "phase": phase,
                    "worker": worker_id,
                    "ts": time.time(),
                },
                lock,
            )
            print(f"start w{worker_id:02d} phase={phase} hash={doi_hash}", flush=True)
            try:
                paper_env = dict(env)
                if args.max_case_cost_usd > 0:
                    paper_env["TWA_LLM_PROCESS_COST_CAP_USD"] = str(
                        args.max_case_cost_usd
                    )
                result = run_one_paper(
                    repo=repo,
                    python=args.python,
                    config_path=queue_config if phase == "extract" else rest_config,
                    doi_hash=doi_hash,
                    worker_id=worker_id,
                    log_dir=launcher,
                    extra_args=extra_args,
                    env=paper_env,
                    stall_seconds=stall_seconds,
                    max_paper_seconds=max_paper_seconds,
                )
            except Exception as exc:  # pragma: no cover - worker guard
                result = {
                    "hash": doi_hash,
                    "worker": worker_id,
                    "exit_code": 1,
                    "elapsed_seconds": 0.0,
                    "error": str(exc),
                }
            result = dict(result)
            result["phase"] = phase
            extract_ok = (runtime / doi_hash / ".main_ontology_extractions_done").is_file()
            with lock:
                prior = papers.get(doi_hash) or {}
                phases_done = dict(prior.get("phases") or {})
                phases_done[phase] = result
                papers[doi_hash] = {
                    **result,
                    "phases": phases_done,
                    "elapsed_seconds": sum(
                        float((row or {}).get("elapsed_seconds") or 0.0)
                        for row in phases_done.values()
                    ),
                }
                if phases and phase == "extract" and extract_ok:
                    rest_work.put(doi_hash)
                in_progress.pop(job_key, None)
            append_jsonl(jobs_path, {"event": "end", **result, "ts": time.time()}, lock)
            print(
                f"exit w{worker_id:02d} phase={phase} hash={doi_hash} "
                f"code={result['exit_code']} elapsed_s={result['elapsed_seconds']:.1f}",
                flush=True,
            )

    def refresh_progress() -> dict[str, dict[str, Any]]:
        with lock:
            for row in in_progress.values():
                row["last_log"] = latest_log_mtime(row["logs"])
            return {key: dict(row) for key, row in in_progress.items()}

    started = time.time()
    threads = [
        threading.Thread(target=worker, args=(index,), name=f"queue-w{index:02d}")
        for index in range(target_workers)
    ]
    next_worker_id = target_workers
    for thread in threads:
        thread.start()
    while True:
        alive = [thread for thread in threads if thread.is_alive()]
        queued = queued_count()
        if not alive and queued == 0:
            break
        snapshot = refresh_progress()
        if should_spawn_fillin(
            queued=queued,
            in_progress=list(snapshot.values()),
            now=time.time(),
            stall_seconds=stall_seconds,
            target_workers=target_workers,
            max_workers=max_workers,
            current_worker_count=len(alive),
        ):
            stalled = [
                row.get("hash") or key
                for key, row in snapshot.items()
                if is_stalled(
                    started=float(row["started"]),
                    last_log=row.get("last_log"),
                    now=time.time(),
                    stall_seconds=stall_seconds,
                )
            ]
            print(
                f"fill-in w{next_worker_id:02d} stalled={stalled} queued={queued}",
                flush=True,
            )
            append_jsonl(
                jobs_path,
                {
                    "event": "fill-in",
                    "worker": next_worker_id,
                    "stalled": stalled,
                    "queued": queued,
                    "ts": time.time(),
                },
                lock,
            )
            extra = threading.Thread(
                target=worker,
                args=(next_worker_id,),
                name=f"queue-w{next_worker_id:02d}",
            )
            threads.append(extra)
            extra.start()
            next_worker_id += 1
        time.sleep(15 if stall_seconds >= 60 else 0.05)
    for thread in threads:
        thread.join()
    elapsed = time.time() - started

    cost = summarize_costs(runtime / "reports" / "openrouter_costs.jsonl")
    index = collect_paper_index(runtime, [row["hash"] for row in ranked])
    for doi_hash, row in index.items():
        row.update(papers.get(doi_hash) or {})
    summary = {
        "workers": target_workers,
        "max_workers": max_workers,
        "stall_seconds": stall_seconds,
        "max_paper_seconds": max_paper_seconds,
        "max_case_cost_usd": args.max_case_cost_usd,
        "fill_in_workers": max(0, len(threads) - target_workers),
        "extract_first": bool(phases),
        "elapsed_seconds": round(elapsed, 3),
        "order": args.order,
        "until": args.until or steps[-1],
        "steps": steps,
        "exit_codes": {
            doi_hash: (papers.get(doi_hash) or {}).get("exit_code")
            for doi_hash in plan["hashes"]
        },
        "papers": index,
        "cost": cost,
        "idle_note": (
            "N workers stay on main extraction; a finished extraction is replaced "
            "immediately. KG runs when no extraction is waiting. A paper with no "
            "log activity is killed and that worker takes the next job."
        ),
    }
    write_json(launcher / "summary.json", summary)
    write_json(launcher / "cost_summary.json", cost)
    write_json(launcher / "paper_index.json", index)

    if args.score:
        summary["score"] = score_run(
            repo,
            runtime,
            run_dir / "evaluation" / "extraction_hint_gt_report.json",
            args.python,
        )
        write_json(launcher / "summary.json", summary)

    print(json.dumps({
        "elapsed_seconds": summary["elapsed_seconds"],
        "workers": summary["workers"],
        "papers": len(index),
        "cost": cost,
        "done": sum(
            1
            for row in index.values()
            if row.get(
                "done_main_kg"
                if (args.until or steps[-1]) == "main_kg_building"
                else "done_main_ontology"
            )
        ),
    }, indent=2))
    failed = [
        doi_hash
        for doi_hash, row in papers.items()
        if row.get("exit_code") not in (0, None)
    ]
    # Windows launcher shutdown can surface 0xC0000005 after a successful paper.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
