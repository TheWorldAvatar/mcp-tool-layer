"""One-click locked 1:1 Pipeline + OntoLogX runner.

Extraction runtimes are not in git. This script unpacks eval PDFs and frozen
MCP packs when the zip files are present, then extract → Pipeline KG → OX
for one protocol. Re-running the same pack/protocol/hashes reuses the last
campaign run when markers are complete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_inputs import (
    assert_frozen_extraction_prompts,
    check_inputs,
    chemistry_pack_path,
    mcp_pack_ready,
    medical_pack_path,
    missing_required_pdfs,
    print_check,
    unpack_if_present,
    write_expected_manifest,
)
from ship_lib import (
    DEFAULT_WORKERS,
    MEDICAL_GOLD,
    MEDICAL_SCHEMA,
    assert_locked_runtime,
    chemistry_score_table,
    child_env,
    command_succeeded,
    configure_stdio,
    ensure_scorer_repo,
    format_locked_runtime_line,
    hash_cli,
    latest_scenario_run,
    locked_runtime_mismatches,
    marker_complete,
    python_executable,
    repo_root,
    required_env_keys,
    run_logged,
    select_eval_cases,
)

# EXTRACT_DONE / KG_DONE match run_default_pipeline markers.
EXTRACT_DONE = ".main_ontology_extractions_done"
KG_DONE = ".main_kg_building_done"

# Official Sep 8 extract put two hashes in one process and ran them
# sequentially (in-process ThreadPool did not exist yet). Pipeline KG and
# extract stay one paper per process so each child keeps its own event loop.
# OntoLogX must follow official s1xha–o: two hashes per process, official
# letter pairing, then merge + score.
EXTRACT_GROUP_SIZE = 1
KG_GROUP_SIZE = 1
OX_GROUP_SIZE = 2

# Official s1–s4 letter pairing from run_fullpack_s1s2_rest20.ALL_GROUPS.
# Do not slice papers_eval30.json in list order; that is a different grouping.
_OFFICIAL5 = (
    "0c57bac8",
    "7ba809dd",
    "d5ff239e",
    "a014d993",
    "50307a45",
)
_ALL30_ALPHA = [
    "0c57bac8",
    "0e299eb4",
    "178ef569",
    "1b9180ec",
    "3a4646d4",
    "3f239659",
    "49613153",
    "4f7936b0",
    "50307a45",
    "5175f0fe",
    "5541fe0c",
    "736dc58b",
    "73a6d32b",
    "7ba809dd",
    "7fa3bf7d",
    "88c21a74",
    "93aab3a3",
    "9b4389c6",
    "9e93418f",
    "a014d993",
    "a527729b",
    "aaf9ce20",
    "b0046ae2",
    "b2490447",
    "b284c4ea",
    "bb5d60c7",
    "c66a0a79",
    "d5ff239e",
    "dc2e2fef",
    "f4f7330e",
]


def _official_s1_letter_groups() -> dict[str, tuple[str, ...]]:
    case10 = tuple(_OFFICIAL5 + tuple(item for item in _ALL30_ALPHA if item not in _OFFICIAL5)[:5])
    rest20 = tuple(item for item in _ALL30_ALPHA if item not in case10)
    groups = {
        "a": case10[0:2],
        "b": case10[2:4],
        "c": case10[4:6],
        "d": case10[6:8],
        "e": case10[8:10],
    }
    for index, letter in enumerate("fghijklmno"):
        groups[letter] = rest20[index * 2 : index * 2 + 2]
    return groups


S1_LETTER_GROUPS = _official_s1_letter_groups()

LOCKED_CAMPAIGN = "locked_campaign.json"
DEFAULT_KG_MODEL = "openai/gpt-4o-2024-11-20"
KIMI_MODEL = "moonshotai/kimi-k3"

# Paper-facing guidance names. Engine ids stay generic-strict / generic-noprompt /
# with-prompt because that is what extraction_runtime and OntoLogX still take.
GUIDANCE = {
    "minimal": {
        "engine": "generic-strict",
        "label": "Minimal",
        "blurb": "occurrence and ownership instructions only",
    },
    "graph-rules": {
        "engine": "generic-noprompt",
        "label": "Graph rules",
        "blurb": "those plus explicit construction recipes and the T-Box handbook",
    },
    "kg-guidance": {
        "engine": "with-prompt",
        "label": "KG guidance",
        "blurb": (
            "those plus the frozen human-engineered graph-construction "
            "guidance and the same T-Box handbook"
        ),
    },
}
GUIDANCE_ALIASES = {
    "minimal": "minimal",
    "min": "minimal",
    "generic-strict": "minimal",
    "graph-rules": "graph-rules",
    "graph-rule": "graph-rules",
    "generic-noprompt": "graph-rules",
    "kg-guidance": "kg-guidance",
    "kg-guide": "kg-guidance",
    "with-prompt": "kg-guidance",
}
LOCKED_PROTOCOLS = tuple(GUIDANCE.keys())

KG_MODEL_ALIASES = {
    "gpt-4o": DEFAULT_KG_MODEL,
    "gpt4o": DEFAULT_KG_MODEL,
    "kimi": KIMI_MODEL,
    "kimi-k3": KIMI_MODEL,
}
EXTRACT_MODEL_ALIASES = {
    "gpt-4.1": "gpt-4.1-2025-04-14",
    "gpt4.1": "gpt-4.1-2025-04-14",
    "gpt-5": "gpt-5-2025-08-07",
    "gpt5": "gpt-5-2025-08-07",
    "kimi": KIMI_MODEL,
    "kimi-k3": KIMI_MODEL,
}
PROTOCOL_TAG = {
    "minimal": "min",
    "graph-rules": "gr",
    "kg-guidance": "kg",
}


def _alias(value: str | None, table: dict[str, str], default: str | None = None) -> str | None:
    if value is None or not str(value).strip():
        return default
    raw = str(value).strip()
    return table.get(raw.lower(), raw)


def normalize_guidance(text: str) -> str:
    raw = str(text or "").strip().lower()
    key = "-".join(raw.replace("_", "-").split())
    mapped = GUIDANCE_ALIASES.get(key, key)
    if mapped not in GUIDANCE:
        names = ", ".join(LOCKED_PROTOCOLS)
        raise argparse.ArgumentTypeError(
            f"Unknown --protocol {text!r}. Use {names} "
            "(Minimal / Graph rules / KG guidance)."
        )
    return mapped


def guidance_spec(name: str) -> dict[str, str]:
    return dict(GUIDANCE[normalize_guidance(name)])


def guidance_line(name: str) -> str:
    spec = guidance_spec(name)
    return f"{spec['label']} — {spec['blurb']}"


def locked_campaign_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "generated" / LOCKED_CAMPAIGN


def _module_cmd(module: str, *args: str) -> list[str]:
    return [python_executable(), "-m", module, *args]


def _hashes(cases: list[dict[str, str]]) -> list[str]:
    return [item["hash"] for item in cases]


def _want(domain: str, name: str) -> bool:
    return domain == "both" or domain == name


def _run_tag(pack: str, protocol: str) -> str:
    return f"lk{pack}{PROTOCOL_TAG[protocol]}"


def _ox_dirname(ontology: str, pack: str, guidance: str) -> str:
    prefix = "ox_m" if ontology == "medical" else "ox"
    return f"{prefix}_{_run_tag(pack, guidance)}"


def resolve_models(args: argparse.Namespace) -> tuple[str, str | None]:
    kg = _alias(args.kg_model, KG_MODEL_ALIASES, DEFAULT_KG_MODEL)
    extract = _alias(args.extract_model, EXTRACT_MODEL_ALIASES, None)
    assert kg is not None
    return kg, extract


def _ensure_inputs(args: argparse.Namespace) -> int:
    root = repo_root()
    write_expected_manifest(root=root)
    unpack_if_present(root=root)
    missing = missing_required_pdfs(cases=args.cases, domain=args.domain, root=root)
    if missing:
        print("[FAIL] Eval PDFs are not in git and are missing locally:")
        for item in missing[:15]:
            print(f"       {item}")
        print("       Put eval30_pdfs.zip in data/eval_bundles/ then re-run,")
        print("       or copy PDFs into scenarios/mops/datasets/eval30 and")
        print("       scenarios/medical/datasets/eval30. See docs/ONE_CLICK_RUN.md.")
        return 2
    if _want(args.domain, "main"):
        pack = chemistry_pack_path(args.pack, root=root)
        if not mcp_pack_ready(pack):
            print(f"[FAIL] Frozen chemistry MCP pack missing: {pack}")
            print("       Put locked_mcp_packs.zip in data/eval_bundles/ and unpack.")
            print("       Do not generate a new pack if you want the paper MCP surface.")
            return 2
        try:
            assert_frozen_extraction_prompts(pack, pack_id=args.pack)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[FAIL] Frozen chemistry extraction prompts: {exc}")
            print("       Use the official s1–s4 pack. Do not point extract at a newly generated run.")
            return 2
    if _want(args.domain, "ontomed"):
        pack = medical_pack_path(root=root)
        if not mcp_pack_ready(pack):
            print(f"[FAIL] Frozen OntoMed MCP pack missing: {pack}")
            print("       Put locked_mcp_packs.zip in data/eval_bundles/ and unpack.")
            return 2
        try:
            assert_frozen_extraction_prompts(pack)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[FAIL] Frozen OntoMed extraction prompts: {exc}")
            return 2
    return 0


def _score_medical(run_dir: Path, scorer: Path, env: dict[str, str], *, dry_run: bool) -> dict[str, Any]:
    eval_dir = run_dir / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    pred = eval_dir / "predicted.csv"
    report_json = eval_dir / "scoring_report.json"
    report_md = eval_dir / "scoring_report.md"
    runtime = run_dir / "score_runtime" if (run_dir / "score_runtime").is_dir() else run_dir / "runtime"
    convert = [
        python_executable(),
        str(scorer / "scripts" / "medical_ttl_to_csv_sparql.py"),
        "--data-dir",
        str(runtime.resolve()),
        "--output",
        str(pred.resolve()),
        "--reference-csv",
        str((repo_root() / MEDICAL_GOLD).resolve()),
        "--reference-csv-header-row",
        "0",
        "--schema-ttl",
        str((repo_root() / MEDICAL_SCHEMA).resolve()),
    ]
    score = [
        python_executable(),
        str(scorer / "scripts" / "medical_score_predicted_vs_gold.py"),
        "--gold",
        str((repo_root() / MEDICAL_GOLD).resolve()),
        "--pred",
        str(pred.resolve()),
        "--out-json",
        str(report_json.resolve()),
        "--out-md",
        str(report_md.resolve()),
    ]
    if not command_succeeded(run_logged(convert, cwd=scorer, env=env, dry_run=dry_run)):
        raise SystemExit("medical TTL→CSV failed")
    if not command_succeeded(run_logged(score, cwd=scorer, env=env, dry_run=dry_run)):
        raise SystemExit("medical scoring failed")
    if dry_run or not report_json.is_file():
        return {}
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _extract_argv(
    *,
    ontology: str,
    pack_root: Path,
    tag: str,
    hashes: list[str],
    workers: int,
    extract_model: str | None,
    config_path: Path | None,
) -> list[str]:
    argv = _module_cmd("src.extraction_runtime", ontology)
    if config_path is not None:
        argv.extend(["--config", str(config_path), "--resume"])
    else:
        argv.extend(["--generation-run", str(pack_root), "--tag", tag])
    argv.extend(
        [
            "--until",
            "main_ontology_extractions",
            "--test",
            "--workers",
            str(workers),
            *hash_cli(hashes),
        ]
    )
    if extract_model:
        argv.extend(["--extraction-model", extract_model])
    return argv


def _pack_child_env(env: dict[str, str], pack_root: Path) -> dict[str, str]:
    bound = dict(env)
    bound["TWA_GENERATED_ARTIFACT_ROOT"] = str(Path(pack_root).resolve())
    return bound


def _pipeline_argv(
    *,
    ontology: str,
    pack_root: Path,
    engine: str,
    tag: str,
    hashes: list[str],
    workers: int,
    kg_model: str,
    extract_model: str | None,
    config_path: Path | None,
    score: bool,
) -> list[str]:
    del engine, extract_model
    argv = _module_cmd("src.extraction_runtime", ontology)
    if config_path is None:
        raise SystemExit("Pipeline KG letter jobs need a prepared run config")
    del pack_root, tag
    argv.extend(
        [
            "--config",
            str(config_path),
            "--resume",
            "--test",
            "--workers",
            str(workers),
            *hash_cli(hashes),
        ]
    )
    if kg_model:
        argv.extend(["--kg-model", kg_model])
    if score:
        argv.append("--score")
    return argv


def _ox_argv(
    *,
    domain: str,
    engine: str,
    hint_run: Path,
    out_dir: Path,
    hashes: list[str],
    kg_model: str,
    score: bool,
    scorer: Path | None,
) -> list[str]:
    argv = _module_cmd(
        "src.kg_building.ontologx",
        "--domain",
        "medical" if domain == "ontomed" else "ontosynthesis",
        "--protocol",
        engine,
        "--hint-runs",
        str(hint_run),
        "--out-dir",
        str(out_dir),
        "--model",
        kg_model,
        *hash_cli(hashes),
    )
    if score and domain != "ontomed":
        argv.append("--score")
        if scorer is not None:
            argv.extend(["--scorer-repo", str(scorer)])
    return argv


def _ox_group_complete(out_dir: Path, hashes: list[str]) -> bool:
    summary = out_dir / "summary.json"
    if not summary.is_file():
        return False
    try:
        payload = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    got = {item.get("hash") for item in payload.get("papers") or []}
    return all(item in got for item in hashes)


def _clean_incomplete_ox(out_dir: Path, hashes: list[str]) -> None:
    if out_dir.exists() and not _ox_group_complete(out_dir, hashes):
        print(f"[CLEAN] incomplete OX {out_dir.name}", flush=True)
        shutil.rmtree(out_dir)


def _merge_ox_letters(
    dest: Path,
    group_runs: list[tuple[str, list[str], Path]],
) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "score_runtime").mkdir(parents=True, exist_ok=True)
    papers: list[dict[str, Any]] = []
    first_prompt: Path | None = None
    for _letter, hashes, src in group_runs:
        prompt = src / "system_prompt.md"
        if first_prompt is None and prompt.is_file():
            first_prompt = prompt
        summary = src / "summary.json"
        if summary.is_file():
            payload = json.loads(summary.read_text(encoding="utf-8"))
            papers.extend(payload.get("papers") or [])
        for paper_hash in hashes:
            paper_src = src / paper_hash
            runtime_src = src / "score_runtime" / paper_hash
            if paper_src.is_dir():
                shutil.copytree(paper_src, dest / paper_hash, dirs_exist_ok=True)
            if runtime_src.is_dir():
                shutil.copytree(
                    runtime_src, dest / "score_runtime" / paper_hash, dirs_exist_ok=True
                )
    if first_prompt is not None:
        shutil.copy2(first_prompt, dest / "system_prompt.md")
    (dest / "summary.json").write_text(
        json.dumps({"papers": papers, "merged_from": [src.name for _, _, src in group_runs]}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _block_complete(run_dir: Path, hashes: list[str], marker: str) -> bool:
    ok, _missing = marker_complete(run_dir / "runtime", hashes, marker)
    return ok


def _rewrite_run_paths(run_dir: Path) -> None:
    config_path = run_dir / "pipeline.resolved.json"
    if not config_path.is_file():
        return
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runtime = run_dir / "runtime"
    try:
        config["data_dir"] = str(runtime.resolve().relative_to(repo_root()).as_posix())
    except ValueError:
        config["data_dir"] = str(runtime)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _prepare_kg_run_config(run_dir: Path, *, engine: str, kg_model: str) -> None:
    """Match official s1ka–o: protocol in the JSON, steps = main_kg_building only."""
    from src.kg_building.experiment_protocol import apply_to_pipeline_config

    config_path = run_dir / "pipeline.resolved.json"
    if not config_path.is_file():
        raise SystemExit(f"missing KG run config: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    apply_to_pipeline_config(config, engine)
    config["steps"] = ["main_kg_building"]
    config["kg_model"] = kg_model
    runtime = run_dir / "runtime"
    try:
        config["data_dir"] = str(runtime.resolve().relative_to(repo_root()).as_posix())
    except ValueError:
        config["data_dir"] = str(runtime)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _strip_kg_markers(run_dir: Path, hashes: list[str]) -> None:
    runtime = run_dir / "runtime"
    for paper_hash in hashes:
        marker = runtime / paper_hash / KG_DONE
        if marker.is_file():
            marker.unlink()


def _copy_extract(src: Path, scenario: str, tag: str, hashes: list[str]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = repo_root() / "scenarios" / scenario / "runs" / f"{stamp}_{tag}"
    shutil.copytree(src, dest)
    _rewrite_run_paths(dest)
    _strip_kg_markers(dest, hashes)
    return dest


def _extract_tag(pack_key: str) -> str:
    return f"lkex{pack_key}"


def hash_groups(hashes: list[str], size: int) -> list[list[str]]:
    width = max(1, int(size))
    return [list(hashes[i : i + width]) for i in range(0, len(hashes), width)]


def official_letter_groups(hashes: list[str]) -> list[tuple[str, list[str]]]:
    """Keep official s1xha–o partners. Drop a paper's mate when it is not selected."""
    wanted = set(hashes)
    grouped: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for letter, pair in S1_LETTER_GROUPS.items():
        present = [item for item in pair if item in wanted]
        if not present:
            continue
        grouped.append((letter, present))
        seen.update(present)
    leftover = [item for item in hashes if item not in seen]
    for index, item in enumerate(leftover):
        grouped.append((f"x{index}", [item]))
    return grouped


