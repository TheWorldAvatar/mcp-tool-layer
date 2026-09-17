"""Helpers for the shipped Windows default pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from models.generated_layout import read_current_pointer, resolve_generated_package_root
from models.locations import repository_root
from src.kg_building.scorer_repo import (
    MEDICAL_GOLD,
    MEDICAL_SCHEMA,
    ensure_scorer_repo,
    find_scorer_repo,
    scorer_looks_valid,
)

CAMPAIGN_PATH_NAME = "ship_campaign.json"
GENERATION_TAG = "gpt5"
MAIN_RUN_TAG = "gsm"
ONTOMED_RUN_TAG = "gsd"
PROTOCOL = "generic-strict"
DEFAULT_WORKERS = 5
MAX_CASES = 30
MIN_CASES = 1

# Official s1–s4 letter jobs used conda env mcp_layer (Python 3.11).
LOCKED_PYTHON = (3, 11)
LOCKED_RUNTIME_VERSIONS = {
    "mcp": "1.10.1",
    "fastmcp": "2.10.1",
    "langchain": "0.3.26",
    "langchain-core": "0.3.66",
    "langchain-openai": "0.3.25",
    "langchain-mcp-adapters": "0.1.7",
    "langgraph": "0.4.8",
    "anyio": "4.9.0",
    "openai": "1.91.0",
}


def default_locked_python() -> Path | None:
    explicit = str(os.environ.get("TWA_LOCKED_PYTHON") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    conda = (
        Path(os.environ.get("USERPROFILE") or "")
        / "AppData"
        / "Local"
        / "anaconda3"
        / "envs"
        / "mcp_layer"
        / "python.exe"
    )
    if conda.is_file():
        return conda
    return None


def locked_runtime_versions() -> dict[str, str]:
    import importlib.metadata as metadata

    out = {"python": sys.version.split()[0], "executable": sys.executable or ""}
    for name in LOCKED_RUNTIME_VERSIONS:
        try:
            out[name] = metadata.version(name)
        except Exception:
            out[name] = "missing"
    return out


def format_locked_runtime_line() -> str:
    payload = locked_runtime_versions()
    parts = [
        f"python={payload['python']}",
        *[f"{name}={payload[name]}" for name in LOCKED_RUNTIME_VERSIONS],
    ]
    return " ".join(parts)


def locked_runtime_mismatches() -> list[str]:
    problems: list[str] = []
    if sys.version_info[:2] != LOCKED_PYTHON:
        problems.append(
            f"python={sys.version.split()[0]} (locked s1-s4 is 3.11 from mcp_layer)"
        )
    payload = locked_runtime_versions()
    for name, expected in LOCKED_RUNTIME_VERSIONS.items():
        got = payload.get(name) or "missing"
        if got != expected:
            problems.append(f"{name}={got} (locked s1-s4 is {expected})")
    return problems


def assert_locked_runtime() -> None:
    problems = locked_runtime_mismatches()
    if not problems:
        return
    print("[FAIL] Interpreter is not the official s1-s4 mcp_layer stack:")
    for item in problems:
        print(f"  {item}")
    hint = default_locked_python()
    if hint is not None:
        print(f"  use: {hint}")
    raise SystemExit("locked runtime mismatch")

MAIN_PAPERS = Path("src/kg_building/ontologx/papers_eval30.json")
ONTOMED_PAPERS = Path("src/kg_building/ontologx/papers_medical.json")
MAIN_PDF_DIR = Path("scenarios/mops/datasets/eval30")
ONTOMED_PDF_DIR = Path("scenarios/medical/datasets/eval30")

WINDOWS_ACCESS_VIOLATION = {3221225477, -1073741819}
FINE_GRAINED_RE = re.compile(
    r"\*\*Fine-grained Scoring(?: \([^)]+\))?:\*\*[^\n]*F1=([0-9.]+)",
    re.IGNORECASE,
)
CURRENT_OVERALL_RE = re.compile(
    r"\*\*Current Overall\*\*:[^\n]*F1=([0-9.]+)",
    re.IGNORECASE,
)
TABLE_OVERALL_F1_RE = re.compile(
    r"\|\s*-\s*\|\s*\*\*Overall\*\*.*?\|\s*\*\*([0-9.]+)\*\*\s*\|",
    re.IGNORECASE,
)
COMBINED_SUMMARY_RE = re.compile(
    r"\*\*Combined Scoring Summary:\*\*[^\n]*F1=([0-9.]+)",
    re.IGNORECASE,
)

STEP_NAMES = {
    "generate": 1,
    "prompts": 1,
    "mcp": 2,
    "extract": 3,
    "extraction": 3,
    "kg": 4,
    "score": 5,
    "report": 5,
}


def repo_root() -> Path:
    return repository_root()


def campaign_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "generated" / CAMPAIGN_PATH_NAME


def pdf_stem(doi: str) -> str:
    return str(doi or "").strip().replace("/", "_")


def load_papers(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    papers = payload.get("papers") if isinstance(payload, dict) else payload
    if not isinstance(papers, list):
        raise ValueError(f"paper list is missing in {path}")
    rows: list[dict[str, str]] = []
    for item in papers:
        if not isinstance(item, dict):
            continue
        paper_hash = str(item.get("hash") or "").strip()
        doi = str(item.get("doi") or "").strip()
        if paper_hash:
            rows.append({"hash": paper_hash, "doi": doi})
    return rows


def select_eval_cases(count: int, *, kind: str, root: Path | None = None) -> list[dict[str, str]]:
    if count < MIN_CASES or count > MAX_CASES:
        raise ValueError(f"cases must be {MIN_CASES}-{MAX_CASES}, got {count}")
    root = root or repo_root()
    relative = MAIN_PAPERS if kind == "main" else ONTOMED_PAPERS
    papers = load_papers(root / relative)
    if len(papers) < count:
        raise ValueError(f"{kind} paper list has {len(papers)} entries; need {count}")
    return papers[:count]


def parse_step(value: str | int) -> int:
    text = str(value).strip().lower()
    if text in STEP_NAMES:
        return STEP_NAMES[text]
    try:
        number = int(text)
    except ValueError as exc:
        raise ValueError(
            "step must be 1-5 or generate/mcp/extract/kg/score"
        ) from exc
    if number not in {1, 2, 3, 4, 5}:
        raise ValueError("step must be 1-5")
    return number


def missing_pdfs(cases: list[dict[str, str]], pdf_dir: Path) -> list[str]:
    missing: list[str] = []
    for paper in cases:
        stem = pdf_stem(paper["doi"])
        path = pdf_dir / f"{stem}.pdf"
        if not path.is_file():
            missing.append(f"{paper['hash']} ({stem}.pdf)")
    return missing


def load_campaign(root: Path | None = None) -> dict[str, Any]:
    path = campaign_path(root)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def save_campaign(payload: dict[str, Any], *, root: Path | None = None) -> Path:
    path = campaign_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def active_generation_root(root: Path | None = None) -> Path:
    root = root or repo_root()
    current = read_current_pointer(repository=root)
    if current is not None:
        return current
    return resolve_generated_package_root(repository=root)


def latest_scenario_run(scenario: str, tag: str, *, root: Path | None = None) -> Path | None:
    folder = (root or repo_root()) / "scenarios" / scenario / "runs"
    if not folder.is_dir():
        return None
    matches = [
        path
        for path in folder.iterdir()
        if path.is_dir()
        and not path.name.startswith("ox_")
        and (path.name == tag or path.name.endswith(f"_{tag}"))
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda path: path.name, reverse=True)[0]


def bounded_workers(requested: int, n_items: int) -> int:
    count = max(1, int(requested or 1))
    if n_items <= 0:
        return 1
    return min(count, n_items)


def hash_cli(hashes: list[str]) -> list[str]:
    out: list[str] = []
    for paper_hash in hashes:
        out.extend(["--hash", paper_hash])
    return out


def parse_fine_grained_f1(markdown: str) -> float | None:
    text = markdown or ""
    for pattern in (
        FINE_GRAINED_RE,
        CURRENT_OVERALL_RE,
        COMBINED_SUMMARY_RE,
        TABLE_OVERALL_F1_RE,
    ):
        matches = pattern.findall(text)
        if matches:
            return float(matches[-1])
    return None


def chemistry_score_table(scores_dir: Path) -> dict[str, float | None]:
    table: dict[str, float | None] = {}
    for name in ("scoring_chemicals", "scoring_steps", "scoring_characterisation", "scoring_cbu"):
        report = scores_dir / name / "_overall.md"
        if report.is_file():
            table[name] = parse_fine_grained_f1(report.read_text(encoding="utf-8"))
        else:
            table[name] = None
    return table


def format_score_report(
    *,
    cases: int,
    main_scores: dict[str, float | None] | None,
    medical: dict[str, Any] | None,
    main_run: str = "",
    medical_run: str = "",
) -> str:
    lines = [
        "DEFAULT PIPELINE SCORES",
        f"protocol: {PROTOCOL}",
        f"cases: {cases}",
        "",
    ]
    if main_scores is not None:
        lines.append(f"Main (OntoSynthesis)  {main_run}")
        for key in ("scoring_chemicals", "scoring_steps", "scoring_characterisation", "scoring_cbu"):
            value = main_scores.get(key)
            label = key.replace("scoring_", "")
            rendered = f"{value:.3f}" if isinstance(value, float) else "n/a"
            note = "  (main-only KG; CBU often 0)" if key == "scoring_cbu" else ""
            lines.append(f"  {label:18} F1={rendered}{note}")
        lines.append("")
    if medical is not None:
        lines.append(f"OntoMed               {medical_run}")
        overall = medical.get("overall_accuracy")
        mean = medical.get("mean_per_case_accuracy")
        n_cases = medical.get("n_cases")
        lines.append(
            f"  overall accuracy    {overall:.1%}"
            if isinstance(overall, float)
            else "  overall accuracy    n/a"
        )
        lines.append(
            f"  mean per-case       {mean:.1%}"
            if isinstance(mean, float)
            else "  mean per-case       n/a"
        )
        if n_cases is not None:
            lines.append(f"  matched cases       {n_cases}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_logged(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> int:
    display = subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(argv)
    print(f"\n$ {display}", flush=True)
    if dry_run:
        return 0
    completed = subprocess.run(argv, cwd=str(cwd or repo_root()), env=env, check=False)
    return int(completed.returncode)


def command_succeeded(returncode: int) -> bool:
    return returncode == 0 or returncode in WINDOWS_ACCESS_VIOLATION


def marker_complete(runtime: Path, hashes: list[str], marker: str) -> tuple[bool, list[str]]:
    missing = [
        paper_hash
        for paper_hash in hashes
        if not (runtime / paper_hash / marker).is_file()
    ]
    return not missing, missing


def child_env(root: Path | None = None) -> dict[str, str]:
    root = root or repo_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["TWA_LLM_SEED"] = env.get("TWA_LLM_SEED") or "42"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def required_env_keys(root: Path | None = None) -> list[str]:
    values = dict(os.environ)
    env_path = (root or repo_root()) / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return [
        key
        for key in ("REMOTE_BASE_URL", "REMOTE_API_KEY")
        if not str(values.get(key) or "").strip()
    ]


def python_executable() -> str:
    return sys.executable or "python"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
