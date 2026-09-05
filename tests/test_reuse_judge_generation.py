import json
from pathlib import Path

import pytest
from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _base_script,
    _checks_script,
    _iteration_kg_prompt,
    _relationships_script,
)
from src.agents.scripts_and_prompts_generation.domain_generation_resume import (
    load_domain_generation_checkpoint,
)


ROOT = Path(__file__).resolve().parents[1]


def _checkpoint_context():
    return load_domain_generation_checkpoint(
        output_root=ROOT / "ai_generated_contents_candidate",
        ontology_name="ontosynthesis",
    )


def test_generated_central_checks_require_pairwise_llm_judgement() -> None:
    source = _checks_script(_checkpoint_context())
    compile(source, "generated_creation_checks.py", "exec")

    assert "from ._reuse_pair_judge import judge_reuse_pairs" in source
    assert "proposed_entity_json: str = \"\"" in source
    assert '"PROPOSED_ENTITY_EVIDENCE_REQUIRED"' in source
    assert "register_central_reuse_authorization" in source
    assert '"reuse_authorization_token": token' in source
    assert '"instances": details' in source


def test_checkpoint_generation_preserves_non_reusable_class_policy() -> None:
    context = _checkpoint_context()
    checks = _checks_script(context)
    base = _base_script(context)
    chemical_synthesis_block = checks.split(
        "def check_existing_ChemicalSynthesis", 1
    )[1].split("\ndef ", 1)[0]
    synthesis_step_block = checks.split(
        "def check_existing_SynthesisStep", 1
    )[1].split("\ndef ", 1)[0]

    assert "lookup_scope='scoped'" in chemical_synthesis_block
    assert "reuse_authorized=False" in chemical_synthesis_block
    assert "lookup_scope='scoped'" in synthesis_step_block
    assert "reuse_authorized=False" in synthesis_step_block
    reusable_literal = base.split("REUSABLE_CLASS_IRIS = ", 1)[1].split("\n", 1)[0]
    assert "ChemicalSynthesis" not in reusable_literal
    assert "SynthesisStep" not in reusable_literal
    assert "ChemicalInput" not in reusable_literal


def test_generated_relationships_forward_scope_bound_reuse_token() -> None:
    source = _relationships_script(_checkpoint_context())
    compile(source, "generated_creation_relationships.py", "exec")

    assert "reuse_authorization_token:" in source
    assert "from pydantic import Field" not in source
    assert "Annotated[" not in source
    assert (
        "subject_iri,\n            object_iri,\n            reuse_authorization_token,"
        in source
    )


def test_kg_prompt_requires_judge_evidence_and_authorization_token() -> None:
    context = _checkpoint_context()
    iteration = (context.iteration_blueprint.get("iterations") or [])[0]
    prompt = _iteration_kg_prompt(context, iteration)

    assert "complete proposed entity hint serialized as JSON" in prompt
    assert "LLM-authorized returned IRI" in prompt
    assert "reuse_authorization_token" in prompt
    assert "without a valid scope-bound token" in prompt


def test_generated_check_hides_denied_candidates_and_grants_approved_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_generated_contents_candidate.scripts.ontosynthesis import (
        _fixed_rdf_runtime as runtime,
    )
    from ai_generated_contents_candidate.scripts.ontosynthesis import (
        ontosynthesis_creation_checks as checks,
    )
    from ai_generated_contents_candidate.scripts.ontosynthesis import (
        ontosynthesis_creation_entities as entities,
    )
    from ai_generated_contents_candidate.scripts.ontosynthesis import (
        ontosynthesis_creation_relationships as relationships,
    )

    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    runtime.reset_retained_graph()
    runtime.init_memory("current-doi", "current-top")
    supplier_class = "https://www.theworldavatar.com/kg/OntoSyn/Supplier"
    candidate = URIRef("https://example.test/supplier/canonical")
    central = Graph()
    central.add((candidate, RDF.type, URIRef(supplier_class)))
    central.add((candidate, RDFS.label, Literal("Canonical Supplier")))
    runtime.publish_reusable_entities_to_central_memory(
        ontology_name="ontosynthesis",
        source_graph=central,
        reusable_class_iris=[supplier_class],
        doi="prior-doi",
        top_level_entity_name="prior-top",
    )

    denied = {
        "pair_id": "p0001",
        "reuse_authorized": False,
        "same_real_world_entity": False,
        "context_independent_identity": True,
        "match_basis_satisfied": False,
        "confidence": 0.99,
        "reason": "Identity does not match.",
        "evidence_used": ["different organization identity"],
    }
    judge_owner = (
        checks
        if hasattr(checks, "judge_reuse_pairs")
        else checks.reuse_judge
    )
    monkeypatch.setattr(
        judge_owner,
        "judge_reuse_pairs",
        lambda requests: [{**denied, "pair_id": requests[0]["pair_id"]}],
    )
    denied_result = json.loads(
        checks.check_existing_Supplier(
            json.dumps({"label": "Different Supplier"})
        )
    )
    assert denied_result["instances"] == []
    assert denied_result["reuse_authorized"] is False

    approved = {
        **denied,
        "reuse_authorized": True,
        "same_real_world_entity": True,
        "match_basis_satisfied": True,
        "reason": "Canonical organization identity matches.",
        "evidence_used": ["canonical organization name"],
    }
    monkeypatch.setattr(
        judge_owner,
        "judge_reuse_pairs",
        lambda requests: [{**approved, "pair_id": requests[0]["pair_id"]}],
    )
    approved_result = json.loads(
        checks.check_existing_Supplier(
            json.dumps({"label": "Canonical Supplier"})
        )
    )
    approved_candidate = approved_result["instances"][0]
    token = approved_candidate["reuse_authorization_token"]

    chemical_input = json.loads(entities.create_ChemicalInput("input"))["iri"]
    rejected_link = json.loads(
        relationships.add_isSuppliedBy(chemical_input, str(candidate))
    )
    assert rejected_link["status"] in {"error", "rejected"}
    assert (
        rejected_link.get("code") == "CENTRAL_REUSE_NOT_AUTHORIZED"
        or "CENTRAL_REUSE_NOT_AUTHORIZED" in rejected_link.get("message", "")
    )

    accepted_link = json.loads(
        relationships.add_isSuppliedBy(chemical_input, str(candidate), token)
    )
    assert accepted_link["status"] == "ok"
