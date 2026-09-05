from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_generation_contract_bundle,
    write_generation_contract_bundle,
)
from src.agents.scripts_and_prompts_generation.fixed_om2_runtime import (
    __file__ as fixed_om2_runtime_path,
)
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    __file__ as fixed_rdf_runtime_path,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    format_class_properties_markdown,
    parse_ontology_ttl,
)
from src.agents.scripts_and_prompts_generation.iteration_plan_compiler import (
    compile_iteration_plan,
)
from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    compile_materialization_operation_units,
)


DEFAULT_AGENTIC_OUTPUT_ROOT = Path("ai_generated_contents_agentic_candidate")
DEFAULT_ONTOLOGY_CONFIGS = {
    "ontosynthesis": Path("configs/meta_task/meta_task_config.json"),
    "ontomops": Path("configs/meta_task/meta_task_config.json"),
    "ontospecies": Path("configs/meta_task/meta_task_config.json"),
    "medical": Path("configs/meta_task/meta_task_config_medical_non_flat_v3.json"),
}


def runtime_publish_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Augment the T-Box publish contract with derived atomic creator metadata."""
    publish = dict(contract.get("ontology_publish_contract") or {})
    profile = contract.get("ordered_member_profile") or {}
    operation_units = contract.get("materialization_operation_units") or {}
    classes = {
        str(item.get("class_iri") or "").rsplit("#", 1)[-1].rsplit("/", 1)[-1]: str(
            item.get("class_iri") or ""
        )
        for item in publish.get("classes") or []
        if str(item.get("class_iri") or "").strip()
    }
    datatype_properties = {
        str(item.get("property_iri") or "").rsplit("#", 1)[-1].rsplit("/", 1)[-1]: str(
            item.get("property_iri") or ""
        )
        for item in publish.get("datatype_properties") or []
        if str(item.get("property_iri") or "").strip()
    }
    ordering_locals = [
        str(value).strip()
        for value in profile.get("single_valued_ordering_properties") or []
        if str(value).strip()
    ]
    if len(ordering_locals) == 1 and ordering_locals[0] in datatype_properties:
        ordering_iri = datatype_properties[ordering_locals[0]]
        publish["ordered_entity_creators"] = [
            {
                "class_iri": classes[class_local],
                "ordering_property_iri": ordering_iri,
                "source": "tbox_derived_ordered_member_profile",
            }
            for class_local in profile.get("ordered_member_classes") or []
            if str(class_local) in classes
        ]
    creator_owned_relationships: dict[str, list[dict[str, str]]] = {}
    for unit in operation_units.get("units") or []:
        creator = (unit or {}).get("creator_contract") or {}
        public_tool = str((unit or {}).get("public_tool") or "").strip()
        owner_class_iri = str((unit or {}).get("owner_class_iri") or "").strip()
        for edge in creator.get("required_edges") or []:
            predicate_iri = str((edge or {}).get("predicate_iri") or "").strip()
            if not predicate_iri or not public_tool:
                continue
            creator_owned_relationships.setdefault(predicate_iri, []).append(
                {
                    "public_tool": public_tool,
                    "owner_class_iri": owner_class_iri,
                    "role": str((edge or {}).get("role") or "").strip(),
                }
            )
    if creator_owned_relationships:
        publish["creator_owned_relationships"] = creator_owned_relationships
    return publish


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
    config_provenance_path: str
    parsed: dict[str, Any]
    contract: dict[str, Any]
    integrity_profile: dict[str, Any]
    pipeline_runtime_policies: dict[str, Any]
    iteration_blueprint: dict[str, Any]
    config_provenance: dict[str, Any]

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_meta_task_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Meta-task config must be a JSON object: {cfg_path}")
    return data


def _ontology_config(
    config: dict[str, Any], ontology_name: str
) -> tuple[str, dict[str, Any]]:
    """Return the selected ontology role and its non-T-Box configuration."""
    ontologies = config.get("ontologies") or {}
    main = ontologies.get("main")
    if isinstance(main, dict) and str(main.get("name") or "").strip() == ontology_name:
        return "main", main
    for extension in ontologies.get("extensions") or []:
        if (
            isinstance(extension, dict)
            and str(extension.get("name") or "").strip() == ontology_name
        ):
            return "extension", extension
    raise ValueError(f"Ontology {ontology_name!r} is not present in meta-task config")


def _load_iteration_blueprint(
    *,
    ontology_config: dict[str, Any],
    meta_task_config_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load scheduling intent separately from the T-Box semantic contract."""
    runtime = ontology_config.get("runtime_policies") or {}
    iteration_plan = runtime.get("iteration_plan") or {}
    configured = str(iteration_plan.get("iterations_blueprint_path") or "").strip()
    if not configured:
        return {}, {"source": "none", "path": "", "sha256": ""}

    candidate = Path(configured)
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.extend(
            [
                meta_task_config_path.parent / candidate,
                meta_task_config_path.resolve().parents[2] / candidate,
            ]
        )
    path = next((item.resolve() for item in candidates if item.is_file()), None)
    if path is None:
        raise FileNotFoundError(f"Missing configured iteration blueprint: {configured}")
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("iterations"), list):
        raise ValueError(f"Iteration blueprint must contain an iterations array: {path}")
    return data, {
        "source": "non_tbox_scheduling_intent",
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


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
    meta_config = load_meta_task_config(cfg_path)
    configured_role, ontology_config = _ontology_config(meta_config, ontology_name)
    if configured_role != spec.role:
        raise ValueError(
            f"Ontology role mismatch for {ontology_name}: {configured_role} != {spec.role}"
        )
    runtime_policies = ontology_config.get("runtime_policies") or {}
    if not isinstance(runtime_policies, dict):
        raise ValueError("runtime_policies must be a JSON object")
    iteration_blueprint, blueprint_provenance = _load_iteration_blueprint(
        ontology_config=ontology_config,
        meta_task_config_path=cfg_path,
    )

    ttl_path = Path(spec.ttl_file)
    if not ttl_path.is_file():
        raise FileNotFoundError(f"Missing ontology TTL: {ttl_path}")

    parsed = parse_ontology_ttl(str(ttl_path))
    contract = build_generation_contract_bundle(
        meta_task_config_path=cfg_path,
        ontology_name=ontology_name,
    )
    integrity_profile = dict(contract.get("ordered_member_profile") or {})
    compiled_iteration_plan = (
        compile_iteration_plan(
            blueprint=iteration_blueprint,
            parsed=parsed,
            contract=contract,
            ontology_name=ontology_name,
            blueprint_provenance=blueprint_provenance,
        )
        if iteration_blueprint
        else {}
    )
    contract["materialization_operation_units"] = (
        compile_materialization_operation_units(
            parsed=parsed,
            contract=contract,
            iteration_plan=compiled_iteration_plan,
        )
    )
    operation_errors = (
        contract["materialization_operation_units"].get("errors") or []
    )
    if operation_errors:
        raise ValueError(
            "Invalid materialization operation policy: "
            + "; ".join(str(item) for item in operation_errors)
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
    config_provenance_path = structure_dir / "config_provenance.json"

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
        config_provenance_path=str(config_provenance_path),
        parsed=parsed,
        contract=contract,
        integrity_profile=integrity_profile,
        pipeline_runtime_policies=dict(runtime_policies),
        iteration_blueprint=compiled_iteration_plan,
        config_provenance={
            "tbox": {
                "source": "active_tbox",
                "path": str(ttl_path.resolve()),
                "sha256": hashlib.sha256(ttl_path.read_bytes()).hexdigest(),
            },
            "meta_task": {
                "source": "non_tbox_runtime_overlay",
                "path": str(cfg_path.resolve()),
                "sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
            },
            "iteration_blueprint": blueprint_provenance,
            "boundary": {
                "semantic_authority": "tbox",
                "iteration_decomposition": "non_tbox_scheduling_intent",
                "runtime_wiring": "meta_task_runtime_policies",
            },
        },
    )

    if write_files:
        structure_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir.mkdir(parents=True, exist_ok=True)
        prompts_dir.mkdir(parents=True, exist_ok=True)
        parsed_summary_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
        parsed_markdown_path.write_text(format_class_properties_markdown(parsed), encoding="utf-8")
        integrity_profile_path.write_text(json.dumps(integrity_profile, indent=2, ensure_ascii=False), encoding="utf-8")
        write_generation_contract_bundle(contract, contract_path)
        config_provenance_path.write_text(
            json.dumps(context.config_provenance, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (scripts_dir / "_fixed_om2_runtime.py").write_text(
            Path(fixed_om2_runtime_path).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (scripts_dir / "_fixed_rdf_runtime.py").write_text(
            Path(fixed_rdf_runtime_path).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (scripts_dir / "_reuse_pair_judge.py").write_text(
            Path(__file__).with_name("reuse_pair_judge.py").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        (scripts_dir / "_relationship_contract.json").write_text(
            json.dumps(
                runtime_publish_contract(contract),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

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