def ox_letter_groups(hashes: list[str], *, chemistry: bool) -> list[tuple[str, list[str]]]:
    if chemistry:
        return official_letter_groups(hashes)
    return [(group_suffix(index), group) for index, group in enumerate(hash_groups(hashes, OX_GROUP_SIZE))]


def group_suffix(index: int) -> str:
    if index < 0:
        raise ValueError(f"letter group index out of range: {index}")
    if index < 26:
        return chr(ord("a") + index)
    high, low = divmod(index, 26)
    return f"{chr(ord('a') + high - 1)}{chr(ord('a') + low)}"


def _run_jobs(
    jobs: list[list[str]],
    *,
    env: dict[str, str],
    max_processes: int,
    dry_run: bool,
) -> list[int]:
    if not jobs:
        return []
    cap = min(max(1, int(max_processes)), len(jobs))
    print(
        f"[INFO] Subprocesses: {len(jobs)} jobs, {cap} concurrent (locked wave = 5)",
        flush=True,
    )
    if dry_run or cap == 1:
        return [run_logged(job, env=env, dry_run=dry_run) for job in jobs]
    codes = [1] * len(jobs)
    with ThreadPoolExecutor(max_workers=cap) as pool:
        futures = {
            pool.submit(run_logged, job, env=env, dry_run=False): index
            for index, job in enumerate(jobs)
        }
        for future in as_completed(futures):
            codes[futures[future]] = int(future.result())
    return codes


