"""Hash × step loop. Fresh run wipes the scenario runtime unless --resume."""

from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from models.generated_layout import resolve_generated_package_root
from models.locations import repository_root
from src.extraction_runtime.discovery import copy_pdfs_to_data_dir, discover_dois
from src.extraction_runtime.domain_binding import RuntimeDomain
from src.extraction_runtime.agent.env import apply_kg_protocol_environment
from src.kg_building.experiment_protocol import lock_revision_policy
from src.extraction_runtime.mcp.test_launch import setup_test_mcp_configs
from src.extraction_runtime.runtime_cleanup import prepare_pipeline_runtime
from src.extraction_runtime.steps.registry import load_step_module
from src.extraction_runtime.locked_mechanisms import (
    ONE_SHOT_ENV,
    extraction_revision_enabled,
    one_shot_extraction_enabled,
)
from src.extraction_runtime.slim_extract_defaults import (
    apply_slim_ontosynthesis_extract_defaults,
)
from src.extraction_runtime.tolerate import extraction_steps_complete


DEFAULT_WORKERS = 5


def bounded_workers(requested: int, n_items: int) -> int:
    count = max(1, int(requested or 1))
    if n_items <= 0:
        return 1
    return min(count, n_items)


PIPELINE_ONLY_KEYS = {
    "steps",
    "mode",
    "description",
    "step_configs",
    "input_dir",
    "data_dir",
    "domain",
    "ontology",
    "domain_config",
    "generated_artifact_root",
    "generation_run",
    "experiment_protocol",
    "pipeline_kg",
    "extraction_model",
    "kg_model",
    "kg_seed",
    "official_onepass_guidance",
    "extraction_revision",
    "kg_revision",
    "disable_kg_revisions",
    "kg_max_attempts",
    "kg_hint_revision_max_attempts",
    "post_publish_structural_retries",
    "continuity_audit_retries",
    "continuity_audit",
    "presence_coverage_audit",
    "compare_one_shot",
}


def _step_config(
    config: dict[str, Any],
    *,
    data_dir: str,
    domain: RuntimeDomain,
    step_name: str,
    test_mcp_config_name: str | None,
) -> dict[str, Any]:
    step_config = {
        key: value
        for key, value in config.items()
        if key not in PIPELINE_ONLY_KEYS
    }
    step_config["data_dir"] = data_dir
    step_config["domain"] = domain
    step_config["ontology_name"] = domain.ontology_name
    step_config["execution_profile"] = domain.execution_profile
    step_config["vision_required"] = domain.vision_required
    step_config.update((config.get("step_configs") or {}).get(step_name) or {})
    if test_mcp_config_name:
        step_config["test_mcp_config"] = test_mcp_config_name
    if str(config.get("extraction_model") or "").strip():
        step_config["extraction_model"] = str(config["extraction_model"]).strip()
    if str(config.get("kg_model") or "").strip():
        step_config["kg_model"] = str(config["kg_model"]).strip()
    protocol = str(config.get("experiment_protocol") or "").strip()
    if protocol:
        # PIPELINE_ONLY_KEYS strips this from the bulk copy. KG steps need it
        # so KG steps can append protocol extras (noprompt T-Box, with-prompt
        # guidance, full-prompt ONEPASS).
        step_config["experiment_protocol"] = protocol
    return step_config


def run_paper_steps(
    *,
    doi_hash: str,
    steps: list[str],
    config: dict[str, Any],
    data_dir: str,
    domain: RuntimeDomain,
    test_mcp_config_name: str | None = None,
) -> None:
    """Run every configured step. Defects are logged; later steps still run."""
    doi_folder = Path(data_dir) / doi_hash
    doi_folder.mkdir(parents=True, exist_ok=True)
    for step_name in steps:
        print(f"\n[STEP] {step_name}")
        step_module = load_step_module(step_name)
        if not step_module:
            print(f"[WARN] Step '{step_name}' is unavailable; continuing")
            continue
        step_config = _step_config(
            config,
            data_dir=data_dir,
            domain=domain,
            step_name=step_name,
            test_mcp_config_name=test_mcp_config_name,
        )
        try:
            success = step_module.run_step(doi_hash, step_config)
        except Exception as exc:
            print(f"[WARN] Step '{step_name}' raised; continuing remaining steps: {exc}")
            import traceback

            traceback.print_exc()
            continue
        if not success:
            print(
                f"[WARN] Step '{step_name}' was incomplete for {doi_hash}; "
                "continuing remaining steps"
            )


