from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_generation_contract_bundle,
    write_generation_contract_bundle,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
    format_class_properties_markdown,
    parse_ontology_ttl,
)


DEFAULT_AGENTIC_OUTPUT_ROOT = Path("ai_generated_contents_agentic_candidate")
DEFAULT_ONTOLOGY_CONFIGS = {
    "ontosynthesis": Path("configs/meta_task/meta_task_config.json"),
    "ontomops": Path("configs/meta_task/meta_task_config.json"),
    "ontospecies": Path("configs/meta_task/meta_task_config.json"),
    "medical": Path("configs/meta_task/meta_task_config_medical_non_flat_v3.json"),
}


@dataclass(frozen=True)
class OntologySpec:
    name: str
    ttl_file: str
    meta_task_config_path: str
    role: str
    description: str = ""


@dataclass(frozen=True)
class AgenticGenerationContext:
    ontology: OntologySpec
    output_root: str
    ontology_structure_dir: str
    scripts_dir: str
    prompts_dir: str
    parsed_summary_path: str
    parsed_markdown_path: str
    contract_path: str
    integrity_profile_path: str
    report_path: str
    parsed: dict[str, Any]
    contract: dict[str, Any]
    integrity_profile: dict[str, Any]

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_meta_task_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Meta-task config must be a JSON object: {cfg_path}")
    return data


def resolve_default_config_for_ontology(ontology_name: str) -> Path:
    key = str(ontology_name or "").strip()
    if key not in DEFAULT_ONTOLOGY_CONFIGS:
        raise ValueError(
            f"No default meta-task config registered for {key!r}. "
            "Pass --meta-task-config explicitly."
        )
    return DEFAULT_ONTOLOGY_CONFIGS[key]


def resolve_ontology_spec(
    *,
    ontology_name: str,
    meta_task_config_path: str | Path,
) -> OntologySpec:
    cfg_path = Path(meta_task_config_path)
    cfg = load_meta_task_config(cfg_path)
    ontologies = (cfg.get("ontologies") or {}) if isinstance(cfg.get("ontologies"), dict) else {}
    candidates: list[tuple[str, dict[str, Any]]] = []
    main = ontologies.get("main")
    if isinstance(main, dict):
        candidates.append(("main", main))
    for ext in ontologies.get("extensions") or []:
        if isinstance(ext, dict):
            candidates.append(("extension", ext))

    for role, item in candidates:
        if str(item.get("name") or "").strip() == ontology_name:
            ttl_file = str(item.get("ttl_file") or "").strip()
            if not ttl_file:
                raise ValueError(f"Ontology {ontology_name!r} has no ttl_file in {cfg_path}")
            return OntologySpec(
                name=ontology_name,
                ttl_file=ttl_file,
                meta_task_config_path=str(cfg_path),
                role=role,
                description=str(item.get("description") or ""),
            )
    raise ValueError(f"Ontology {ontology_name!r} not found in {cfg_path}")


def build_agentic_generation_context(
    *,
    ontology_name: str,
    meta_task_config_path: str | Path | None = None,
    output_root: str | Path = DEFAULT_AGENTIC_OUTPUT_ROOT,
    write_files: bool = True,
) -> AgenticGenerationContext:
    cfg_path = Path(meta_task_config_path) if meta_task_config_path else resolve_default_config_for_ontology(ontology_name)
    spec = resolve_ontology_spec(ontology_name=ontology_name, meta_task_config_path=cfg_path)

    ttl_path = Path(spec.ttl_file)
    if not ttl_path.is_file():
        raise FileNotFoundError(f"Missing ontology TTL: {ttl_path}")

    parsed = parse_ontology_ttl(str(ttl_path))
    integrity_profile = extract_ontology_integrity_profile(str(ttl_path))
    contract = build_generation_contract_bundle(
        meta_task_config_path=cfg_path,
        ontology_name=ontology_name,
    )

    root = Path(output_root)
    structure_dir = root / "ontology_structures" / ontology_name
    scripts_dir = root / "scripts" / ontology_name
    prompts_dir = root / "prompts" / ontology_name
    parsed_summary_path = structure_dir / "parsed.json"
    parsed_markdown_path = structure_dir / "parsed.md"
    contract_path = structure_dir / "generation_contract.json"
    integrity_profile_path = structure_dir / "integrity_profile.json"
    report_path = root / "reports" / ontology_name / "generation_report.json"

    context = AgenticGenerationContext(
        ontology=spec,
        output_root=str(root),
        ontology_structure_dir=str(structure_dir),
        scripts_dir=str(scripts_dir),
        prompts_dir=str(prompts_dir),
        parsed_summary_path=str(parsed_summary_path),
        parsed_markdown_path=str(parsed_markdown_path),
        contract_path=str(contract_path),
        integrity_profile_path=str(integrity_profile_path),
        report_path=str(report_path),
        parsed=parsed,
        contract=contract,
        integrity_profile=integrity_profile,
    )

    if write_files:
        structure_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir.mkdir(parents=True, exist_ok=True)
        prompts_dir.mkdir(parents=True, exist_ok=True)
        parsed_summary_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
        parsed_markdown_path.write_text(format_class_properties_markdown(parsed), encoding="utf-8")
        integrity_profile_path.write_text(json.dumps(integrity_profile, indent=2, ensure_ascii=False), encoding="utf-8")
        write_generation_contract_bundle(contract, contract_path)

    return context


def build_contexts_for_ontologies(
    ontology_names: list[str],
    *,
    meta_task_config_path: str | Path | None = None,
    output_root: str | Path = DEFAULT_AGENTIC_OUTPUT_ROOT,
    write_files: bool = True,
) -> list[AgenticGenerationContext]:
    contexts: list[AgenticGenerationContext] = []
    for name in ontology_names:
        contexts.append(
            build_agentic_generation_context(
                ontology_name=name,
                meta_task_config_path=meta_task_config_path,
                output_root=output_root,
                write_files=write_files,
            )
        )
    return contexts