def _merge_letter_runs(
    dest: Path,
    group_runs: list[tuple[list[str], Path]],
) -> Path:
    if not group_runs:
        raise SystemExit("no extract groups to merge")
    first_src = group_runs[0][1]
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(first_src, dest)
    dest_runtime = dest / "runtime"
    dest_runtime.mkdir(parents=True, exist_ok=True)
    for group_hashes, src in group_runs[1:]:
        src_runtime = src / "runtime"
        for paper_hash in group_hashes:
            src_paper = src_runtime / paper_hash
            if not src_paper.is_dir():
                continue
            dest_paper = dest_runtime / paper_hash
            if dest_paper.exists():
                shutil.rmtree(dest_paper)
            shutil.copytree(src_paper, dest_paper)
        reports = src_runtime / "reports" / "openrouter_costs.jsonl"
        dest_reports = dest_runtime / "reports" / "openrouter_costs.jsonl"
        if reports.is_file():
            dest_reports.parent.mkdir(parents=True, exist_ok=True)
            with dest_reports.open("a", encoding="utf-8") as out, reports.open(
                encoding="utf-8"
            ) as inp:
                out.write(inp.read())
    _rewrite_run_paths(dest)
    return dest


def _run_extract_campaign(
    *,
    ontology: str,
    scenario: str,
    pack_root: Path,
    extract_tag: str,
    hashes: list[str],
    extract_model: str | None,
    env: dict[str, str],
    max_processes: int,
    dry_run: bool,
) -> Path:
    groups = hash_groups(hashes, EXTRACT_GROUP_SIZE)
    print(
        f"[INFO] Extract: frozen pack {pack_root.name} "
        f"(s1–s4 letter jobs; not regenerating prompts); "
        f"{EXTRACT_GROUP_SIZE} paper/process, "
        f"{len(groups)} groups, up to {max_processes} processes",
        flush=True,
    )
    pending_argv: list[list[str]] = []
    pending_meta: list[tuple[int, list[str], str]] = []
    completed: dict[int, tuple[list[str], Path]] = {}
    for index, group in enumerate(groups):
        tag = f"{extract_tag}{group_suffix(index)}"
        existing = latest_scenario_run(scenario, tag)
        if existing is not None and _block_complete(existing, group, EXTRACT_DONE):
            print(f"[OK] Reuse extract group {tag} {existing.name}", flush=True)
            completed[index] = (group, existing)
            continue
        pending_argv.append(
            _extract_argv(
                ontology=ontology,
                pack_root=pack_root,
                tag=tag,
                hashes=group,
                workers=1,
                extract_model=extract_model,
                config_path=None,
            )
        )
        pending_meta.append((index, group, tag))
    codes = _run_jobs(
        pending_argv,
        env=_pack_child_env(env, pack_root),
        max_processes=max_processes,
        dry_run=dry_run,
    )
    if dry_run:
        return Path(f"scenarios/{scenario}/runs/dryrun_{extract_tag}")
    for (index, group, tag), code in zip(pending_meta, codes):
        run_dir = latest_scenario_run(scenario, tag)
        if run_dir is None:
            raise SystemExit(f"{ontology} extract group {tag} did not mint a run")
        if not command_succeeded(code) and not _block_complete(run_dir, group, EXTRACT_DONE):
            raise SystemExit(f"{ontology} extract group {tag} failed")
        completed[index] = (group, run_dir)
    ordered = [completed[index] for index in range(len(groups))]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = repo_root() / "scenarios" / scenario / "runs" / f"{stamp}_{extract_tag}"
    _merge_letter_runs(dest, ordered)
    if not _block_complete(dest, hashes, EXTRACT_DONE):
        raise SystemExit(f"{ontology} merged extract {dest.name} is incomplete")
    print(f"[OK] Merged extract groups → {dest.name}", flush=True)
    return dest


