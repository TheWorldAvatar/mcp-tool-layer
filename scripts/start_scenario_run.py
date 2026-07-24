#!/usr/bin/env python3
"""
Mint a scenario run folder with resolved pipeline config (runtime + evaluation).

Creates:
  scenarios/<domain>/runs/<yyyymmdd>_<dataset>_<tag>/{runtime,evaluation}/
  plus pipeline.resolved.json and run_meta.json

Example:
  python scripts/start_scenario_run.py --domain mops --dataset eval30 --tag gpt-4.1
  python scripts/start_scenario_run.py --domain medical --dataset eval30 --tag gpt-4.1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE_BY_KEY = {
    ("medical", "eval30"): ROOT / "configs" / "scenarios" / "pipeline_medical_eval30.json",
    ("medical", "dev5"): ROOT / "configs" / "scenarios" / "pipeline_medical_dev5.json",
    ("mops", "eval30"): ROOT / "configs" / "scenarios" / "pipeline_mops_eval30.json",
}

DATASET_DIRS = {
    ("medical", "eval30"): ROOT / "scenarios" / "medical" / "datasets" / "eval30",
    ("medical", "dev5"): ROOT / "scenarios" / "medical" / "datasets" / "dev5",
    ("mops", "eval30"): ROOT / "scenarios" / "mops" / "datasets" / "eval30",
}


def _sanitize_tag(tag: str) -> str:
    tag = tag.strip()
    if not tag:
        raise SystemExit("--tag must be non-empty")
    # Allow model-like tags: gpt-4.1, mixed_default, balanced_gpt-4.1
    cleaned = re.sub(r"[^A-Za-z0-9._+-]+", "_", tag)
    return cleaned.strip("._") or "run"


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except Exception:
        return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _load_dotenv_conda_py() -> str | None:
    """Read CONDA_ENV_PY from repo .env without requiring python-dotenv."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "CONDA_ENV_PY":
            continue
        path = value.strip().strip('"').strip("'")
        return path or None
    return None


def _default_python() -> str:
    """Resolve an explicit project interpreter, then use the active interpreter."""
    for candidate in (
        os.environ.get("CONDA_ENV_PY", "").strip(),
        _load_dotenv_conda_py() or "",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return sys.executable or "python"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--domain", choices=["medical", "mops"], required=True)
    p.add_argument(
        "--dataset",
        choices=["eval30", "dev5"],
        required=True,
        help="medical: eval30|dev5; mops: eval30 only",
    )
    p.add_argument(
        "--tag",
        required=True,
        help="Model/profile label for the run folder, e.g. gpt-4.1 or mixed_default",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Override date stamp YYYYMMDD (default: today local)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Reuse an existing run directory (overwrite pipeline.resolved.json / run_meta.json)",
    )
    p.add_argument(
        "--print-only",
        action="store_true",
        help="Print paths/commands without writing files (still requires non-existing or --force)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    key = (args.domain, args.dataset)
    if key not in TEMPLATE_BY_KEY:
        raise SystemExit(
            f"Unsupported combination domain={args.domain} dataset={args.dataset}. "
            "Supported: medical+eval30, medical+dev5, mops+eval30."
        )

    template_path = TEMPLATE_BY_KEY[key]
    dataset_dir = DATASET_DIRS[key]
    if not dataset_dir.is_dir():
        raise SystemExit(f"Dataset directory missing: {dataset_dir}. Run scripts/bootstrap_scenario_datasets.py first.")

    date_stamp = args.date or datetime.now().strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", date_stamp):
        raise SystemExit("--date must be YYYYMMDD")
    tag = _sanitize_tag(args.tag)
    # Include dataset so medical eval30 vs dev5 (same tag) do not collide.
    run_id = f"{date_stamp}_{args.dataset}_{tag}"
    run_dir = ROOT / "scenarios" / args.domain / "runs" / run_id
    runtime_dir = run_dir / "runtime"
    evaluation_dir = run_dir / "evaluation"

    if run_dir.exists() and not args.force and not args.print_only:
        raise SystemExit(
            f"Run already exists: {_rel(run_dir)}\n"
            "Pick a different --tag/--date or pass --force to reuse."
        )

    template = _load_json(template_path)
    resolved = dict(template)
    resolved["input_dir"] = _rel(dataset_dir)
    resolved["data_dir"] = _rel(runtime_dir)
    resolved["scenario"] = {
        "domain": args.domain,
        "dataset": args.dataset,
        "run_id": run_id,
        "tag": tag,
        "evaluation_dir": _rel(evaluation_dir),
    }

    models_path = ROOT / "configs" / "extraction_models.json"
    models = _load_json(models_path) if models_path.exists() else {}

    py = _default_python()
    use_test_flag = args.domain == "mops"  # wire candidate MCP via --test for chemistry stack
    cmd_parts = [
        py,
        "generic_main.py",
        "--config",
        _rel(run_dir / "pipeline.resolved.json"),
    ]
    if use_test_flag:
        cmd_parts.append("--test")
    cmd = subprocess.list2cmdline(cmd_parts) if os.name == "nt" else " ".join(
        f'"{c}"' if " " in c else c for c in cmd_parts
    )

    run_meta = {
        "domain": args.domain,
        "dataset": args.dataset,
        "run_id": run_id,
        "tag": tag,
        "date": date_stamp,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "template_config": _rel(template_path),
        "resolved_config": _rel(run_dir / "pipeline.resolved.json"),
        "input_dir": _rel(dataset_dir),
        "data_dir": _rel(runtime_dir),
        "evaluation_dir": _rel(evaluation_dir),
        "meta_task_config": resolved.get("meta_task_config"),
        "extraction_models": models,
        "artifact_root_env": os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", ""),
        "python": py,
        "command": cmd,
        "notes": (
            "Write evaluation artifacts into evaluation_dir. "
            "Legacy data/ and data_medical_new_cases/ are deprecated for new scenario runs."
        ),
    }

    print("=== Scenario run ===")
    print(f"run_dir:       {_rel(run_dir)}")
    print(f"input_dir:     {_rel(dataset_dir)}")
    print(f"data_dir:      {_rel(runtime_dir)}")
    print(f"evaluation:    {_rel(evaluation_dir)}")
    print(f"command:\n  {cmd}")

    if args.print_only:
        return 0

    runtime_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pipeline.resolved.json").write_text(
        json.dumps(resolved, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "run_meta.json").write_text(
        json.dumps(run_meta, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[OK] Wrote {_rel(run_dir / 'pipeline.resolved.json')}")
    print(f"[OK] Wrote {_rel(run_dir / 'run_meta.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
