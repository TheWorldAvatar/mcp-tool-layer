"""Locate or fetch the read-only chemistry/OntoMed scoring checkout.

Other users should not set SCORER_REPO. setup.cmd / run.cmd look next to
this clone, then clone the archive branch of this GitHub repo into
``data/third_party_repos/`` and overlay gold files from ``data/scorer_assets/``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from models.locations import repository_root

SCORER_DIR_NAME = "MCP-enhanced-MOPs-Extraction_Reproduction"
LOCAL_SCORER_RELATIVE = Path("data") / "third_party_repos" / SCORER_DIR_NAME
ASSETS_RELATIVE = Path("data") / "scorer_assets"
DEFAULT_CLONE_URL = "https://github.com/TheWorldAvatar/mcp-tool-layer.git"
SCORER_BRANCHES = ("cleanup/workspace-archive-docs", "archive/pre-v2-main")
MEDICAL_GOLD = ASSETS_RELATIVE / "medical_cases_new_20260710_all30_corrected.csv"
MEDICAL_SCHEMA = ASSETS_RELATIVE / "medical_case_schema_de_non_flat_v3.ttl"
ENGINE_MARKERS = (
    Path("evaluation") / "scoring_steps.py",
    Path("scripts") / "merge_and_conversion_main.py",
    Path("scripts") / "medical_ttl_to_csv_sparql.py",
    Path("scripts") / "medical_score_predicted_vs_gold.py",
)


def _root(root: Path | None = None) -> Path:
    return root or repository_root()


def _read_env_file(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = root / ".env"
    if not env_path.is_file():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def scorer_engines_present(path: Path) -> bool:
    return path.is_dir() and all((path / marker).is_file() for marker in ENGINE_MARKERS)


def scorer_looks_valid(path: Path) -> bool:
    """Engines present. Gold is overlaid from this repo when missing."""
    return scorer_engines_present(path)


def _candidate_paths(explicit: str | Path | None, root: Path) -> list[Path]:
    home = Path.home()
    env_file = _read_env_file(root)
    env_scorer = str(os.environ.get("SCORER_REPO") or env_file.get("SCORER_REPO") or "").strip()
    names = (
        SCORER_DIR_NAME,
        "mcp-tool-layer",
        "MCP-enhanced-MOPs-Extraction_clean-v2",
    )
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if env_scorer:
        candidates.append(Path(env_scorer).expanduser())
    candidates.append(root / LOCAL_SCORER_RELATIVE)
    candidates.append(root.parent / SCORER_DIR_NAME)
    candidates.append(home / "Documents" / "GitHub" / SCORER_DIR_NAME)
    for name in names:
        candidates.append(home / "Documents" / "GitHub" / name)
        candidates.append(root.parent / name)
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser()
        if not resolved.is_absolute():
            resolved = (root / resolved).resolve()
        else:
            try:
                resolved = resolved.resolve()
            except OSError:
                continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def find_scorer_repo(explicit: str | Path | None = None, *, root: Path | None = None) -> Path | None:
    root = _root(root)
    for candidate in _candidate_paths(explicit, root):
        if scorer_engines_present(candidate):
            return candidate
    return None


def _origin_url(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return DEFAULT_CLONE_URL
    url = (completed.stdout or "").strip()
    return url or DEFAULT_CLONE_URL


def _clone_scorer(dest: Path, *, root: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    urls = []
    origin = _origin_url(root)
    if origin:
        urls.append(origin)
    if DEFAULT_CLONE_URL not in urls:
        urls.append(DEFAULT_CLONE_URL)
    for url in urls:
        for branch in SCORER_BRANCHES:
            print(
                f"[INFO] Cloning scorer engines: {url} ({branch}) -> {dest}",
                flush=True,
            )
            completed = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--single-branch",
                    "--branch",
                    branch,
                    url,
                    str(dest),
                ],
                check=False,
            )
            if completed.returncode == 0 and scorer_engines_present(dest):
                print(f"[OK] Scorer engines: {dest}", flush=True)
                return True
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
    return False


def overlay_scorer_assets(scorer: Path, *, root: Path | None = None) -> None:
    """Copy gold/GT from this repo so a fresh engine clone can score."""
    root = _root(root)
    assets = root / ASSETS_RELATIVE
    gt_src = assets / "full_ground_truth"
    gt_dest = scorer / "full_ground_truth"
    if gt_src.is_dir() and not (gt_dest / "steps").is_dir():
        shutil.copytree(gt_src, gt_dest, dirs_exist_ok=True)
        print(f"[OK] Overlayed scoring gold -> {gt_dest}", flush=True)


def ensure_scorer_repo(
    explicit: str | Path | None = None,
    *,
    root: Path | None = None,
    clone: bool = True,
) -> Path | None:
    root = _root(root)
    found = find_scorer_repo(explicit, root=root)
    if found is None and clone:
        dest = root / LOCAL_SCORER_RELATIVE
        if _clone_scorer(dest, root=root):
            found = dest
    if found is None:
        return None
    overlay_scorer_assets(found, root=root)
    return found


def main(argv: list[str] | None = None) -> int:
    del argv
    path = ensure_scorer_repo()
    if path is None:
        print(
            "[FAIL] Could not find or clone scoring engines. "
            "Need git access to this repository's origin "
            f"(branch {SCORER_BRANCHES[0]}).",
            flush=True,
        )
        return 1
    print(f"[OK] Scorer: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