def process_doi(
    *,
    doi_hash: str,
    steps: list[str],
    config: dict[str, Any],
    data_dir: str,
    domain: RuntimeDomain,
    test_mcp_config_name: str | None = None,
) -> bool:
    """Run one paper, then retry the full step list once if extraction is incomplete."""
    doi_folder = Path(data_dir) / doi_hash
    run_paper_steps(
        doi_hash=doi_hash,
        steps=steps,
        config=config,
        data_dir=data_dir,
        domain=domain,
        test_mcp_config_name=test_mcp_config_name,
    )
    if extraction_steps_complete(doi_folder, steps):
        return True
    print(
        f"\n[WARN] Extraction incomplete for {doi_hash}; "
        "retrying the full step list once"
    )
    run_paper_steps(
        doi_hash=doi_hash,
        steps=steps,
        config=config,
        data_dir=data_dir,
        domain=domain,
        test_mcp_config_name=test_mcp_config_name,
    )
    if extraction_steps_complete(doi_folder, steps):
        print(f"[OK] Extraction completed on retry: {doi_hash}")
        return True
    print(
        f"[WARN] Extraction still incomplete after retry for {doi_hash}; "
        "keeping partial artifacts"
    )
    return True


def process_hashes(
    doi_hashes: list[str],
    *,
    steps: list[str],
    config: dict[str, Any],
    data_dir: str,
    domain: RuntimeDomain,
    test_mcp_config_name: str | None,
    max_workers: int,
) -> list[str]:
    """Run papers with up to ``max_workers`` threads. Returns incomplete hashes in input order."""
    workers = bounded_workers(max_workers, len(doi_hashes))
    print(f"[INFO] Paper workers: {workers} (of {len(doi_hashes)} hashes)\n")

    def _one(doi_hash: str) -> tuple[str, bool]:
        print(f"\n{'=' * 60}")
        print(f"Processing: {doi_hash}")
        print(f"{'=' * 60}")
        try:
            process_doi(
                doi_hash=doi_hash,
                steps=steps,
                config=config,
                data_dir=data_dir,
                domain=domain,
                test_mcp_config_name=test_mcp_config_name,
            )
        except Exception as exc:
            print(f"[WARN] {doi_hash} raised; keeping partial artifacts: {exc}")
            traceback.print_exc()
        complete = extraction_steps_complete(Path(data_dir) / doi_hash, steps)
        print(f"\nCompleted: {doi_hash}")
        return doi_hash, complete

    complete_by_hash: dict[str, bool] = {}
    if workers == 1:
        for doi_hash in doi_hashes:
            paper, ok = _one(doi_hash)
            complete_by_hash[paper] = ok
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, doi_hash) for doi_hash in doi_hashes]
            for doi_hash, future in zip(doi_hashes, futures):
                paper, ok = future.result()
                complete_by_hash[paper] = ok
    return [doi_hash for doi_hash in doi_hashes if not complete_by_hash.get(doi_hash)]


