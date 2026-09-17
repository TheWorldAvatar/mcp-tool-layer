from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

from src.extraction_runtime.agent.env import apply_kg_protocol_environment
from src.kg_building.pipeline.main_kg.hints import load_entity_hints
from src.kg_building.ontologx.cli import build_parser as ox_cli_parser

OX = Path(__file__).resolve().parents[1] / "src" / "kg_building" / "ontologx"
if str(OX) not in sys.path:
    sys.path.insert(0, str(OX))

from graph_types import GraphDocument, Node
from run_loop import compose_ox_human, reuse_extra_human
from strict_noprompt import (
    build_generic_noprompt_medical_prompt,
    build_generic_noprompt_system_prompt,
    build_strict_noprompt_medical_prompt,
    build_strict_noprompt_system_prompt,
    build_with_prompt_hops_system_prompt,
    build_with_prompt_qty_system_prompt,
    build_with_prompt_system_prompt,
)


class _Entity:
    key = "ChemicalSynthesis-1"
    label = "UMC-1"
    uri = "http://example.org/umc-1"
    identity_dossier = {"uri": "http://example.org/umc-1"}


def test_load_entity_hints_skips_iter1(tmp_path: Path) -> None:
    mcp = tmp_path / "mcp_run"
    mcp.mkdir()
    (mcp / "iter1_hints_UMC-1.txt").write_text("ITER1 SHOULD DROP", encoding="utf-8")
    (mcp / "iter2_hints_UMC-1.txt").write_text("SEMANTIC_HINTS_V1\niter2", encoding="utf-8")
    (mcp / "iter3_hints_UMC-1.txt").write_text("SEMANTIC_HINTS_V1\niter3", encoding="utf-8")
    (mcp / "iter4_hints_UMC-1.txt").write_text("SEMANTIC_HINTS_V1\niter4", encoding="utf-8")
    text = load_entity_hints(tmp_path, "UMC-1")
    assert "iter2" in text and "iter3" in text and "iter4" in text
    assert "ITER1 SHOULD DROP" not in text
    assert text.startswith("SEMANTIC_HINTS_V1")
    assert "Whole-graph ledger:" in text
    assert "=== ITER2 SEMANTIC_HINTS ===" in text
    assert "=== ITER3 SEMANTIC_HINTS ===" in text
    assert "=== ITER4 SEMANTIC_HINTS ===" in text


def test_kg_protocol_env_pins_seed_and_surfaces() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TWA_LLM_SEED", None)
        os.environ.pop("TWA_MCP_TOOL_DESCRIPTIONS_ENABLED", None)
        os.environ.pop("TWA_SEMANTIC_OPERATION_SURFACE", None)
        apply_kg_protocol_environment()
        assert os.environ["TWA_LLM_SEED"] == "42"
        os.environ["TWA_LLM_SEED"] = "99"
        apply_kg_protocol_environment()
        assert os.environ["TWA_LLM_SEED"] == "42"
        assert os.environ["TWA_MCP_TOOL_DESCRIPTIONS_ENABLED"] == "0"
        assert os.environ["TWA_SEMANTIC_OPERATION_SURFACE"] == "1"


def test_resolve_default_tbox_prefers_frozen_handbook() -> None:
    from src.kg_building.generic_noprompt_graph_rules import (
        load_ontosynthesis_tbox_text,
        resolve_default_tbox as pipeline_resolve,
    )
    from prompt_builder import resolve_default_tbox

    path = resolve_default_tbox()
    assert path.is_file()
    assert path.name == "ontosynthesis_parsed.md"
    assert path.parts[-2:] == ("ontologies", "ontosynthesis_parsed.md")
    assert pipeline_resolve() == path
    assert load_ontosynthesis_tbox_text() == path.read_text(encoding="utf-8").strip()
    assert "Class:" in load_ontosynthesis_tbox_text() or "Add" in load_ontosynthesis_tbox_text()