def _score_chemistry(
    run_dir: Path,
    hashes: list[str],
    scorer: Path,
    workers: int,
    *,
    runtime_name: str = "runtime",
) -> None:
    ox = repo_root() / "src" / "kg_building" / "ontologx"
    if str(ox) not in sys.path:
        sys.path.insert(0, str(ox))
    from score_four import convert_runtime_many, score_four

    runtime = run_dir / runtime_name
    convert_runtime_many(
        data_dir=runtime,
        output_dir=run_dir / "merged",
        paper_hashes=hashes,
        converter_repo=scorer,
        max_workers=workers,
    )
    score_four(
        pred_root=run_dir / "merged",
        out_root=run_dir / "scores",
        paper_hashes=hashes,
        scorer_repo=scorer,
        max_workers=workers,
    )


def _run_kg_campaign(
    *,
    ontology: str,
    pack_root: Path,
    engine: str,
    tag: str,
    kg_run: Path,
    hashes: list[str],
    kg_model: str,
    extract_model: str | None,
    env: dict[str, str],
    max_processes: int,
    score: bool,
    scorer: Path | None,
    dry_run: bool,
) -> None:
    groups = hash_groups(hashes, KG_GROUP_SIZE)
    print(
        f"[INFO] Pipeline KG like locked s1ka–o: steps=main_kg_building, "
        f"{KG_GROUP_SIZE} paper/process, {len(groups)} jobs, up to {max_processes} processes",
        flush=True,
    )
    if not dry_run:
        _prepare_kg_run_config(kg_run, engine=engine, kg_model=kg_model)
    config_path = kg_run / "pipeline.resolved.json"
    jobs: list[list[str]] = []
    for group in groups:
        if not dry_run and _block_complete(kg_run, group, KG_DONE):
            print(f"[OK] Reuse Pipeline KG {group[0]}", flush=True)
            continue
        jobs.append(
            _pipeline_argv(
                ontology=ontology,
                pack_root=pack_root,
                engine=engine,
                tag=tag,
                hashes=group,
                workers=1,
                kg_model=kg_model,
                extract_model=extract_model,
                config_path=config_path,
                score=False,
            )
        )
    codes = _run_jobs(jobs, env=_pack_child_env(env, pack_root), max_processes=max_processes, dry_run=dry_run)
    if dry_run:
        return
    if any(not command_succeeded(code) for code in codes) and not _block_complete(
        kg_run, hashes, KG_DONE
    ):
        raise SystemExit(f"{ontology} Pipeline KG failed")
    if not _block_complete(kg_run, hashes, KG_DONE):
        raise SystemExit(f"{ontology} Pipeline KG failed")
    if score and ontology != "medical":
        if scorer is None:
            raise SystemExit("chemistry scoring needs a scorer repo")
        _score_chemistry(kg_run, hashes, scorer, max_processes)


