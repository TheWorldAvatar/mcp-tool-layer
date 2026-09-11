"""Facade for generation-contract compile and artifact validation.

Implementations live in `contract_bundle.py`, `contract_publish.py`, and
`contract_observations.py`. This module must stay free of paper-specific
facts. See compile/README.md.
"""

from __future__ import annotations

from src.extraction_prompt_generation.compile.contract_bundle import (
    build_generation_contract_bundle,
    build_relationship_tool_contracts_from_tbox,
    validate_generated_artifacts,
    write_generation_contract_bundle,
)
from src.extraction_prompt_generation.compile.contract_observations import (
    build_validation_observation,
)
from src.extraction_prompt_generation.compile.contract_publish import (
    build_ontology_publish_contract,
    build_ontology_publish_contract_from_tbox,
    load_meta_task_config,
)

__all__ = [
    "build_generation_contract_bundle",
    "build_ontology_publish_contract",
    "build_ontology_publish_contract_from_tbox",
    "build_relationship_tool_contracts_from_tbox",
    "build_validation_observation",
    "load_meta_task_config",
    "validate_generated_artifacts",
    "write_generation_contract_bundle",
]
