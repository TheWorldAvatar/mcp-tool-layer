"""Convert a spliced OX/Pipeline runtime and score chemicals, steps, characterisation, CBU.

Scoring engines stay in the original repo so this package does not copy
the 4k-line scorers or write into that repo's evaluation/data/.

Steps matching is always by type, not position. Vessel is absent from the
committed gold; do not pass ``--skip-order`` or ``--no-vessel``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from paths import REPO_ROOT

_HOLD_SCORE_TAGS = REPO_ROOT / "generated" / "campaigns" / "HOLD_SCORE_TAGS.json"


def _held_score_path(path: Path) -> bool:
    """True when a campaign asked to delay scoring until a new scorer is loaded."""
    if not _HOLD_SCORE_TAGS.is_file():
        return False
    try:
        tags = json.loads(_HOLD_SCORE_TAGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    text = str(path).replace("\\", "/")
    return any(f"_{tag}" in text or text.rstrip("/").endswith(str(tag)) for tag in tags)

SCORE_MODULES = (
    ("evaluation.scoring_cbu", ["--full"]),
    ("evaluation.scoring_characterisation", ["--full"]),
    (
        "evaluation.scoring_steps",
        [
            "--full",
            "--ignore",
            "--llm-synonyms",
            "--llm-synonym-model",
            "openai/gpt-5.6-sol",
        ],
    ),
    ("evaluation.scoring_chemicals", ["--full"]),
)
DEFAULT_WORKERS = 5


def _bounded_workers(requested: int, n_items: int) -> int:
    count = max(1, int(requested or 1))
    if n_items <= 0:
        return 1
    return min(count, n_items)


def convert_runtime(
    *,
    data_dir: Path,
    output_dir: Path,
    paper_hash: str,
    converter_repo: Path | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Merge TTL folders (or convert a paper TTL) into scoring JSON."""
    data_dir = Path(data_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if _held_score_path(output_dir):
        print(f"[HOLD] skip convert {output_dir}; see {_HOLD_SCORE_TAGS}", flush=True)
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    repo = converter_repo or REPO_ROOT
    script = repo / "scripts" / "merge_and_conversion_main.py"
    if not script.is_file():
        print(f"[WARN] merge_and_conversion_main missing under {repo}")
        return 1
    command = [
        sys.executable,
        str(script),
        "--hash",
        paper_hash,
        "--data-dir",
        str(data_dir),
        "--output-dir",
        str(output_dir),
        *(extra_args or []),
    ]
    return subprocess.run(command, cwd=repo, check=False).returncode


def convert_runtime_many(
    *,
    data_dir: Path,
    output_dir: Path,
    paper_hashes: list[str],
    converter_repo: Path | None = None,
    extra_args: list[str] | None = None,
    max_workers: int = DEFAULT_WORKERS,
) -> dict[str, int]:
    """Convert papers with up to ``max_workers`` parallel subprocesses."""
    hashes = [item for item in paper_hashes if str(item).strip()]
    workers = _bounded_workers(max_workers, len(hashes))
    print(f"[INFO] Convert workers: {workers} (of {len(hashes)} hashes)", flush=True)
    results: dict[str, int] = {}
    if workers == 1:
        for paper_hash in hashes:
            results[paper_hash] = convert_runtime(
                data_dir=data_dir,
                output_dir=output_dir,
                paper_hash=paper_hash,
                converter_repo=converter_repo,
                extra_args=extra_args,
            )
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            paper_hash: pool.submit(
                convert_runtime,
                data_dir=data_dir,
                output_dir=output_dir,
                paper_hash=paper_hash,
                converter_repo=converter_repo,
                extra_args=extra_args,
            )
            for paper_hash in hashes
        }
        for paper_hash, future in futures.items():
            results[paper_hash] = int(future.result())
    return results


def score_four(
    *,
    pred_root: Path,
    out_root: Path,
    paper_hash: str | None = None,
    paper_hashes: list[str] | None = None,
    scorer_repo: Path,
    max_workers: int = DEFAULT_WORKERS,
) -> dict[str, int]:
    """Run the four official scorers against pred_root. Writes into out_root only.

    Steps accepts ``--hash``; pass every paper once at the end so ``_overall.md``
    is the batch, not the last hash that happened to score.
    """
    env = os.environ.copy()
    pred_root = Path(pred_root).resolve()
    out_root = Path(out_root).resolve()
    if _held_score_path(pred_root) or _held_score_path(out_root):
        print(f"[HOLD] skip score {out_root}; see {_HOLD_SCORE_TAGS}", flush=True)
        return {"held": 0}
    step_hashes = list(paper_hashes or [])
    if paper_hash and paper_hash not in step_hashes:
        step_hashes.append(paper_hash)
    results: dict[str, int] = {}

    def _run_module(module: str, flags: list[str]) -> tuple[str, int]:
        dest = out_root / module.rsplit(".", 1)[-1]
        dest.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            module,
            *flags,
            "--pred-root",
            str(pred_root),
            "--out-root",
            str(dest),
        ]
        if module == "evaluation.scoring_steps":
            for item in step_hashes:
                command.extend(["--hash", item])
        print("SCORE", " ".join(command), flush=True)
        return module, subprocess.run(
            command,
            cwd=scorer_repo,
            env=env,
            check=False,
        ).returncode

    workers = _bounded_workers(max_workers, len(SCORE_MODULES))
    print(f"[INFO] Score-module workers: {workers} (of {len(SCORE_MODULES)} modules)", flush=True)
    if workers == 1:
        for module, flags in SCORE_MODULES:
            name, code = _run_module(module, list(flags))
            results[name] = code
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_module, module, list(flags))
            for module, flags in SCORE_MODULES
        ]
        for future in futures:
            name, code = future.result()
            results[name] = code
    return results
