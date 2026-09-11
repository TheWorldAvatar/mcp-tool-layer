"""LLM semantic review of generated extraction prompts.

One GPT-5 JSON call per `.md` against its role contract and T-Box slice.
See generate/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
    _prompt_artifact_generation_contract,
    _prompt_role_contract,
)
from src.extraction_prompt_generation.llm.invoke import (
    invoke_json,
)


def review_generated_artifact_semantics_with_llm(
    *,
    context: AgenticGenerationContext,
    artifact_path: str | Path,
    model_name: str,
) -> dict[str, Any]:
    """Review one generated extraction prompt against its role and T-Box."""
    path = Path(artifact_path)
    if path.suffix != ".md":
        raise ValueError("Artifact semantic reviewer requires a Markdown prompt")
    generation_contract = _prompt_artifact_generation_contract(context, path)
    evidence = {
        "artifact": path.name,
        "artifact_source": path.read_text(encoding="utf-8", errors="replace"),
        "artifact_role_contract": _prompt_role_contract(path, generation_contract),
        "owned_entity_tool_contracts": {},
        "fixed_runtime_api": {},
        "tbox_generation_contract": generation_contract,
        "artifact_lifecycle_state": "runtime_template_not_executed",
        "runtime_behavior_probes": {},
        "read_only_dependency_sources": {},
    }
    prompt = (
        "You are the independent soft-semantic reviewer for one generated ontology "
        "pipeline artifact. Compare the implementation with the supplied artifact role "
        "and the T-Box-derived contract. Do not judge by keyword presence, identifier "
        "names, formatting, or preferred code style. A pass requires no critical "
        "semantic or architectural errors. "
        "For a Markdown runtime prompt template, evaluate its instructions and placeholders, "
        "not whether it already contains runtime extraction or A-Box instances. A template must "
        "not pre-populate fixture entities, quantities, source quotations, ordered members, or "
        "links; missing runtime instances before source injection is correct and never a repair "
        "reason. It must implement only the supplied iteration_spec responsibility and use only "
        "the supplied tbox_scope; repeating the ontology-wide extraction task is a critical scope "
        "error. When generation_contract.agent_tool_contract is present, require every operative "
        "tool instruction to use its exact tool and parameter names; generic creator/relation/"
        "lifecycle descriptions are not executable. When semantic_scalar_output_contract is "
        "present, require every scalar's complete source-grounded value as a standalone "
        "key-value line under the owning occurrence; prose must not be the sole carrier "
        "for semantic-text.v1. "
        "For an extraction prompt whose supplied artifact role requires the "
        "ref-entity-relations.v1 interchange contract, reason from the complete instructions: "
        "the runtime output must be one authoritative object whose only top-level collections "
        "are entities and relations; entity identity, class, canonical label, datatype payload, "
        "and relation endpoints must remain separated as specified by the role contract. A "
        "second records, evidence-accounting, label-transport, wrapper, or replacement-anchor "
        "output is a critical interface error. Merely mentioning one of those concepts while "
        "explicitly prohibiting it is compliant and must not be treated as evidence that the "
        "prompt authorizes it. Likewise, determine workflow ownership, missing-reference "
        "behavior, and reuse restrictions from the operative instruction as a whole rather than "
        "from isolated words or phrases. Every domain-specific trigger, example, exclusion, "
        "disambiguation rule, and scientific interpretation must have direct support in "
        "tbox_scope; plausible but unsupported domain knowledge is a critical provenance error. "
        + (
            "For an extension extraction prompt, entity_label and entity_uri identify the "
            "inherited upstream scope, while tbox_scope.extension_focus identifies the class "
            "to extract. Treating the inherited entity itself as an instance of the extension "
            "focus, limiting output to that one entity, or failing to allow multiple relevant "
            "extension-focus instances is a critical scope error. Paper content and the T-Box "
            "arrive through wrapper channels and must not appear as runtime placeholders. "
            if context.ontology.role == "extension"
            and path.name.startswith("EXTRACTION_")
            else ""
        )
        + "Return JSON only:\n"
        '{"decision":"pass|repair","summary":"...",'
        '"critical_errors":[{"finding":"...","behavioral_evidence":["..."],'
        '"contract_evidence":["..."],"repair_target":"exact filename"}],'
        '"noncritical_observations":["..."],"confidence":0.0}\n\n'
        + json.dumps(evidence, ensure_ascii=False)
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=None,
        max_attempts=3,
        provider_max_retries=0,
    )
    review = dict(response.data)
    decision = review.get("decision")
    errors = review.get("critical_errors")
    if decision not in {"pass", "repair"}:
        raise ValueError("Artifact semantic reviewer returned an invalid decision")
    if not isinstance(errors, list):
        raise ValueError("Artifact semantic reviewer requires critical_errors as a list")
    if decision == "pass" and errors:
        raise ValueError("Artifact semantic pass cannot contain critical errors")
    if decision == "repair" and not errors:
        raise ValueError("Artifact semantic repair requires critical errors")
    review["evidence"] = evidence
    return review


def review_generated_prompt_semantics_with_llm(
    *,
    context: AgenticGenerationContext,
    artifact_path: str | Path,
    model_name: str,
) -> dict[str, Any]:
    """Review one runtime prompt semantically without phrase or keyword gates."""
    path = Path(artifact_path)
    if path.suffix != ".md":
        raise ValueError("Prompt semantic reviewer requires a Markdown artifact")
    return review_generated_artifact_semantics_with_llm(
        context=context,
        artifact_path=path,
        model_name=model_name,
    )