def test_tbox_loader_imports_without_ontologx_on_path() -> None:
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env.pop("PYTHONHOME", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.kg_building.generic_noprompt_graph_rules import load_ontosynthesis_tbox_text; "
            "text = load_ontosynthesis_tbox_text(); "
            "assert 'Class:' in text or 'Add' in text",
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_generic_noprompt_system_matches_profile() -> None:
    text = build_generic_noprompt_system_prompt()
    strict = build_strict_noprompt_system_prompt()
    assert text.startswith(strict.rstrip())
    assert "Occurrence protocol" in text
    assert "Ownership and attachment" in text
    assert "Generic OntoLogX graph rules" in text
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" in text
    assert "Authoritative OntoSynthesis T-Box" in text
    assert "There is no paper body" in text
    assert "official_onepass_ox.contract" not in text
    assert "Task: KG materialization, not paper extraction" not in text
    assert "Whole-graph construction from all iteration hints" not in text


def test_generic_strict_system_matches_profile() -> None:
    text = build_strict_noprompt_system_prompt()
    assert "Generic OntoLogX graph rules" in text
    assert "There is no paper body" in text
    assert "Occurrence protocol" in text
    assert "Ownership and attachment" in text
    assert "Authoritative OntoSynthesis T-Box" not in text
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" not in text
    assert "# With-prompt OntoSyn KG-building guidance" not in text
    assert "REQUIRED: every Add has exactly one hasAddedChemicalInput" not in text
    assert "## Crystallize" not in text
    assert "hasCrystallizationTargetTemperature" not in text
    assert "EXTENSION_CONTRACT" not in text
    assert "Do split those hops onto later nodes" in text
    assert "Do not split those details onto a later node" not in text


def test_medical_occurrence_keeps_literal_copy() -> None:
    text = build_strict_noprompt_medical_prompt()
    assert "Do not split those details onto a later node" in text
    assert "Do split those hops onto later nodes" not in text


def test_ox_human_includes_reuse_inventories() -> None:
    extra = reuse_extra_human(_Entity(), None, None)
    human = compose_ox_human("SEMANTIC_HINTS_V1\nledger", {"doi": "10.x"}, extra)
    assert "SAME_PAPER_REUSABLE_ENTITIES" in human
    assert "CROSS_DOCUMENT_REUSABLE_ENTITIES" in human
    assert "(none yet)" in human
    assert "Target ChemicalSynthesis" in human
    assert "KG_BINDING" not in human
    paper_graph = GraphDocument(
        nodes=[Node(id="doc1", type="bibo:Document", properties={"rdfs:label": "10.x"})],
        relationships=[],
        source=None,
    )
    later = reuse_extra_human(_Entity(), paper_graph, None)
    assert "doc1" in later


def test_step_config_forwards_experiment_protocol() -> None:
    from src.extraction_runtime.runner import _step_config

    class _Domain:
        ontology_name = "ontosynthesis"
        execution_profile = "complex_main"
        vision_required = False

    step = _step_config(
        {
            "experiment_protocol": "generic-noprompt",
            "pipeline_kg": "no-contract",
            "kg_model": "openai/gpt-4o-2024-11-20",
            "steps": ["main_kg_building"],
        },
        data_dir="runtime",
        domain=_Domain(),
        step_name="main_kg_building",
        test_mcp_config_name=None,
    )
    assert step["experiment_protocol"] == "generic-noprompt"
    assert step["kg_model"] == "openai/gpt-4o-2024-11-20"
    assert "steps" not in step


def test_pipeline_generic_strict_user_excludes_graph_rules() -> None:
    from src.kg_building.pipeline.binding import bind_kg_runtime_context

    kwargs = {
        "hints": "SEMANTIC_HINTS_V1\nstep-1",
        "doi": "10.1021/acsami.7b18836",
        "entity_label": "UMC-1",
        "entity_uri": "http://example.org/umc-1",
    }
    unset = bind_kg_runtime_context(**kwargs)
    strict = bind_kg_runtime_context(**kwargs, protocol="generic-strict")
    assert unset == strict
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" not in strict
    assert "# Generic-noprompt graph rules" not in strict
    assert "Use the attached MCP for the bound graph-building task." in strict
    assert (
        "Before export_memory, call every public create_* that matches a ledger heading."
        in strict
    )
    assert "Do split those hops onto later nodes" not in strict


def test_pipeline_generic_noprompt_user_shares_graph_rules_and_tbox() -> None:
    from src.kg_building.generic_noprompt_graph_rules import (
        GENERIC_NOPROMPT_GRAPH_RULES,
        load_ontosynthesis_tbox_text,
    )
    from src.kg_building.pipeline.binding import bind_kg_runtime_context

    kwargs = {
        "hints": "SEMANTIC_HINTS_V1\nstep-1",
        "doi": "10.1021/acsami.7b18836",
        "entity_label": "UMC-1",
        "entity_uri": "http://example.org/umc-1",
    }
    strict = bind_kg_runtime_context(**kwargs, protocol="generic-strict")
    noprompt = bind_kg_runtime_context(**kwargs, protocol="generic-noprompt")
    ox_text = build_generic_noprompt_system_prompt()
    tbox = load_ontosynthesis_tbox_text()
    assert noprompt.startswith(strict.rstrip())
    assert GENERIC_NOPROMPT_GRAPH_RULES in noprompt
    assert GENERIC_NOPROMPT_GRAPH_RULES in ox_text
    assert tbox in noprompt
    assert tbox in ox_text
    assert "Authoritative OntoSynthesis T-Box" in noprompt
    assert "A wash of retained solid is Filter" in noprompt
    assert GENERIC_NOPROMPT_GRAPH_RULES not in build_strict_noprompt_system_prompt()
    assert tbox not in strict
    assert "Do split those hops onto later nodes" not in noprompt
    assert "Do split those hops onto later nodes" in ox_text
    assert "# With-prompt OntoSyn KG-building guidance" not in noprompt
    assert "# With-prompt OntoSyn KG-building guidance" not in ox_text


def test_pipeline_with_prompt_appends_frozen_guidance() -> None:
    from src.kg_building.generic_noprompt_graph_rules import (
        GENERIC_NOPROMPT_GRAPH_RULES,
        load_ontosynthesis_tbox_text,
    )
    from src.kg_building.pipeline.binding import bind_kg_runtime_context
    from src.kg_building.with_prompt_guidance import load_ontosyn_with_prompt_guidance

    kwargs = {
        "hints": "SEMANTIC_HINTS_V1\nstep-1",
        "doi": "10.1021/acsami.7b18836",
        "entity_label": "UMC-1",
        "entity_uri": "http://example.org/umc-1",
    }
    strict = bind_kg_runtime_context(**kwargs, protocol="generic-strict")
    prompted = bind_kg_runtime_context(**kwargs, protocol="with-prompt")
    ox_text = build_with_prompt_system_prompt()
    guidance = load_ontosyn_with_prompt_guidance()
    tbox = load_ontosynthesis_tbox_text()
    assert prompted.startswith(strict.rstrip())
    assert guidance in prompted
    assert guidance in ox_text
    assert tbox in prompted
    assert tbox in ox_text
    assert "# With-prompt OntoSyn KG-building guidance" in prompted
    assert "Authoritative OntoSynthesis T-Box" in prompted
    assert "Authoritative OntoSynthesis T-Box" in ox_text
    assert "# Generic-noprompt graph rules" not in prompted
    assert GENERIC_NOPROMPT_GRAPH_RULES not in prompted
    assert GENERIC_NOPROMPT_GRAPH_RULES not in ox_text
    assert "hasAddedChemicalInput" in guidance
    assert "There is no `ontosyn:Crystallize`" in guidance
    assert guidance not in strict
    assert tbox not in strict
    assert "Do split those hops onto later nodes" not in prompted
    assert "Do split those hops onto later nodes" in ox_text
    medical = bind_kg_runtime_context(
        hints="SEMANTIC_HINTS_V1\nledger",
        doi="OPR1a",
        entity_label="Case-1",
        protocol="with-prompt",
        ontology="medical",
    )
    from src.kg_building.with_prompt_guidance import load_medical_with_prompt_guidance
    from src.kg_building.generic_noprompt_graph_rules import load_medical_tbox_text

    med_guidance = load_medical_with_prompt_guidance()
    med_tbox = load_medical_tbox_text()
    assert guidance not in medical
    assert tbox not in medical
    assert "Authoritative OntoSynthesis T-Box" not in medical
    assert med_guidance in medical
    assert med_tbox in medical
    assert "Authoritative medical T-Box" in medical
    assert "# With-prompt OntoMed KG-building guidance" in medical
    assert "Official ONEPASS semantic guidance" not in prompted
    assert "Official ONEPASS semantic guidance" not in ox_text


def test_full_prompt_is_official_onepass_plus_tbox() -> None:
    from prompt_builder import build_full_prompt_system_prompt
    from src.kg_building.full_prompt import load_official_onepass_contract
    from src.kg_building.generic_noprompt_graph_rules import load_ontosynthesis_tbox_text
    from src.kg_building.pipeline.binding import bind_kg_runtime_context
    from src.kg_building.with_prompt_guidance import load_ontosyn_with_prompt_guidance

    kwargs = {
        "hints": "SEMANTIC_HINTS_V1\nstep-1",
        "doi": "10.1021/acsami.7b18836",
        "entity_label": "UMC-1",
        "entity_uri": "http://example.org/umc-1",
    }
    strict = bind_kg_runtime_context(**kwargs, protocol="generic-strict")
    pipeline = bind_kg_runtime_context(**kwargs, protocol="full-prompt")
    ox_text = build_full_prompt_system_prompt()
    contract = load_official_onepass_contract()
    tbox = load_ontosynthesis_tbox_text()
    guidance = load_ontosyn_with_prompt_guidance()
    assert pipeline.startswith(strict.rstrip())
    assert contract in pipeline
    assert contract in ox_text
    assert tbox in pipeline
    assert tbox in ox_text
    assert "Official ONEPASS semantic guidance" in pipeline
    assert "Official ONEPASS semantic guidance" in ox_text
    assert "Authoritative OntoSynthesis T-Box" in pipeline
    assert "Authoritative OntoSynthesis T-Box" in ox_text
    assert "Task: KG materialization, not paper extraction" in ox_text
    assert "Use the attached MCP for the bound graph-building task." in pipeline
    assert "Do not emit OntoLogX JSON" in pipeline
    assert "Occurrence protocol" not in ox_text
    assert "# With-prompt OntoSyn KG-building guidance" not in pipeline
    assert "# With-prompt OntoSyn KG-building guidance" not in ox_text
    assert guidance not in pipeline
    assert guidance not in ox_text
    assert "Whole-graph construction from all iteration hints" not in ox_text
    assert contract not in strict
    assert contract not in bind_kg_runtime_context(**kwargs, protocol="with-prompt")
    assert contract not in build_strict_noprompt_system_prompt()
    assert contract not in build_generic_noprompt_system_prompt()
    assert contract not in build_with_prompt_system_prompt()
    medical = bind_kg_runtime_context(
        hints="SEMANTIC_HINTS_V1\nledger",
        doi="OPR1a",
        entity_label="Case-1",
        protocol="full-prompt",
        ontology="medical",
    )
    assert contract not in medical


def test_with_prompt_qty_is_ox_overlay_on_frozen_guidance() -> None:
    from src.kg_building.experiment_protocol import (
        PROTOCOL_NAMES,
        apply_to_ox_args,
        apply_to_pipeline_config,
    )
    from src.kg_building.pipeline.binding import bind_kg_runtime_context
    from src.kg_building.with_prompt_guidance import (
        load_ontosyn_with_prompt_guidance,
        load_ontosyn_with_prompt_quantity_dualcode,
    )
    from quantity_ownership import QUANTITY_FACETS, apply_quantity_nested_ownership
    from strict_noprompt import load_pipeline_surface

    frozen = load_ontosyn_with_prompt_guidance()
    dualcode = load_ontosyn_with_prompt_quantity_dualcode()
    locked = build_with_prompt_system_prompt()
    qty = build_with_prompt_qty_system_prompt()
    heat_facets = (
        "Facets on this occurrence: hasOrder, hasParameter, hasStepDuration, "
        "hasTargetTemperature, hasTemperatureRate, hasVacuum, isSealed, "
        "isStirredHeatChill."
    )
    hops_temp = (
        "Never put hasTargetTemperature or the measure class name on this "
        "HeatChill's properties."
    )
    assert frozen in locked
    assert frozen in qty
    assert dualcode not in locked
    assert dualcode in qty
    assert heat_facets not in locked
    assert heat_facets not in build_strict_noprompt_system_prompt()
    assert heat_facets not in qty
    assert hops_temp in locked
    assert hops_temp in build_strict_noprompt_system_prompt()
    assert hops_temp in qty
    assert "related om-2:Temperature node owned by this HeatChill" in qty
    assert "related om-2:Duration node owned by this Add" in qty
    assert "with-prompt-qty" not in PROTOCOL_NAMES

    original = load_pipeline_surface()
    heat = next(item for item in original["owner_occurrences"] if item["owner_class"] == "HeatChill")
    before = list(heat["self_facets"])
    rewritten = apply_quantity_nested_ownership(original)
    heat_after = next(
        item for item in rewritten["owner_occurrences"] if item["owner_class"] == "HeatChill"
    )
    heat_orig = next(
        item for item in original["owner_occurrences"] if item["owner_class"] == "HeatChill"
    )
    assert heat_orig["self_facets"] == before
    for name in ("hasTargetTemperature", "hasStepDuration", "hasTemperatureRate"):
        assert name not in heat_after["self_facets"]
        hops = [item for item in heat_after["nested_ownership"] if item["argument"] == name]
        assert hops and hops[0]["role"] == "nested_quantity"
        assert hops[0]["range_class"] == QUANTITY_FACETS[name]

    kwargs = {
        "hints": "SEMANTIC_HINTS_V1\nstep-1",
        "doi": "10.1021/acsami.7b18836",
        "entity_label": "UMC-1",
        "entity_uri": "http://example.org/umc-1",
    }
    pipeline_qty = bind_kg_runtime_context(**kwargs, protocol="with-prompt-qty")
    assert frozen in pipeline_qty
    assert dualcode in pipeline_qty
    try:
        apply_to_pipeline_config({}, "with-prompt-qty")
        raised = False
    except ValueError:
        raised = True
    assert raised
    ox_args = ox_cli_parser().parse_args(
        ["--protocol", "with-prompt-qty", "--out-dir", "tmp", "--hash", "0c57bac8"]
    )
    spec = apply_to_ox_args(ox_args, ox_args.protocol)
    assert ox_args.protocol == "with-prompt-qty"
    assert spec.ox_prompt_profile == "with-prompt-qty"


def test_with_prompt_hops_expands_chemical_and_quantity_hops() -> None:
    from src.kg_building.experiment_protocol import PROTOCOL_NAMES, apply_to_pipeline_config
    from src.kg_building.with_prompt_guidance import (
        load_ontosyn_with_prompt_guidance,
        load_ontosyn_with_prompt_hops_dualcode,
        load_ontosyn_with_prompt_quantity_dualcode,
    )

    frozen = load_ontosyn_with_prompt_guidance()
    hops_text = load_ontosyn_with_prompt_hops_dualcode()
    qty_text = load_ontosyn_with_prompt_quantity_dualcode()
    locked = build_with_prompt_system_prompt()
    hops = build_with_prompt_hops_system_prompt()
    strict = build_strict_noprompt_system_prompt()
    noprompt = build_generic_noprompt_system_prompt()
    assert frozen in locked
    assert frozen in hops
    assert hops_text in hops
    assert hops_text not in locked
    assert hops_text not in strict
    assert hops_text not in noprompt
    assert qty_text not in hops
    assert "Do not split those details onto a later node" not in locked
    assert "Do not split those details onto a later node" not in strict
    assert "Do not split those details onto a later node" not in noprompt
    assert "Do not split those details onto a later node" not in hops
    assert "Do split those hops onto later nodes" in locked
    assert "Do split those hops onto later nodes" in strict
    assert "Do split those hops onto later nodes" in noprompt
    assert "Do split those hops onto later nodes" in hops
    assert "Never put hasAddedChemicalInput on this Add's properties." in locked
    assert "Never put hasAddedChemicalInput on this Add's properties." in hops
    assert "Ledger keys hasAddedChemicalInput" in hops
    assert "Never put hasWashingSolvent on this Filter's properties." in hops
    assert "Never put hasTargetTemperature or the measure class name on this HeatChill's properties." in hops
    assert "from the bound ChemicalSynthesis, not from this ChemicalOutput" in hops
    assert "properties on that node: hasAlternativeNames, hasAmount" not in hops
    assert "Add --hasVessel--> that node" in strict
    assert "ontosyn:VesselType node and relationship Add --hasVessel-->" not in strict
    assert "ontosyn:Supplier node and relationship Evaporate --removesSpecies-->" not in strict
    assert "with-prompt-hops" not in PROTOCOL_NAMES
    try:
        apply_to_pipeline_config({}, "with-prompt-hops")
        raised = False
    except ValueError:
        raised = True
    assert raised
    ox_args = ox_cli_parser().parse_args(
        ["--protocol", "with-prompt-hops", "--out-dir", "tmp", "--hash", "0c57bac8"]
    )
    assert ox_args.protocol == "with-prompt-hops"


def test_shacl_and_surface_drop_crystallize() -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    shacl = (
        repo / "src" / "kg_building" / "ontologx" / "resources" / "ontosynthesis_shacl.ttl"
    ).read_text(encoding="utf-8")
    surface = (
        repo
        / "src"
        / "kg_building"
        / "ontologx"
        / "resources"
        / "pipeline_occurrence_surface_ox.json"
    ).read_text(encoding="utf-8")
    assert "ontosyn:CrystallizeShape" not in shacl
    assert "hasCrystallizationTargetTemperature" not in shacl
    assert "AddMustLinkAddedChemicalInputShape" not in shacl
    assert "sh:minCount 1" in shacl
    assert "sh:class ontolab:LabEquipment" in shacl
    assert '"owner_class": "Crystallize"' not in surface
    assert "hasCrystallizationTargetTemperature" not in surface


def test_has_equipment_shacl_follows_tbox_labequipment() -> None:
    from rdflib import Graph, Namespace

    repo = Path(__file__).resolve().parents[1]
    shacl = Graph()
    shacl.parse(
        repo / "src" / "kg_building" / "ontologx" / "resources" / "ontosynthesis_shacl.ttl",
        format="turtle",
    )
    sh = Namespace("http://www.w3.org/ns/shacl#")
    ontosyn = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    ontolab = Namespace("https://www.theworldavatar.com/kg/OntoLab/")
    equipment_classes = set()
    for shape in shacl.subjects(sh.targetClass, ontosyn.ChemicalSynthesis):
        for prop in shacl.objects(shape, sh.property):
            if shacl.value(prop, sh.path) == ontosyn.hasEquipment:
                equipment_classes.add(shacl.value(prop, sh["class"]))
    uses_classes = {
        shacl.value(prop, sh["class"])
        for prop in shacl.subjects(sh.path, ontosyn.usesEquipment)
    }
    assert equipment_classes == {ontolab.LabEquipment}
    assert uses_classes == {ontosyn.Equipment}


def test_pipeline_kg_imports_do_not_cycle() -> None:
    from src.extraction_runtime.steps.registry import load_step_module
    from src.kg_building.pipeline.binding import bind_kg_runtime_context, pin_entity_context

    assert callable(bind_kg_runtime_context)
    assert callable(pin_entity_context)
    top_kg = load_step_module("top_entity_kg_building")
    main_kg = load_step_module("main_kg_building")
    assert top_kg is not None and hasattr(top_kg, "run_step")
    assert main_kg is not None and hasattr(main_kg, "run_step")


def test_ox_cli_requires_protocol() -> None:
    parser = ox_cli_parser()
    args = parser.parse_args(
        ["--protocol", "with-prompt", "--out-dir", "tmp", "--hash", "0c57bac8"]
    )
    assert args.protocol == "with-prompt"
    full_args = parser.parse_args(
        ["--protocol", "full-prompt", "--out-dir", "tmp", "--hash", "0c57bac8"]
    )
    assert full_args.protocol == "full-prompt"
    try:
        parser.parse_args(["--official-onepass-guidance"])
        raised = False
    except SystemExit:
        raised = True
    assert raised


def test_protocol_locks_pipeline_steps_and_ox_knobs() -> None:
    from src.extraction_runtime.cli import build_parser as runtime_parser
    from src.kg_building.experiment_protocol import apply_to_ox_args, apply_to_pipeline_config

    parser = runtime_parser()
    args = parser.parse_args(
        ["ontosynthesis", "--protocol", "generic-strict", "--hash", "0c57bac8"]
    )
    assert args.protocol == "generic-strict"
    config = {"steps": ["extensions_kg_building"], "vision_pdf_conversion": True}
    apply_to_pipeline_config(config, args.protocol)
    assert config["experiment_protocol"] == "generic-strict"
    assert config["pipeline_kg"] == "no-contract"
    assert config["steps"][-1] == "main_kg_building"
    assert "extensions_kg_building" not in config["steps"]
    assert config["vision_pdf_conversion"] is False
    assert config["extraction_revision"] is True
    assert config["kg_revision"] is False
    assert config["official_onepass_guidance"] is False
    full_config = {"steps": ["main_kg_building"]}
    apply_to_pipeline_config(full_config, "full-prompt")
    assert full_config["experiment_protocol"] == "full-prompt"
    assert full_config["official_onepass_guidance"] is True
    assert full_config["pipeline_kg"] == "no-contract"

    ox_args = ox_cli_parser().parse_args(
        ["--protocol", "generic-noprompt", "--out-dir", "tmp"]
    )
    spec = apply_to_ox_args(ox_args, ox_args.protocol)
    assert spec.ox_prompt_profile == "generic-noprompt"
    noprompt_config = {"steps": ["main_kg_building"]}
    apply_to_pipeline_config(noprompt_config, "generic-noprompt")
    assert noprompt_config["experiment_protocol"] == "generic-noprompt"
    assert noprompt_config["pipeline_kg"] == "no-contract"
    assert ox_args.model == "openai/gpt-4o-2024-11-20"
    assert ox_args.seed == 42
    assert ox_args.kg_revision is False
    from src.kg_building.experiment_protocol import ox_summary_fields

    assert ox_summary_fields(spec)["pipeline_token_budget"] == "equivalent"
    ox_args.from_main_run = Path("somewhere")
    try:
        apply_to_ox_args(ox_args, "generic-strict")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_pipeline_noprompt_skips_chemistry_rules_for_medical() -> None:
    from src.kg_building.pipeline.binding import bind_kg_runtime_context

    syn = bind_kg_runtime_context(
        hints="SEMANTIC_HINTS_V1\nledger",
        doi="10.x",
        entity_label="UMC-1",
        protocol="generic-noprompt",
        ontology="ontosynthesis",
    )
    med = bind_kg_runtime_context(
        hints="SEMANTIC_HINTS_V1\nledger",
        doi="OPR1a",
        entity_label="Case-1",
        protocol="generic-noprompt",
        ontology="medical",
    )
    assert "# Generic-noprompt graph rules" in syn
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" in syn
    assert "Authoritative OntoSynthesis T-Box" in syn
    assert "# Generic-noprompt graph rules" not in med
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" not in med
    assert "Authoritative medical T-Box" in med
    assert "Authoritative OntoSynthesis T-Box" not in med
    assert "# Generic-noprompt medical graph rules" in med
    assert "binary_checklist" in med
    assert "Class: `Diagnosis`" in med
    strict = bind_kg_runtime_context(
        hints="SEMANTIC_HINTS_V1\nledger",
        doi="10.x",
        entity_label="UMC-1",
        protocol="generic-strict",
        ontology="ontosynthesis",
    )
    assert "# Generic-noprompt graph rules" not in strict


def test_ox_medical_requires_protocol() -> None:
    parser = ox_cli_parser()
    args = parser.parse_args(
        ["--domain", "medical", "--protocol", "generic-strict", "--out-dir", "tmp"]
    )
    assert args.protocol == "generic-strict"
    from src.kg_building.ontologx.prompt_builder import build_medical_noprompt_prompt
    from src.kg_building.ontologx.strict_noprompt import build_with_prompt_medical_prompt

    text = build_generic_noprompt_medical_prompt()
    assert "Occurrence protocol" in text
    assert "Authoritative medical T-Box" in text
    assert "There is no paper body" in text
    assert "One Add owns exactly one ontosyn:hasAddedChemicalInput" not in text
    assert "# Generic-noprompt medical graph rules" in text
    legacy = build_medical_noprompt_prompt()
    assert "Authoritative medical T-Box" in legacy
    assert "Class: `Diagnosis`" in legacy
    prompted = build_with_prompt_medical_prompt()
    assert "# With-prompt OntoMed KG-building guidance" in prompted
    assert "hasAddedChemicalInput" not in prompted
    assert "Authoritative medical T-Box" in prompted


def test_medical_protocol_keeps_vision() -> None:
    from src.kg_building.experiment_protocol import apply_to_pipeline_config

    config = {"ontology": "medical", "steps": ["main_kg_building"]}
    apply_to_pipeline_config(config, "generic-strict")
    assert config["vision_pdf_conversion"] is True
    assert config["kg_model"] == "openai/gpt-4o-2024-11-20"
    assert config["extraction_revision"] is False
    assert config["execution_profile"] == "simple_main"
    assert config["steps"][0] == "pdf_conversion"
    assert config["steps"][1] == "top_entity_extraction"
    assert "tbox_slim" not in config["steps"]
    assert config["steps"][-1] == "main_kg_building"
    assert "extensions_kg_building" not in config["steps"]


def test_loaded_ccdc_snippet_is_injected_into_every_protocol() -> None:
    from src.kg_building.pipeline.binding import bind_kg_runtime_context
    from src.kg_building.pipeline.external_mcp_prompt import (
        attached_external_mcp_contract,
    )

    kwargs = {
        "hints": "SEMANTIC_HINTS_V1\nThe CCDC number is 1576897.",
        "doi": "10.1021/acsami.7b18836",
        "entity_label": "UMC-1",
        "entity_uri": "http://example.org/umc-1",
        "mcp_tools": ["ontospecies_extension", "ccdc"],
    }
    occurrence_only = bind_kg_runtime_context(
        **{**kwargs, "mcp_tools": ["ontospecies_extension"]}
    )
    assert "Loaded external MCP: `ccdc`" not in occurrence_only
    assert attached_external_mcp_contract(["ontospecies_extension"]) == ""
    strict = bind_kg_runtime_context(**kwargs, protocol="generic-strict")
    noprompt = bind_kg_runtime_context(**kwargs, protocol="generic-noprompt")
    assert "Attached external MCP contract:" in strict
    assert "Loaded external MCP: `ccdc`" in strict
    assert "ledger-attested number" in strict
    assert "search_ccdc_by_mop_name" in strict
    assert "hasCCDCNumber_label" in strict
    assert "Loaded external MCP: `ccdc`" in noprompt
    assert noprompt.startswith(strict.rstrip())
    assert "Loaded external MCP: `pubchem`" not in strict


def test_loaded_pubchem_snippet_is_independent_of_ccdc() -> None:
    from src.kg_building.pipeline.binding import bind_kg_runtime_context

    text = bind_kg_runtime_context(
        hints="SEMANTIC_HINTS_V1\nwater",
        doi="10.x",
        entity_label="UMC-1",
        protocol="generic-strict",
        mcp_tools=["llm_created_mcp", "pubchem"],
    )
    assert "Loaded external MCP: `pubchem`" in text
    assert "Loaded external MCP: `ccdc`" not in text
    assert "Do not call PubChem to replace ledger-attested values." in text