def _run_ox_campaign(
    *,
    domain: str,
    scenario: str,
    engine: str,
    hint_run: Path,
    hashes: list[str],
    kg_model: str,
    env: dict[str, str],
    max_processes: int,
    score: bool,
    scorer: Path | None,
    pack: str,
    guidance: str,
    ontology: str,
    dry_run: bool,
) -> Path:
    ox_base = _ox_dirname(ontology, pack, guidance)
    dest = repo_root() / "scenarios" / scenario / "runs" / ox_base
    groups = ox_letter_groups(hashes, chemistry=ontology != "medical")
    print(
        f"[INFO] OntoLogX like official s1xha–o: {OX_GROUP_SIZE} paper/process, "
        f"{len(groups)} letter jobs, up to {max_processes} processes",
        flush=True,
    )
    if not dry_run and _ox_group_complete(dest, hashes) and (dest / "scores").is_dir():
        print(f"[OK] Reuse merged OX {dest.name}", flush=True)
        return dest
    pending_argv: list[list[str]] = []
    pending_meta: list[tuple[str, list[str], Path]] = []
    completed: dict[str, tuple[list[str], Path]] = {}
    for letter, group in groups:
        if len(group) > OX_GROUP_SIZE:
            raise SystemExit(f"OX letter {letter} has {len(group)} papers; official jobs are {OX_GROUP_SIZE}")
        out_dir = repo_root() / "scenarios" / scenario / "runs" / f"{ox_base}{letter}"
        if not dry_run and _ox_group_complete(out_dir, group):
            print(f"[OK] Reuse OX letter {out_dir.name} {group}", flush=True)
            completed[letter] = (group, out_dir)
            continue
        if not dry_run:
            _clean_incomplete_ox(out_dir, group)
        pending_argv.append(
            _ox_argv(
                domain=domain,
                engine=engine,
                hint_run=hint_run,
                out_dir=out_dir,
                hashes=group,
                kg_model=kg_model,
                score=False,
                scorer=None,
            )
        )
        pending_meta.append((letter, group, out_dir))
    codes = _run_jobs(pending_argv, env=env, max_processes=max_processes, dry_run=dry_run)
    if dry_run:
        return dest
    for (letter, group, out_dir), code in zip(pending_meta, codes):
        if not command_succeeded(code) and not _ox_group_complete(out_dir, group):
            raise SystemExit(f"{ontology} OntoLogX letter {out_dir.name} failed")
        completed[letter] = (group, out_dir)
    ordered = [(letter, *completed[letter]) for letter, _group in groups]
    _merge_ox_letters(dest, ordered)
    if not _ox_group_complete(dest, hashes):
        raise SystemExit(f"{ontology} merged OX {dest.name} is incomplete")
    print(f"[OK] Merged OX letters → {dest.name}", flush=True)
    if score and ontology != "medical":
        if scorer is None:
            raise SystemExit("chemistry scoring needs a scorer repo")
        _score_chemistry(dest, hashes, scorer, max_processes, runtime_name="score_runtime")
    return dest


