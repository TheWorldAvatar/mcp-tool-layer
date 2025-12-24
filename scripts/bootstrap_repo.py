#!/usr/bin/env python3
"""
Bootstrap script for a fresh clone.

This repo ignores many "runtime" folders (data caches, generated artifacts, logs).
Some modules (e.g. `models/locations.py`) require certain directories to exist at import time.

Run:
  python scripts/bootstrap_repo.py

Optional:
  python scripts/bootstrap_repo.py --with-grounding-cache ontospecies
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _touch_gitkeep(p: Path) -> None:
    """
    Optionally drop a .gitkeep so empty directories can be retained locally if desired.
    (We do NOT add it automatically because these folders are typically gitignored.)
    """
    p.write_text("", encoding="utf-8")


def bootstrap_dirs(
    *,
    root: Path,
    extra_dirs: Iterable[Path] = (),
    create_gitkeep: bool = False,
) -> List[Path]:
    created: List[Path] = []

    # Minimum required for imports and basic pipeline runs
    base_dirs = [
        root / "configs",
        root / "data",
        root / "data" / "log",
        root / "data" / "ontologies",
        root / "raw_data",
    ]

    # Common runtime working directories (gitignored)
    generated_dirs = [
        root / "sandbox",
        root / "sandbox" / "tasks",
        root / "sandbox" / "code",
        root / "tmp",
        root / "evaluation" / "data",
        root / "archive",
        # AI-generated artifacts (gitignored)
        root / "ai_generated_contents",
        root / "ai_generated_contents" / "prompts",
        root / "ai_generated_contents" / "iterations",
        root / "ai_generated_contents" / "sparqls",
        root / "ai_generated_contents_candidate",
        root / "ai_generated_contents_candidate" / "prompts",
        root / "ai_generated_contents_candidate" / "iterations",
        root / "ai_generated_contents_candidate" / "scripts",
        root / "ai_generated_contents_candidate" / "ontology_structures",
        root / "ai_generated_contents_reference",
        root / "ai_generated_contents_reference" / "prompts",
        root / "ai_generated_contents_reference" / "iterations",
        root / "ai_generated_contents_reference" / "scripts",
        root / "ai_generated_contents_reference" / "sparqls",
    ]

    for p in [*base_dirs, *generated_dirs, *list(extra_dirs)]:
        if not p.exists():
            _mkdir(p)
            created.append(p)
        elif p.is_file():
            raise RuntimeError(f"Expected directory but found file: {p}")

        if create_gitkeep:
            gp = p / ".gitkeep"
            if not gp.exists():
                _touch_gitkeep(gp)

    return created


def main() -> None:
    ap = argparse.ArgumentParser(description="Create gitignored runtime directories required by this repo.")
    ap.add_argument(
        "--root",
        default=".",
        help="Repo root (default: current directory)",
    )
    ap.add_argument(
        "--with-grounding-cache",
        action="append",
        default=[],
        help="Also create data/grounding_cache/<name>/{labels,resume} (repeatable). Example: --with-grounding-cache ontospecies",
    )
    ap.add_argument(
        "--gitkeep",
        action="store_true",
        help="Create empty .gitkeep files inside created directories (usually unnecessary).",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    extra: List[Path] = []
    for name in args.with_grounding_cache:
        n = str(name).strip()
        if not n:
            continue
        extra.extend(
            [
                root / "data" / "grounding_cache" / n,
                root / "data" / "grounding_cache" / n / "labels",
                root / "data" / "grounding_cache" / n / "resume",
            ]
        )

    created = bootstrap_dirs(root=root, extra_dirs=extra, create_gitkeep=bool(args.gitkeep))
    if created:
        print("Created directories:")
        for p in created:
            print(f"- {p.relative_to(root)}")
    else:
        print("No directories needed creating (already present).")


if __name__ == "__main__":
    main()


