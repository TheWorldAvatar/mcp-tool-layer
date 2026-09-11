"""Stage-mode contract probes for the most recently generated artifact.

Dispatches on filename suffix after each exact-edits publish. See README.md.
"""

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.stage.base import (
    validate_creation_base,
)
from src.extraction_prompt_generation.validate.report.stage.checks import (
    probe_existing_checks,
)
from src.extraction_prompt_generation.validate.report.stage.creators import (
    prepare_creator_surface,
    probe_owned_creators,
)
from src.extraction_prompt_generation.validate.report.stage.entities import (
    probe_entity_creator_surface,
    probe_om2_quantity,
    probe_range_materialization,
    validate_creation_entities_source,
)
from src.extraction_prompt_generation.validate.report.stage.load import (
    begin_retained_graph,
    check_public_tool_signatures,
    end_retained_graph,
    import_stage_python,
    resolve_stage_artifact,
)
from src.extraction_prompt_generation.validate.report.stage.main import (
    validate_stage_main,
)
from src.extraction_prompt_generation.validate.report.stage.prompt_md import (
    validate_stage_prompt,
)
from src.extraction_prompt_generation.validate.report.stage.relationships import (
    probe_relationship_writers,
    validate_creation_relationships_source,
)


def _stage_artifact_contract_report(
    context: AgenticGenerationContext,
    active_artifacts: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Validate obligations owned by the most recently generated artifact."""
    probe, early = resolve_stage_artifact(context, active_artifacts)
    if early is not None:
        return early
    assert probe is not None

    if probe.path.suffix == ".py":
        import_stage_python(probe)
        check_public_tool_signatures(probe)
        if probe.imported_module is not None and not probe.name.endswith(
            "_creation_base.py"
        ):
            if begin_retained_graph(probe):
                prepare_creator_surface(probe)
                if probe.name.endswith("_creation_entities.py"):
                    probe_entity_creator_surface(probe)
                probe_owned_creators(probe)
                if probe.name.endswith("_creation_entities.py"):
                    probe_om2_quantity(probe)
                probe_relationship_writers(probe)
                if probe.name.endswith("_creation_entities.py"):
                    probe_range_materialization(probe)
                if probe.name.endswith("_creation_checks.py"):
                    probe_existing_checks(probe)
                end_retained_graph(probe)

    if probe.name.endswith("_creation_base.py"):
        validate_creation_base(probe)
    elif probe.name.endswith("_creation_entities.py"):
        validate_creation_entities_source(probe)
    elif probe.name.endswith("_creation_relationships.py"):
        validate_creation_relationships_source(probe)
    elif probe.name == "main.py":
        validate_stage_main(probe)
    elif probe.path.suffix == ".md":
        validate_stage_prompt(probe)
    return probe.failures, probe.warnings, [probe.relative]


__all__ = [
    "_stage_artifact_contract_report",
]