def run_domain(
    *,
    args: argparse.Namespace,
    ontology: str,
    scenario: str,
    pack_root: Path,
    hashes: list[str],
    env: dict[str, str],
    scorer: Path | None,
    kg_model: str,
    extract_model: str | None,
    from_extract: Path | None,
) -> dict[str, Any]:
    pack_key = args.pack if scenario == "mops" else "m"
    guidance = args.protocol
    spec = guidance_spec(guidance)
    engine = spec["engine"]
    extract_tag = _extract_tag(pack_key)
    kg_tag = _run_tag(pack_key, guidance)
    builder = args.builder
    score_pipeline = builder in {"pipeline", "both"}
    run_ox = builder in {"ox", "both"}
    need_pipeline = builder in {"pipeline", "ox", "both"}

    block: dict[str, Any] = {
        "ontology": ontology,
        "hashes": hashes,
        "pack": str(pack_root),
        "protocol": guidance,
        "guidance": spec["label"],
        "guidance_blurb": spec["blurb"],
        "engine": engine,
        "kg_model": kg_model,
    }

    extract_run = from_extract if from_extract is not None else latest_scenario_run(scenario, extract_tag)
    if from_extract is not None and (extract_run is None or not extract_run.is_dir()):
        raise SystemExit(f"Missing --from-extract {from_extract}")

    if need_pipeline and (extract_run is None or not _block_complete(extract_run, hashes, EXTRACT_DONE)):
        if from_extract is not None:
            raise SystemExit(f"{ontology} --from-extract is incomplete")
        extract_run = _run_extract_campaign(
            ontology=ontology,
            scenario=scenario,
            pack_root=pack_root,
            extract_tag=extract_tag,
            hashes=hashes,
            extract_model=extract_model,
            env=env,
            max_processes=args.workers,
            dry_run=args.dry_run,
        )
        if not args.dry_run and (
            extract_run is None or not _block_complete(extract_run, hashes, EXTRACT_DONE)
        ):
            raise SystemExit(f"{ontology} extraction failed")

    kg_run = latest_scenario_run(scenario, kg_tag)
    if need_pipeline and (kg_run is None or not _block_complete(kg_run, hashes, KG_DONE)):
        if args.dry_run:
            kg_run = kg_run or Path(f"scenarios/{scenario}/runs/dryrun_{kg_tag}")
            _run_kg_campaign(
                ontology=ontology,
                pack_root=pack_root,
                engine=engine,
                tag=kg_tag,
                kg_run=kg_run,
                hashes=hashes,
                kg_model=kg_model,
                extract_model=extract_model,
                env=env,
                max_processes=args.workers,
                score=score_pipeline and ontology != "medical",
                scorer=scorer,
                dry_run=True,
            )
        else:
            if extract_run is None:
                raise SystemExit(f"{ontology} extract run is missing")
            if kg_run is None or not (kg_run / "pipeline.resolved.json").is_file():
                kg_run = _copy_extract(extract_run, scenario, kg_tag, hashes)
            else:
                print(f"[INFO] Resume Pipeline KG {kg_run.name}", flush=True)
            _run_kg_campaign(
                ontology=ontology,
                pack_root=pack_root,
                engine=engine,
                tag=kg_tag,
                kg_run=kg_run,
                hashes=hashes,
                kg_model=kg_model,
                extract_model=extract_model,
                env=env,
                max_processes=args.workers,
                score=score_pipeline and ontology != "medical",
                scorer=scorer,
                dry_run=False,
            )
    run_dir = kg_run
    if run_dir is None:
        if args.dry_run:
            run_dir = Path(f"scenarios/{scenario}/runs/dryrun_{kg_tag}")
        else:
            raise SystemExit(f"{ontology} run directory is missing")

    try:
        block["run_dir"] = str(run_dir.resolve().relative_to(repo_root()).as_posix())
        block["config"] = str((run_dir / "pipeline.resolved.json").resolve().relative_to(repo_root()).as_posix())
        if extract_run is not None:
            block["extract_run"] = str(extract_run.resolve().relative_to(repo_root()).as_posix())
    except (OSError, ValueError):
        block["run_dir"] = str(run_dir).replace("\\", "/")
        block["config"] = str(Path(run_dir) / "pipeline.resolved.json").replace("\\", "/")

    if score_pipeline and ontology == "medical" and scorer is not None:
        block["medical_score"] = _score_medical(run_dir, scorer, env, dry_run=args.dry_run)
    elif score_pipeline and ontology != "medical":
        scores = run_dir / "scores"
        if scores.is_dir():
            block["scores"] = chemistry_score_table(scores)

    if run_ox:
        ox_dir = _run_ox_campaign(
            domain="ontomed" if ontology == "medical" else "main",
            scenario=scenario,
            engine=engine,
            hint_run=run_dir,
            hashes=hashes,
            kg_model=kg_model,
            env=env,
            max_processes=args.workers,
            score=ontology != "medical",
            scorer=scorer,
            pack=args.pack,
            guidance=guidance,
            ontology=ontology,
            dry_run=args.dry_run,
        )
        block["ox_dir"] = str(ox_dir).replace("\\", "/")
        try:
            block["ox_dir"] = str(ox_dir.resolve().relative_to(repo_root()).as_posix())
        except (OSError, ValueError):
            pass
        if ontology == "medical" and scorer is not None:
            block["ox_medical_score"] = _score_medical(ox_dir, scorer, env, dry_run=args.dry_run)
        elif ontology != "medical":
            ox_scores = ox_dir / "scores"
            if ox_scores.is_dir():
                block["ox_scores"] = chemistry_score_table(ox_scores)
    return block


