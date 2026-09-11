"""Run v2 mop derivation on an OX-published ontomops_output. OX does not derive."""

from __future__ import annotations

from pathlib import Path


def run_mop_derivation(*, data_dir: Path, paper_hash: str, config: dict | None = None) -> bool:
    from src.extraction_runtime.domain_binding import load_runtime_domain
    from src.extraction_runtime.steps.mop_derivation.derive import run_step

    domain = load_runtime_domain("ontosynthesis")
    step_config = {
        "data_dir": str(data_dir),
        "domain": domain,
        "ontology_name": domain.ontology_name,
        **(config or {}),
    }
    paper = Path(data_dir) / paper_hash
    ontomops = paper / "ontomops_output"
    if not ontomops.is_dir() or not list(ontomops.glob("ontomops_extension_*.ttl")):
        print(f"[SKIP] mop derivation: no ontomops_extension_*.ttl under {ontomops}")
        return True
    return bool(run_step(paper_hash, step_config))