def run_pipeline(
    *,
    config: dict[str, Any],
    config_path: str | Path,
    domain: RuntimeDomain,
    input_dir: str | None = None,
    only_hashes: list[str] | None = None,
    use_test_mcp: bool = False,
    resume_existing_runtime: bool = False,
    vision_override: bool | None = None,
    max_workers: int = DEFAULT_WORKERS,
) -> bool:
    apply_slim_ontosynthesis_extract_defaults(config, steps_were_explicit=True)
    config["execution_profile"] = domain.execution_profile
    config["workflow_profile"] = domain.workflow_profile
    lock_revision_policy(config)
    if config.get("compare_one_shot"):
        os.environ[ONE_SHOT_ENV] = "1"
    if vision_override is not None:
        config["vision_pdf_conversion"] = vision_override
    elif domain.vision_required and "vision_pdf_conversion" not in config:
        config["vision_pdf_conversion"] = True

    artifact_root = str(config.get("generated_artifact_root") or "").strip()
    if artifact_root:
        resolved_root = resolve_generated_package_root(explicit=artifact_root)
    else:
        resolved_root = resolve_generated_package_root()
    config["generated_artifact_root"] = str(resolved_root)
    os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(resolved_root)

    data_dir = config.get("data_dir") or ""
    if not data_dir:
        raise ValueError("run config is missing data_dir")
    if input_dir is None:
        input_dir = config.get("input_dir") or ""
    if not input_dir:
        raise ValueError("run config is missing input_dir")

    steps = list(config.get("steps") or domain.default_steps)
    print("\n" + "=" * 60)
    print(
        "Extraction runtime"
        + (" [TEST MODE - generated MCP]" if use_test_mcp else "")
    )
    print("=" * 60)
    print(f"Domain: {domain.ontology_name} ({domain.execution_profile})")
    print(f"Config: {config_path}")
    print(f"Steps: {', '.join(steps)}")
    print(f"Input: {input_dir}")
    print(f"Generated artifacts: {resolved_root}")
    protocol = str(config.get("experiment_protocol") or "").strip()
    if protocol:
        print(f"Protocol: {protocol} (pipeline KG = official no-contract)")
    extraction_on = extraction_revision_enabled(config)
    print(
        "Revision lock: extraction_revision="
        + ("on" if extraction_on else "off (simple_main)")
        + ", kg_revision=off"
        + (
            "; COMPARISON ABLATION one-shot "
            "(LLM judges skipped; empty/invalid payloads still retry)"
            if one_shot_extraction_enabled()
            else (
                "; simple ontology skips extraction judges "
                "(empty/invalid payloads still retry)"
                if not extraction_on
                else ""
            )
        )
    )
    print("=" * 60 + "\n")

    repo = repository_root()
    try:
        runtime_path = prepare_pipeline_runtime(
            data_dir=data_dir,
            repository_root=repo,
            config_path=config_path,
            selected_hashes=only_hashes,
            resume_existing_runtime=resume_existing_runtime,
            reuse_conversion_artifacts_from=config.get(
                "reuse_conversion_artifacts_from"
            ),
        )
    except (OSError, ValueError) as exc:
        print(f"[FAIL] Runtime initialization refused: {exc}")
        return False

    data_dir = str(runtime_path)
    os.environ["TWA_AGENTIC_DATA_DIR"] = str(Path(data_dir).resolve())
    os.environ["TWA_CENTRAL_MEMORY_DIR"] = str(
        (Path(data_dir) / "central_memory").resolve()
    )
    os.environ["TWA_MAIN_ONTOLOGY_NAME"] = domain.pipeline_main_ontology_name
    apply_kg_protocol_environment(force=bool(str(config.get("experiment_protocol") or "").strip()))

    if resume_existing_runtime:
        print(f"[WARN] Resume mode: preserving existing runtime at {data_dir}\n")
    else:
        print(f"[OK] Fresh runtime initialized: {data_dir}\n")

    test_mcp_config_name = None
    if use_test_mcp:
        print("[INFO] Setting up test MCP configuration from mcp_capabilities")
        try:
            test_mcp_config_name = setup_test_mcp_configs(domain, data_dir=data_dir)
        except Exception as exc:
            print(f"[FAIL] Could not setup test MCP configuration: {exc}")
            return False

    print("[INFO] Discovering DOIs from input directory...")
    doi_mapping = discover_dois(input_dir, data_dir)
    if not doi_mapping:
        print("[FAIL] No DOIs found or discovery failed")
        return False

    if only_hashes:
        doi_hashes = only_hashes
        print(f"[INFO] Processing {len(doi_hashes)} specific hash(es)\n")
    else:
        doi_hashes = list(doi_mapping.values())
        print(f"[INFO] Processing all {len(doi_hashes)} DOIs\n")

    print("[INFO] Copying PDFs into the runtime")
    for doi, doi_hash in doi_mapping.items():
        if only_hashes and doi_hash not in only_hashes:
            continue
        copy_pdfs_to_data_dir(doi, doi_hash, input_dir, data_dir)
    print()

    incomplete = process_hashes(
        doi_hashes,
        steps=steps,
        config=config,
        data_dir=data_dir,
        domain=domain,
        test_mcp_config_name=test_mcp_config_name,
        max_workers=max_workers,
    )

    print(f"\n{'=' * 60}")
    if incomplete:
        print(
            "[WARN] Pipeline completed with incomplete extraction: "
            + ", ".join(incomplete)
        )
    else:
        print("[OK] Pipeline completed")
    print(f"{'=' * 60}\n")
    return True
