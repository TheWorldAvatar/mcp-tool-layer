from pathlib import Path

import importlib
import json
from rdflib import RDF, URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _import_generated_main_module,
)


ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "configs" / "meta_task" / "meta_task_config.json"


def test_generated_entities_use_absolute_tbox_range_iris(tmp_path: Path) -> None:
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        meta_task_config_path=META,
        output_root=tmp_path,
        write_files=True,
    )
    generate_deterministic_script_slice(context)

    entities_path = Path(context.scripts_dir) / "ontosynthesis_creation_entities.py"
    entities = entities_path.read_text(encoding="utf-8")
    represented_by_range = (
        "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron"
    )
    assert f"URIRef({represented_by_range!r})" in entities
    assert "'AmountOfSubstanceFraction'" in entities

    # The generation path must never fall back to the main ontology namespace
    # for an externally defined range with the same local name.
    assert "NS.MetalOrganicPolyhedron" not in entities

    main = _import_generated_main_module(Path(context.scripts_dir), "ontosynthesis")
    generated_entities = importlib.import_module(
        f"{main.__package__}.ontosynthesis_creation_entities"
    )
    generated_base = importlib.import_module(
        f"{main.__package__}.ontosynthesis_creation_base"
    )
    output = json.loads(
        generated_entities.create_ChemicalOutput(
            "Validator output",
            isRepresentedBy_label="Validator MOP",
        )
    )
    synthesis = json.loads(
        generated_entities.create_ChemicalSynthesis(
            "Validator synthesis",
            hasYield_label="1 %",
        )
    )
    output_ref = URIRef(output["iri"])
    synthesis_ref = URIRef(synthesis["iri"])
    mop = next(
        generated_base.GRAPH.objects(
            output_ref,
            URIRef("https://www.theworldavatar.com/kg/OntoSyn/isRepresentedBy"),
        )
    )
    yield_node = next(
        generated_base.GRAPH.objects(
            synthesis_ref,
            URIRef("https://www.theworldavatar.com/kg/OntoSyn/hasYield"),
        )
    )
    assert (mop, RDF.type, URIRef(represented_by_range)) in generated_base.GRAPH
    assert (
        yield_node,
        RDF.type,
        URIRef(
            "http://www.ontology-of-units-of-measure.org/resource/om-2/"
            "AmountOfSubstanceFraction"
        ),
    ) in generated_base.GRAPH
