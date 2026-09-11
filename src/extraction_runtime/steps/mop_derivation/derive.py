"""Run post-publish CBU derivation via the scorer-repo orchestrator.

Work-repo agents are not vendored here. The scorer checkout owns metal,
organic, integration, and MOP-formula workers. This step must not mark
success as `skipped_missing_derivation_agents` when those workers exist.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.kg_building.experiment_protocol import resolve_scorer_repo

_LAUNCHER = (
    "from src.pipelines.mop_derivation.derive import run_step; "
    "import sys; "
    "ok = run_step(sys.argv[1], {'data_dir': sys.argv[2], 'project_root': sys.argv[3]}); "
    "raise SystemExit(0 if ok else 1)"
)


def _ontomops_ttls(doi_folder: Path) -> list[Path]:
    output_dir = doi_folder / "ontomops_output"
    if not output_dir.is_dir():
        return []
    return sorted(
        path
        for path in output_dir.glob("ontomops_extension_*.ttl")
        if path.is_file()
    )


def _has_derived_cbu_formulas(doi_folder: Path) -> bool:
    for side in ("metal", "organic"):
        structured = doi_folder / "cbu_derivation" / side / "structured"
        if structured.is_dir() and any(structured.glob("*.json")):
            return True
    return False


def _scorer_derivation_env(runtime_root: Path, scorer: Path) -> dict[str, str]:
    log_dir = runtime_root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("TWA_GENERATED_ARTIFACT_ROOT", None)
    env.update(
        {
            "DATA_DIR": str(runtime_root),
            "DATA_LOG_DIR": str(log_dir),
            "DATA_CCDC_DIR": str(scorer / "data" / "ontologies" / "ccdc"),
            "CBU_DATABASE_PATH": str(
                scorer / "data" / "ontologies" / "full_cbus_with_canonical_smiles_updated.csv"
            ),
            "TWA_AGENTIC_DATA_DIR": str(runtime_root),
            "TWA_SCORER_ROOT": str(scorer),
            "PYTHONPATH": str(scorer),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "ROOT_DIR": str(scorer),
        }
    )
    return env


def run_step(doi_hash: str, config: dict) -> bool:
    data_dir = config.get("data_dir", "data")
    doi_folder = Path(data_dir) / doi_hash
    print(f">> Derivation: {doi_hash}")
    ttl_files = _ontomops_ttls(doi_folder)
    marker = doi_folder / ".mop_derivation_done"
    if not ttl_files:
        print("  [SKIP] No published OntoMOPs TTL")
        marker.write_text("skipped_no_ontomops_ttl\n", encoding="utf-8")
        return True

    newest_source = max(path.stat().st_mtime for path in ttl_files)
    if marker.is_file() and marker.stat().st_mtime >= newest_source:
        body = marker.read_text(encoding="utf-8").strip()
        if body != "skipped_missing_derivation_agents":
            print("  [SKIP] Derivation already completed")
            return True
        print("  [INFO] Replacing no-op skip marker with scorer CBU derivation")

    raw_scorer = (os.environ.get("TWA_SCORER_ROOT") or "").strip()
    scorer = resolve_scorer_repo(raw_scorer or None)
    if scorer is None or not (
        scorer / "src" / "pipelines" / "mop_derivation" / "derive.py"
    ).is_file():
        print("  [WARN] Scorer mop_derivation orchestrator not found")
        marker.write_text("skipped_missing_derivation_agents\n", encoding="utf-8")
        return True

    runtime_root = Path(data_dir).resolve()
    print(f"  [INFO] Delegating to scorer mop_derivation {scorer}")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _LAUNCHER,
            doi_hash,
            str(runtime_root),
            str(scorer),
        ],
        cwd=str(scorer),
        env=_scorer_derivation_env(runtime_root, scorer),
        check=False,
    )
    if completed.returncode == 0 and marker.is_file():
        print(f"[OK] Derivation completed: {doi_hash}")
        return True
    if _has_derived_cbu_formulas(doi_folder):
        marker.write_text("completed_cbu_formulas\n", encoding="utf-8")
        print(
            f"[WARN] Scorer orchestrator exited {completed.returncode}; "
            "metal/organic CBU formulas are present, marking derivation complete"
        )
        return True
    print(f"  [WARN] scorer mop_derivation exited {completed.returncode}")
    return False
