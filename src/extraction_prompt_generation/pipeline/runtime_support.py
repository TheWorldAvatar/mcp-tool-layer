"""Write `iterations.json` and flatten RDF / OM-2 into the script package.

Prompt authoring does not own these files. See pipeline/README.md and
runtime_support/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.pipeline.plan import _iteration_plan


def write_fixed_om2_runtime(
    destination: str | Path,
    *,
    context: AgenticGenerationContext | None = None,
    surface: Mapping[str, Any] | None = None,
) -> Path:
    """Flatten class-aware OM-2 helpers next to generated scripts.

    Tables come from the v2 quantity surface (``om2.ttl`` plus
    ``om2_unit_aliases.json``) and are inlined so the generated package does
    not read those files at runtime.
    """
    from src.kg_building_mcp_generation_v2.overlay.om2_runtime_emit import (
        emit_om2_runtime,
    )
    from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
        compile_quantity_surface,
    )

    path = Path(destination)
    if path.exists() and path.is_dir() or path.suffix != ".py":
        path = path / "_fixed_om2_runtime.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    compiled = surface
    if compiled is None:
        units: Mapping[str, Any] = {}
        if context is not None:
            units = (context.contract.get("occurrence_surface_units") or {})
        compiled = compile_quantity_surface(units, context=context)
    path.write_text(emit_om2_runtime(compiled), encoding="utf-8", newline="\n")
    return path.resolve()


def _pipeline_main_ontology_name(context: AgenticGenerationContext) -> str:
    """Main ontology name from the compiled adapter, else the current package."""
    try:
        meta = json.loads(
            Path(context.ontology.meta_task_config_path).read_text(encoding="utf-8")
        )
    except Exception:
        return context.ontology.name
    main_name = str(
        ((meta.get("ontologies") or {}).get("main") or {}).get("name") or ""
    ).strip()
    return main_name or context.ontology.name


def generate_runtime_support_slice(
    context: AgenticGenerationContext,
    *,
    iterations: dict[str, Any] | None = None,
) -> list[str]:
    """Materialize run-local pipeline support artifacts from current inputs."""
    written: list[str] = []
    from src.extraction_prompt_generation.runtime_support.rdf import (
        write_fixed_rdf_runtime,
    )

    scripts_dir = Path(context.scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    written.append(str(write_fixed_rdf_runtime(scripts_dir / "_fixed_rdf_runtime.py")))
    written.append(
        str(
            write_fixed_om2_runtime(
                scripts_dir / "_fixed_om2_runtime.py",
                context=context,
            )
        )
    )
    from src.extraction_prompt_generation.config.namespace import (
        write_runtime_namespace_sidecar,
    )

    written.append(
        str(
            write_runtime_namespace_sidecar(
                scripts_dir,
                main_ontology_name=_pipeline_main_ontology_name(context),
            )
        )
    )
    materialized_iterations = (
        iterations if iterations is not None else _iteration_plan(context)
    )
    if materialized_iterations.get("iterations"):
        iterations_dir = (
            Path(context.output_root) / "iterations" / context.ontology.name
        )
        iterations_dir.mkdir(parents=True, exist_ok=True)
        iterations_path = iterations_dir / "iterations.json"
        iterations_path.write_text(
            json.dumps(materialized_iterations, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(str(iterations_path))

    top = context.contract.get("top_entity") or {}
    top_class_iri = str(top.get("class_iri") or "").strip()
    if not top_class_iri:
        # Legacy meta-task compatibility only. The two-input domain-config path
        # always supplies a GPT-5-selected top entity before support generation.
        iter1 = context.pipeline_runtime_policies.get("iter1_top_entity_kg") or {}
        configured_local = str(
            (iter1.get("prompt_rules") or {}).get("top_level_entity_name") or ""
        ).strip()
        configured_class = (context.parsed.get("classes") or {}).get(
            configured_local
        ) or {}
        top_class_iri = str(configured_class.get("iri") or "").strip()
    if top_class_iri:
        sparql_dir = Path(context.output_root) / "sparqls" / context.ontology.name
        sparql_dir.mkdir(parents=True, exist_ok=True)
        sparql_path = sparql_dir / "top_entity_parsing.sparql"
        sparql_path.write_text(
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n\n"
            "SELECT DISTINCT ?entity ?label WHERE {\n"
            f"  ?entity a <{top_class_iri}> .\n"
            "  OPTIONAL { ?entity rdfs:label ?label }\n"
            "}\n",
            encoding="utf-8",
        )
        written.append(str(sparql_path))
    from src.extraction_prompt_generation.compile.enrichment_sparql import (
        write_enrichment_target_sparql,
    )

    enrichment_sparql = write_enrichment_target_sparql(context)
    if enrichment_sparql:
        written.append(enrichment_sparql)
    return written
