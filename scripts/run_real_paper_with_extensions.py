from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOI = "10.1021/acsami.7b18836"
DOI_HASH = "0c57bac8"
SOURCE_STEM = "10.1021_acsami.7b18836"
SOURCE_DIR = ROOT / "scenarios" / "mops" / "datasets" / "eval30"
META_CONFIG = ROOT / "configs" / "meta_task" / "meta_task_config.json"
EXTRACTION_CONFIG = ROOT / "configs" / "extraction_models.json"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)


def write_launcher(path: Path, *, artifact_root: Path, ontology: str) -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import sys",
                "from pathlib import Path",
                f"ARTIFACT_ROOT = Path({str(artifact_root)!r})",
                "sys.path.insert(0, str(ARTIFACT_ROOT))",
                f"from scripts.{ontology}.main import mcp",
                'mcp.run(transport="stdio")',
                "",
            ]
        ),
        encoding="utf-8",
    )


def patch_extension_mcp_set_name(artifact_root: Path, mcp_relpath: str) -> None:
    for ontology in ("ontomops", "ontospecies"):
        path = artifact_root / "iterations" / ontology / "iterations.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        iterations = data if isinstance(data, list) else data.get("iterations") or []
        for iteration in iterations:
            if isinstance(iteration, dict):
                iteration["mcp_set_name"] = mcp_relpath
        if isinstance(data, list):
            path.write_text(json.dumps(iterations, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            data["iterations"] = iterations
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def prepare_runtime(
    *,
    work_root: Path,
    artifact_root: Path,
    runtime_root: Path,
    reuse_runtime_from: Path | None,
    run_main: bool,
) -> str:
    case_dir = runtime_root / DOI_HASH
    if reuse_runtime_from is not None:
        source_case = reuse_runtime_from / DOI_HASH
        if not source_case.is_dir():
            raise FileNotFoundError(f"reuse runtime case missing: {source_case}")
        if case_dir.exists():
            shutil.rmtree(case_dir)
        shutil.copytree(source_case, case_dir)
        for marker in (
            ".extensions_extractions_done",
            ".extensions_kg_building_done",
        ):
            (case_dir / marker).unlink(missing_ok=True)
        for name in ("ontomops_output", "ontospecies_output", "mcp_run_ontomops", "mcp_run_ontospecies"):
            path = case_dir / name
            if path.exists():
                shutil.rmtree(path)
    else:
        case_dir.mkdir(parents=True, exist_ok=True)
        main_pdf = case_dir / f"{DOI_HASH}.pdf"
        if not main_pdf.is_file():
            _copy_file(SOURCE_DIR / f"{SOURCE_STEM}.pdf", main_pdf)
        si_source = SOURCE_DIR / f"{SOURCE_STEM}_si.pdf"
        si_pdf = case_dir / f"{DOI_HASH}_si.pdf"
        if si_source.is_file() and not si_pdf.is_file():
            _copy_file(si_source, si_pdf)

    write_json(runtime_root / "doi_to_hash.json", {DOI: DOI_HASH})

    main_launcher = runtime_root / "launch_ontosynthesis_mcp.py"
    write_launcher(main_launcher, artifact_root=artifact_root, ontology="ontosynthesis")
    mops_launcher = runtime_root / "launch_ontomops_mcp.py"
    write_launcher(mops_launcher, artifact_root=artifact_root, ontology="ontomops")
    species_launcher = runtime_root / "launch_ontospecies_mcp.py"
    write_launcher(species_launcher, artifact_root=artifact_root, ontology="ontospecies")

    main_mcp = {
        "llm_created_mcp": {
            "command": sys.executable,
            "args": [str(main_launcher)],
            "transport": "stdio",
            "cwd": str(ROOT),
            "env": {
                "TWA_AGENTIC_DATA_DIR": str(runtime_root),
                "TWA_GENERATED_ARTIFACT_ROOT": str(artifact_root),
                "TWA_REQUIRE_GENERATED_ARTIFACT_ROOT": "1",
            },
        }
    }
    extension_mcp = {
        "mops_extension": {
            "command": sys.executable,
            "args": [str(mops_launcher)],
            "transport": "stdio",
            "cwd": str(ROOT),
            "env": {
                "TWA_AGENTIC_DATA_DIR": str(runtime_root),
                "TWA_GENERATED_ARTIFACT_ROOT": str(artifact_root),
                "TWA_REQUIRE_GENERATED_ARTIFACT_ROOT": "1",
            },
        },
        "ontospecies_extension": {
            "command": sys.executable,
            "args": [str(species_launcher)],
            "transport": "stdio",
            "cwd": str(ROOT),
            "env": {
                "TWA_AGENTIC_DATA_DIR": str(runtime_root),
                "TWA_GENERATED_ARTIFACT_ROOT": str(artifact_root),
                "TWA_REQUIRE_GENERATED_ARTIFACT_ROOT": "1",
            },
        },
        "ccdc": {
            "command": sys.executable,
            "args": ["-m", "src.mcp_servers.ccdc.main"],
            "transport": "stdio",
            "cwd": str(ROOT),
        },
    }
    write_json(runtime_root / "runtime_mcp.json", main_mcp)
    write_json(runtime_root / "extension_mcp.json", extension_mcp)

    extension_rel = os.path.relpath(
        runtime_root / "extension_mcp.json", ROOT / "configs"
    ).replace("\\", "/")
    patch_extension_mcp_set_name(artifact_root, extension_rel)

    if run_main:
        return os.path.relpath(
            runtime_root / "runtime_mcp.json", ROOT / "configs"
        ).replace("\\", "/")
    return extension_rel


def load_model_mapping(artifact_root: Path, runtime_root: Path) -> dict[str, object]:
    meta = json.loads(META_CONFIG.read_text(encoding="utf-8"))
    extraction = json.loads(EXTRACTION_CONFIG.read_text(encoding="utf-8"))
    extensions = meta.get("ontologies", {}).get("extensions") or []
    return {
        "top_entity_kg_building": meta["ontologies"]["main"]["agent_model"],
        "main_kg_building": meta["ontologies"]["main"]["agent_model"],
        "extension_kg_building": {
            str(item.get("name")): item.get("agent_model") for item in extensions
        },
        "extraction_config": extraction,
        "artifact_root": str(artifact_root),
        "runtime_root": str(runtime_root),
        "case_dir": str(runtime_root / DOI_HASH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run OntoSynthesis paper pipeline including ontomops/ontospecies extensions."
    )
    parser.add_argument("--work-root", required=True, help="Isolated work directory")
    parser.add_argument(
        "--artifact-root",
        required=True,
        help="Generated artifact root containing ontosynthesis + extension packages",
    )
    parser.add_argument(
        "--reuse-runtime-from",
        default="",
        help="Optional existing runtime root to copy (skips main pipeline when set with --extensions-only)",
    )
    parser.add_argument(
        "--extensions-only",
        action="store_true",
        help="Skip main OntoSynthesis stages and only run extension extraction/KG building",
    )
    parser.add_argument(
        "--skip-extensions",
        action="store_true",
        help="Run only the main OntoSynthesis stages",
    )
    parser.add_argument(
        "--extension-agent-model",
        default="",
        help="Override extension KG-building agent_model for ontomops/ontospecies",
    )
    args = parser.parse_args()

    work_root = Path(args.work_root).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    runtime_root = (work_root / "runtime").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "doi": DOI,
        "doi_hash": DOI_HASH,
        "artifact_root": str(artifact_root),
        "runtime_root": str(runtime_root),
        "case_dir": str(runtime_root / DOI_HASH),
        "steps": {},
    }
    summary_path = work_root / "pipeline_summary.json"

    try:
        if not artifact_root.is_dir():
            raise FileNotFoundError(f"artifact root missing: {artifact_root}")
        for ontology in ("ontosynthesis", "ontomops", "ontospecies"):
            if not (artifact_root / "iterations" / ontology / "iterations.json").is_file():
                if ontology == "ontosynthesis" and args.extensions_only:
                    continue
                if ontology != "ontosynthesis" and args.skip_extensions:
                    continue
                if ontology != "ontosynthesis" and not args.skip_extensions:
                    raise FileNotFoundError(
                        f"missing extension iterations: {artifact_root / 'iterations' / ontology / 'iterations.json'}"
                    )

        reuse_from = (
            Path(args.reuse_runtime_from).resolve()
            if str(args.reuse_runtime_from or "").strip()
            else None
        )
        mcp_config_name = prepare_runtime(
            work_root=work_root,
            artifact_root=artifact_root,
            runtime_root=runtime_root,
            reuse_runtime_from=reuse_from,
            run_main=not args.extensions_only,
        )
        meta_task_config_path = META_CONFIG
        extension_agent_model = str(args.extension_agent_model or "").strip()
        if extension_agent_model:
            meta_payload = json.loads(META_CONFIG.read_text(encoding="utf-8"))
            for item in (meta_payload.get("ontologies") or {}).get("extensions") or []:
                if isinstance(item, dict) and str(item.get("name") or "") in {
                    "ontomops",
                    "ontospecies",
                }:
                    item["agent_model"] = extension_agent_model
            for ontology in ("ontomops", "ontospecies"):
                iterations_path = (
                    artifact_root / "iterations" / ontology / "iterations.json"
                )
                if not iterations_path.is_file():
                    continue
                iterations_payload = json.loads(
                    iterations_path.read_text(encoding="utf-8")
                )
                for iteration in iterations_payload.get("iterations") or []:
                    if isinstance(iteration, dict):
                        iteration["agent_model"] = extension_agent_model
                iterations_path.write_text(
                    json.dumps(iterations_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            meta_task_config_path = work_root / "meta_task_config.extensions.json"
            write_json(meta_task_config_path, meta_payload)
            summary["extension_agent_model"] = extension_agent_model
        summary["model_mapping"] = load_model_mapping(artifact_root, runtime_root)
        summary["mcp_config"] = str(runtime_root / "runtime_mcp.json")
        summary["extension_mcp_config"] = str(runtime_root / "extension_mcp.json")
        summary["meta_task_config"] = str(meta_task_config_path)
        write_json(summary_path, summary)

        os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(artifact_root)
        os.environ["TWA_AGENTIC_DATA_DIR"] = str(runtime_root)
        os.environ["TWA_REQUIRE_GENERATED_ARTIFACT_ROOT"] = "1"

        config = {
            "data_dir": str(runtime_root),
            "project_root": str(ROOT),
            "meta_task_config": str(meta_task_config_path),
            "test_mcp_config": mcp_config_name,
            "force_react_kg": True,
        }

        steps: list[tuple[str, object]] = []
        if not args.extensions_only:
            from src.pipelines.pdf_conversion.convert import run_step as pdf_conversion
            from src.pipelines.section_classification.classify import (
                run_step as section_classification,
            )
            from src.pipelines.stitching.stitch import run_step as stitching
            from src.pipelines.top_entity_extraction.extract import (
                run_step as top_entity_extraction,
            )
            from src.pipelines.top_entity_kg_building.build import (
                run_step as top_entity_kg_building,
            )
            from src.pipelines.main_ontology_extractions.extract import (
                run_step as main_ontology_extractions,
            )
            from src.pipelines.main_kg_building.build import run_step as main_kg_building

            steps.extend(
                [
                    ("pdf_conversion", pdf_conversion),
                    ("section_classification", section_classification),
                    ("stitching", stitching),
                    ("top_entity_extraction", top_entity_extraction),
                    ("top_entity_kg_building", top_entity_kg_building),
                    ("main_ontology_extractions", main_ontology_extractions),
                    ("main_kg_building", main_kg_building),
                ]
            )

        if not args.skip_extensions:
            from src.pipelines.extensions_extractions.extract import (
                run_step as extensions_extractions,
            )
            from src.pipelines.extensions_kg_building.build import (
                run_step as extensions_kg_building,
            )

            steps.extend(
                [
                    ("extensions_extractions", extensions_extractions),
                    ("extensions_kg_building", extensions_kg_building),
                ]
            )

        for name, step in steps:
            print(f"\n=== {name} ===", flush=True)
            ok = bool(step(DOI_HASH, config))
            summary["steps"][name] = ok  # type: ignore[index]
            write_json(summary_path, summary)
            if not ok:
                summary["ok"] = False
                summary["error"] = f"pipeline step failed: {name}"
                write_json(summary_path, summary)
                return 1

        case_dir = runtime_root / DOI_HASH
        summary["ttl_paths"] = sorted(
            str(path.resolve()) for path in case_dir.rglob("*.ttl")
        )
        summary["final_ttl_paths"] = sorted(
            str(path.resolve())
            for path in (case_dir / "ontosynthesis_output").glob("*.ttl")
        )
        summary["extension_ttl_paths"] = {
            "ontomops": sorted(
                str(path.resolve())
                for path in (case_dir / "ontomops_output").glob("*.ttl")
            )
            if (case_dir / "ontomops_output").exists()
            else [],
            "ontospecies": sorted(
                str(path.resolve())
                for path in (case_dir / "ontospecies_output").glob("*.ttl")
            )
            if (case_dir / "ontospecies_output").exists()
            else [],
        }
        summary["ok"] = True
        write_json(summary_path, summary)
        return 0
    except Exception as exc:
        summary["ok"] = False
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()
        write_json(summary_path, summary)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