def print_summary(campaign: dict[str, Any]) -> None:
    print("")
    print("=" * 60)
    print("LOCKED 1:1 SUMMARY")
    print("=" * 60)
    print(f"Guidance: {campaign.get('guidance_line') or campaign.get('protocol')}")
    print(f"Pack:     {campaign.get('pack')}")
    print(f"KG model: {campaign.get('kg_model')}")
    print(f"Cases:    {campaign.get('cases')}")
    print(f"Builder:  {campaign.get('builder')}")
    for key, title in (("main", "OntoSynthesis"), ("ontomed", "OntoMed")):
        block = campaign.get(key)
        if not isinstance(block, dict):
            continue
        print(f"\n{title}")
        print(f"  pipeline  {block.get('run_dir')}")
        if block.get("ox_dir"):
            print(f"  ontologx  {block.get('ox_dir')}")
        scores = block.get("scores") or {}
        if scores:
            steps = scores.get("scoring_steps")
            if isinstance(steps, float):
                print(f"  pipeline steps F1={steps:.3f}")
        ox_scores = block.get("ox_scores") or {}
        if ox_scores:
            steps = ox_scores.get("scoring_steps")
            if isinstance(steps, float):
                print(f"  ontologx steps F1={steps:.3f}")
        medical = block.get("medical_score") or {}
        if isinstance(medical.get("overall_accuracy"), float):
            print(f"  pipeline accuracy={medical['overall_accuracy']:.1%}")
        ox_med = block.get("ox_medical_score") or {}
        if isinstance(ox_med.get("overall_accuracy"), float):
            print(f"  ontologx accuracy={ox_med['overall_accuracy']:.1%}")
    print("=" * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one locked 1:1 condition: unpack eval zips if needed, extract "
            "from PDFs (extraction is not in git), Pipeline KG, then OntoLogX."
        )
    )
    parser.add_argument(
        "cases_positional",
        nargs="?",
        type=int,
        default=None,
        help="How many eval cases (1-30). Same as --cases. Default: 1.",
    )
    parser.add_argument("--cases", type=int, default=1, help="How many eval cases (1-30). Default: 1.")
    parser.add_argument(
        "--protocol",
        type=normalize_guidance,
        default="minimal",
        help=(
            "Guidance surface (default: minimal). "
            "minimal — occurrence and ownership instructions only. "
            "graph-rules — those plus explicit construction recipes and the T-Box handbook. "
            "kg-guidance — those plus the frozen human-engineered graph-construction "
            "guidance and the same T-Box handbook. "
            "Legacy engine ids generic-strict / generic-noprompt / with-prompt still map."
        ),
    )
    parser.add_argument("--domain", choices=("both", "main", "ontomed"), default="both")
    parser.add_argument(
        "--builder",
        choices=("both", "pipeline", "ox"),
        default="both",
        help="Pipeline MCP, OntoLogX, or both. OX still runs Pipeline KG first (token budget).",
    )
    parser.add_argument(
        "--pack",
        choices=("s1", "s2", "s3", "s4"),
        default="s1",
        help="Frozen chemistry MCP pack. OntoMed always uses med-s1_newmcp.",
    )
    parser.add_argument("--kg-model", default="gpt-4o", help="gpt-4o or kimi (aliases allowed).")
    parser.add_argument(
        "--extract-model",
        default=None,
        help="Optional extract override (gpt-4.1, gpt-5, kimi). Default: domain config.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Max concurrent extract/KG Python processes (default: 5, locked wave). "
            "Each process runs exactly one paper, sequentially."
        ),
    )
    parser.add_argument("--from-extract", type=Path, help="Reuse this chemistry Pipeline run.")
    parser.add_argument("--from-extract-medical", type=Path, help="Reuse this OntoMed Pipeline run.")
    parser.add_argument(
        "--hash",
        action="append",
        dest="hashes",
        help="Optional paper hash (repeatable). Overrides --cases order.",
    )
    parser.add_argument("--scorer-repo", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true", help="Print input status and exit.")
    parser.add_argument("--unpack", action="store_true", help="Unpack eval zips and exit.")
    parser.add_argument("--list", action="store_true", help="Print the locked conditions and exit.")
    return parser


def _print_list() -> None:
    print("Locked 1:1 guidance surfaces")
    for key, spec in GUIDANCE.items():
        print(f"  --protocol {key:12}  {spec['label']} — {spec['blurb']}")
        print(f"                   engine id {spec['engine']}")
    print("  Chemistry packs: s1 s2 s3 (gpt-4.1 extract) and s4 (use --extract-model kimi --kg-model kimi)")
    print("  OntoMed locked pack is Minimal only; other protocols still run if you ask")
    print("  KG models: gpt-4o (default) or kimi")
    print("  Builders: pipeline, ox, both")
    print("")
    print("Examples")
    print("  run_locked.cmd")
    print("  run_locked.cmd --domain main --protocol minimal --cases 5")
    print("  run_locked.cmd --domain main --protocol graph-rules --cases 5")
    print("  run_locked.cmd --domain main --protocol kg-guidance --cases 5")
    print("  run_locked.cmd --pack s4 --extract-model kimi --kg-model kimi --domain main")
    print("")
    print("Extraction ledgers are not in git. The script extracts from PDFs.")
    print("Inputs: docs/ONE_CLICK_RUN.md")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.list:
        _print_list()
        return 0
    if args.unpack:
        write_expected_manifest()
        unpack_if_present(force=False)
        return print_check(check_inputs(cases=args.cases, domain=args.domain, pack=args.pack))
    if args.check:
        write_expected_manifest()
        return print_check(check_inputs(cases=args.cases, domain=args.domain, pack=args.pack))

    cases_count = args.cases_positional if args.cases_positional is not None else args.cases
    args.cases = cases_count
    if args.workers < 1:
        print("[FAIL] --workers must be at least 1")
        return 2
    try:
        main_cases = select_eval_cases(cases_count, kind="main")
        medical_cases = select_eval_cases(cases_count, kind="ontomed")
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2
    if args.hashes:
        wanted = [item.strip() for item in args.hashes if str(item).strip()]
        catalog = {item["hash"]: item for item in select_eval_cases(30, kind="main")}
        missing = [item for item in wanted if item not in catalog]
        if missing:
            print("[FAIL] Unknown --hash: " + ", ".join(missing))
            return 2
        main_cases = [catalog[item] for item in wanted]
        args.cases = len(main_cases)
        cases_count = args.cases

    kg_model, extract_model = resolve_models(args)
    if "kimi-k3" in kg_model.lower() or (extract_model and "kimi-k3" in extract_model.lower()):
        import os

        os.environ.setdefault("TWA_REASONING_EFFORT", "low")

    missing_env = required_env_keys()
    if missing_env and not args.dry_run:
        print("[FAIL] Copy .env.example to .env and set: " + ", ".join(missing_env))
        return 2
    if not args.dry_run:
        status = _ensure_inputs(args)
        if status:
            return status

    scorer = None
    if not args.dry_run:
        scorer = ensure_scorer_repo(args.scorer_repo)
        if scorer is None:
            print("[FAIL] Could not find or clone scoring engines.")
            return 2
        print(f"[OK] Scorer: {scorer}")

    env = child_env()
    if scorer is not None:
        env["SCORER_REPO"] = str(scorer)

    print("=" * 60)
    print("Locked 1:1 run")
    print("=" * 60)
    print(f"Cases:    {cases_count}")
    print(f"Guidance: {guidance_line(args.protocol)}")
    print(f"Engine:   {guidance_spec(args.protocol)['engine']}")
    print(f"Domain:   {args.domain}")
    print(f"Builder:  {args.builder}")
    print(f"Pack:     {args.pack}")
    print(f"KG model: {kg_model}")
    print(f"Extract:  {extract_model or 'domain default'}")
    print(
        f"Workers:  {args.workers} processes "
        f"(extract {EXTRACT_GROUP_SIZE}/process, KG {KG_GROUP_SIZE}/process, "
        f"OX {OX_GROUP_SIZE}/process official s1 letters)"
    )
    print(f"Python:   {sys.executable}")
    print(f"Runtime:  {format_locked_runtime_line()}")
    print("=" * 60, flush=True)
    if locked_runtime_mismatches():
        if args.dry_run:
            print(
                "[WARN] dry-run interpreter is not the official s1-s4 mcp_layer stack",
                flush=True,
            )
        else:
            assert_locked_runtime()

    campaign = {
        "schema_version": "locked-campaign.v1",
        "cases": cases_count,
        "protocol": args.protocol,
        "guidance_line": guidance_line(args.protocol),
        "engine": guidance_spec(args.protocol)["engine"],
        "domain": args.domain,
        "builder": args.builder,
        "pack": args.pack,
        "kg_model": kg_model,
        "extract_model": extract_model,
        "workers": args.workers,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        if _want(args.domain, "main"):
            campaign["main"] = run_domain(
                args=args,
                ontology="ontosynthesis",
                scenario="mops",
                pack_root=chemistry_pack_path(args.pack),
                hashes=_hashes(main_cases),
                env=env,
                scorer=scorer,
                kg_model=kg_model,
                extract_model=extract_model,
                from_extract=args.from_extract,
            )
        if _want(args.domain, "ontomed"):
            campaign["ontomed"] = run_domain(
                args=args,
                ontology="medical",
                scenario="medical",
                pack_root=medical_pack_path(),
                hashes=_hashes(medical_cases),
                env=env,
                scorer=scorer,
                kg_model=kg_model,
                extract_model=extract_model,
                from_extract=args.from_extract_medical,
            )
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(f"[FAIL] {message}")
        return 1

    if not args.dry_run:
        path = locked_campaign_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(campaign, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] Campaign {path}")
    print_summary(campaign)
    print("[OK] Locked run finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
