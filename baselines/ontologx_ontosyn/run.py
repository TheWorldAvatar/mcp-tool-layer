"""Run OntoLogX (main parser + SHACL correction) on the three OntoSynthesis papers."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.agents.scripts_and_prompts_generation.llm_global_context_resolver import (
    inject_global_context_brief,
    load_global_context_brief,
)
from extraction_hints import (
    bound_chemical_outputs,
    load_extension_ledger,
    load_hint_entities,
    set_hint_runs,
)
from iteration_guides import attach_pipeline_identity
from graph_merge import (
    attach_subgraph,
    bound_mop_outputs,
    canonicalize_reused,
    graph_inventory,
    layered_graph_inventory,
    merge_graphs,
    reusable_inventory,
    reusable_subgraph,
    reused_node_ids,
    scoped_reuse_inventory,
    reattach_detached_species_facts,
    seed_mop_targets,
    seed_reusable,
    seed_species_outputs,
)
from parser import OntoSynParser, ParseUsage
from prompt_builder import (
    DEFAULT_ONTOLOGX_PROMPT,
    DEFAULT_ONTOMOPS_TBOX,
    DEFAULT_ONTOSPECIES_TBOX,
    DEFAULT_TBOX,
    FAITHFUL_ONTOLOGX_PROMPT,
    OFFICIAL_ONEPASS_OX_CONTRACT,
    build_ontomops_prompt,
    build_ontospecies_prompt,
    build_system_prompt,
    entity_human_suffix,
    existing_graph_suffix,
    load_faithful_ontologx_prompt,
    load_original_ontologx_prompt,
    main_ttl_extension_suffix,
)
from strict_noprompt import SURFACE_PATH, build_strict_noprompt_system_prompt
from shacl_validate import validate_graph
from top_entities import load_top_entities
from ttl_export import graph_to_turtle, instance_iri, read_ttl, write_ttl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ontologx_ontosyn")


def _usage_record(parse_usage: ParseUsage, token_budget: int | None) -> dict:
    avg_round = parse_usage.total_tokens / parse_usage.calls if parse_usage.calls else 0
    first_round = (
        parse_usage.call_details[0]["total_tokens"]
        if parse_usage.call_details
        else parse_usage.total_tokens
    )
    budget = int(token_budget or 0)
    return {
        "calls": parse_usage.calls,
        "prompt_tokens": parse_usage.prompt_tokens,
        "completion_tokens": parse_usage.completion_tokens,
        "total_tokens": parse_usage.total_tokens,
        "stop_reason": parse_usage.stop_reason,
        "call_details": parse_usage.call_details,
        "avg_round_tokens": int(avg_round),
        "first_round_tokens": int(first_round),
        "estimated_rounds_at_avg": int(budget // avg_round) if avg_round and budget else None,
        "estimated_rounds_at_first": int(budget // first_round) if first_round and budget else None,
    }


def _reuse_seed(paper_graph, central_graph, *, enabled: bool):
    return seed_reusable(paper_graph, central_graph) if enabled else None


def _publish_global_reuse(central_graph, paper_graph):
    global_graph = reusable_subgraph(paper_graph, scope="global")
    if global_graph is None:
        return central_graph
    return (
        attach_subgraph(central_graph, global_graph, reuse="paper")
        if central_graph is not None
        else global_graph
    )


def _inject_entity_global_context(extra: str, entity) -> str:
    """Mirror the Pipeline's per-KG-iteration global-context injection."""
    cache_path = entity.path.parent.parent / "global_procedure_context.json"
    return inject_global_context_brief(
        extra,
        load_global_context_brief(cache_path),
    )


def _parse_layered_entity(
    ontologx,
    entity,
    paper,
    args,
    entity_dir: Path | None = None,
    seed_graph=None,
) -> tuple:
    graph = None
    layers = []
    total_usage = ParseUsage()
    last_conforms = False
    last_messages: list[str] = []
    for layer, path, text, layer_budget in entity.iter_layers():
        existing = graph
        extra = entity_human_suffix(
            entity.key,
            entity.label,
            layer=layer,
            entity_uri=getattr(entity, "uri", "") or "",
            identity_dossier=getattr(entity, "identity_dossier", None),
            include_iteration_surface=True,
        )
        if graph is not None or seed_graph is not None:
            inventory = layered_graph_inventory(graph, seed_graph)
            extra = existing_graph_suffix(inventory) + extra
        extra = _inject_entity_global_context(extra, entity)
        token_budget = None
        if args.from_extraction and not args.no_kg_budget:
            token_budget = layer_budget or entity.token_budget
        logger.info(
            "  %s %s layer=iter%s budget=%s prior_nodes=%s",
            paper["hash"],
            entity.label,
            layer,
            token_budget,
            len(graph.nodes) if graph is not None else len(seed_graph.nodes) if seed_graph else 0,
        )
        use_surface_shacl = bool(getattr(args, "layered_surface_shacl", False))
        layer_shacl = None
        if use_surface_shacl:
            from layered_shacl import layered_shacl_path

            layer_shacl = str(layered_shacl_path(layer))
        layer_graph, conforms, messages, usage = ontologx.parse(
            text,
            {
                "doi": paper["doi"],
                "hash": paper["hash"],
                "title": paper.get("title", ""),
                "entity_key": entity.key,
                "entity_label": entity.label,
                "entity_uri": getattr(entity, "uri", "") or "",
                "source": f"iter{layer}_hints",
                "layer": layer,
                "hint_path": str(path),
                "hint_run": entity.run,
            },
            paper["hash"],
            extra_human=extra,
            token_budget=token_budget,
            existing_graph=existing,
            require_shacl=True if use_surface_shacl else layer >= 3,
            shacl_path=layer_shacl,
        )
        if seed_graph is not None:
            layer_graph = canonicalize_reused(layer_graph, seed_graph)
        if layer_graph is not None:
            graph = layer_graph
        total_usage.calls += usage.calls
        total_usage.prompt_tokens += usage.prompt_tokens
        total_usage.completion_tokens += usage.completion_tokens
        total_usage.total_tokens += usage.total_tokens
        total_usage.call_details.extend(usage.call_details)
        total_usage.stop_reason = usage.stop_reason
        last_conforms = bool(conforms and graph is not None)
        last_messages = messages
        layer_ttl = None
        if graph is not None and entity_dir is not None:
            layer_ttl = write_ttl(
                graph,
                paper["hash"],
                entity_dir / f"{entity.slug}_iter{layer}.ttl",
            )
        layers.append(
            {
                "layer": layer,
                "hint": str(path),
                "conforms": conforms,
                "n_nodes": len(graph.nodes) if graph is not None else 0,
                "n_relationships": len(graph.relationships) if graph is not None else 0,
                "ttl": str(layer_ttl) if layer_ttl else None,
                "token_budget": layer_budget,
                "token_usage": _usage_record(usage, layer_budget),
                "stop_reason": usage.stop_reason,
            }
        )
    return graph, last_conforms, last_messages, total_usage, layers


def _parse_full_hints_entity(
    ontologx,
    entity,
    paper,
    args,
    entity_dir: Path,
    paper_graph=None,
    central_graph=None,
) -> tuple:
    """Build one complete ChemicalSynthesis graph from all hint layers."""
    bundle = entity.full_hints()
    combined_path = entity_dir / f"{entity.slug}_full_hints.txt"
    combined_path.write_text(bundle.text, encoding="utf-8")
    extra = entity_human_suffix(
        entity.key,
        entity.label,
        entity_uri=getattr(entity, "uri", "") or "",
        identity_dossier=getattr(entity, "identity_dossier", None),
        include_iteration_surface=False,
    )
    extra += "\n\nReusable entity inventories (reuse only when actually used):\n"
    extra += scoped_reuse_inventory(paper_graph, central_graph)
    extra = _inject_entity_global_context(extra, entity)
    token_budget = None if args.no_kg_budget else entity.token_budget
    logger.info(
        "  %s %s full-hints layers=%s budget=%s",
        paper["hash"],
        entity.label,
        list(bundle.layers),
        token_budget,
    )
    graph, conforms, messages, usage = ontologx.parse(
        bundle.text,
        {
            "doi": paper["doi"],
            "hash": paper["hash"],
            "title": paper.get("title", ""),
            "entity_key": entity.key,
            "entity_label": entity.label,
            "entity_uri": getattr(entity, "uri", "") or "",
            "source": "full_hints",
            "hint_paths": [str(path) for path in bundle.paths],
            "hint_run": entity.run,
        },
        paper["hash"],
        extra_human=extra,
        token_budget=token_budget,
        existing_graph=None,
        require_shacl=True,
    )
    seed = seed_reusable(paper_graph, central_graph)
    graph = canonicalize_reused(graph, seed)
    return graph, conforms, messages, usage, {
        "full_hints": str(combined_path),
        "hint_layers": list(bundle.layers),
        "hint_paths": [str(path) for path in bundle.paths],
    }


def _find_main_entity_ttl(main_run: Path, paper_hash: str, slug: str) -> Path | None:
    direct = main_run / paper_hash / slug / f"{slug}.ttl"
    if direct.exists():
        return direct
    if not main_run.is_dir():
        return None
    for child in sorted(main_run.iterdir()):
        candidate = child / paper_hash / slug / f"{slug}.ttl"
        if candidate.exists():
            return candidate
    return None


_EXTENSION_CLASS = {
    "ontospecies": (
        "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species",
        "configs/sparql/extensions/ontospecies_enrichment_target.sparql",
    ),
    "ontomops": (
        "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron",
        "configs/sparql/extensions/ontomops_enrichment_target.sparql",
    ),
}


def _parse_extension_entity(
    ontologx,
    entity,
    paper,
    args,
    existing,
    entity_dir: Path | None = None,
    domain: str = "ontospecies",
) -> tuple:
    hint_path, ledger = load_extension_ledger(
        paper["hash"],
        entity.label,
        domain,
        uri=getattr(entity, "uri", "") or "",
    )
    if hint_path is None:
        logger.info(
            "  %s %s extension=%s skipped (no hint)",
            paper["hash"],
            entity.label,
            domain,
        )
        record = {
            "extension": domain,
            "hint": None,
            "skipped": "no_hint",
            "conforms": True,
            "n_nodes": len(existing.nodes) if existing is not None else 0,
            "n_relationships": len(existing.relationships) if existing is not None else 0,
            "ttl": None,
            "token_budget": None,
            "budget_detail": None,
            "token_usage": _usage_record(ParseUsage(), None),
            "stop_reason": "no_hint",
            "bound_outputs": bound_chemical_outputs(existing),
        }
        return existing, True, [], ParseUsage(), record
    if domain == "ontospecies":
        existing = seed_species_outputs(existing)
        bound = bound_chemical_outputs(existing)
        target_key = "output_id"
    else:
        existing = seed_mop_targets(existing)
        bound = bound_mop_outputs(existing)
        target_key = "mop_id"
    class_iri, sparql = _EXTENSION_CLASS[domain]
    extra = entity_human_suffix(
        entity.key,
        entity.label,
        entity_uri=getattr(entity, "uri", "") or "",
        identity_dossier=getattr(entity, "identity_dossier", None),
    )
    if existing is not None:
        targets = [
            {
                "name": "primary",
                "target_iri": instance_iri(paper["hash"], row[target_key]),
                "class_iri": class_iri,
                "source": sparql,
            }
            for row in bound
        ]
        extra += main_ttl_extension_suffix(
            graph_to_turtle(existing, paper["hash"]),
            enrichment_targets=targets,
        )
    token_budget = None
    budget_detail = None
    if not args.no_kg_budget:
        from kg_token_budget import entity_extension_budget

        budget_detail = entity_extension_budget(
            paper["hash"],
            entity.label,
            domain=domain,
            run=entity.run,
        )
        if budget_detail:
            token_budget = int(budget_detail["total_tokens"])
    logger.info(
        "  %s %s extension=%s budget=%s prior_nodes=%s",
        paper["hash"],
        entity.label,
        domain,
        token_budget,
        len(existing.nodes) if existing is not None else 0,
    )
    graph, conforms, messages, usage = ontologx.parse(
        ledger,
        {
            "doi": paper["doi"],
            "hash": paper["hash"],
            "title": paper.get("title", ""),
            "entity_key": entity.key,
            "entity_label": entity.label,
            "entity_uri": getattr(entity, "uri", "") or "",
            "source": f"{domain}_hints",
            "extension": domain,
            "hint_path": str(hint_path),
            "hint_run": entity.run,
        },
        paper["hash"],
        extra_human=extra,
        token_budget=token_budget,
        existing_graph=existing,
        require_shacl=True,
    )
    if domain == "ontospecies" and graph is not None:
        graph = reattach_detached_species_facts(graph)
    ext_ttl = None
    if graph is not None and entity_dir is not None:
        ext_ttl = write_ttl(
            graph,
            paper["hash"],
            entity_dir / f"{entity.slug}_{domain}.ttl",
        )
    record = {
        "extension": domain,
        "hint": str(hint_path),
        "conforms": conforms,
        "n_nodes": len(graph.nodes) if graph is not None else 0,
        "n_relationships": len(graph.relationships) if graph is not None else 0,
        "ttl": str(ext_ttl) if ext_ttl else None,
        "token_budget": token_budget,
        "budget_detail": budget_detail,
        "token_usage": _usage_record(usage, token_budget),
        "stop_reason": usage.stop_reason,
        "bound_outputs": bound,
    }
    return graph, conforms, messages, usage, record


def _load_papers(repo_root: Path, papers_path: Path) -> list[dict]:
    payload = json.loads(papers_path.read_text(encoding="utf-8"))
    papers = []
    for item in payload["papers"]:
        text_path = repo_root / item["text"]
        if not text_path.exists():
            raise FileNotFoundError(f"Missing paper text for {item['hash']}: {text_path}")
        papers.append({**item, "text_path": text_path, "text": text_path.read_text(encoding="utf-8")})
    return papers


def main() -> None:
    load_dotenv(REPO_ROOT / ".env", override=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, default=HERE / "papers.json")
    parser.add_argument("--hash", action="append", dest="hashes", default=None)
    parser.add_argument(
        "--label",
        action="append",
        dest="labels",
        default=None,
        help="Only run these ChemicalSynthesis labels (substring match).",
    )
    parser.add_argument("--model", default="gpt-4o", help="Must match configs/extraction_models.json")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Best-effort provider sampling seed (default: 42).",
    )
    parser.add_argument(
        "--openrouter-provider",
        default=None,
        help="Pin one OpenRouter provider and disable fallback (for reproducibility).",
    )
    parser.add_argument(
        "--correction-steps",
        type=int,
        default=3,
        help="Legacy round cap when --no-kg-budget. With a KG token budget this is only a safety floor for max rounds.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="Hard cap on LLM rounds per entity (default: 64 when matching KG budget).",
    )
    parser.add_argument(
        "--spend-full-budget",
        action="store_true",
        help="Keep refining after SHACL passes until the pipeline KG-building token budget is used up.",
    )
    parser.add_argument(
        "--no-kg-budget",
        action="store_true",
        help="Disable per-entity pipeline KG token matching; use --correction-steps as a dead round limit.",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--tbox-prompt",
        type=Path,
        default=DEFAULT_TBOX,
        help="OntoSynthesis T-Box markdown (default data/ontologies/ontosynthesis_parsed.md)",
    )
    parser.add_argument(
        "--prompt-profile",
        choices=(
            "ontosynthesis",
            "ontologx-original",
            "ontologx-faithful",
            "ontosynthesis-strict-noprompt",
        ),
        default="ontosynthesis",
        help=(
            "System prompt profile. 'ontosynthesis' is this adapter's long T-Box + "
            "construction contracts; 'ontologx-original' is upstream cybersecurity "
            "main.system.md verbatim; 'ontologx-faithful' is the short OntoSynthesis "
            "analog of that prompt (T-Box only in the tool schema); "
            "'ontosynthesis-strict-noprompt' keeps only the official Pipeline "
            "occurrence ownership/attachment surface, rewritten for SynthesisGraph."
        ),
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Old whole-paper parse (no top-entity inventory).",
    )
    parser.add_argument(
        "--top-entities-root",
        type=Path,
        default=REPO_ROOT / "scenarios" / "mops" / "runs" / "20260825_eval30_gpt4o-topentity" / "runtime",
        help="Pipeline runtime root that already has top_entities.txt (judges applied).",
    )
    parser.add_argument(
        "--from-extraction",
        action="store_true",
        help="Materialize pipeline iter3 hint ledgers (KG-building comparison, not paper extraction).",
    )
    parser.add_argument(
        "--hint-runs",
        nargs="+",
        default=None,
        help=(
            "Priority list of scenarios/mops/runs/<id> for iter2/3/4 hints and "
            "KG token budgets. Default is the v4 e2e HINT_RUNS list."
        ),
    )
    construction_mode = parser.add_mutually_exclusive_group()
    construction_mode.add_argument(
        "--layered",
        action="store_true",
        help="End-to-end: iter2 skeleton, then attach iter3/iter4 subgraphs onto the prior graph.",
    )
    construction_mode.add_argument(
        "--full-hints",
        action="store_true",
        help="Build one complete entity graph from its combined iter2/iter3/iter4 hints.",
    )
    parser.add_argument(
        "--official-onepass-guidance",
        action="store_true",
        help=(
            "Use the fixed OntoLogX-native rendering of the official Pipeline "
            "ONEPASS semantic contract. Requires --full-hints."
        ),
    )
    parser.add_argument(
        "--no-entity-reuse",
        action="store_true",
        help="Disable paper-level entity reuse (Document/Supplier/atmosphere). Layer attach still runs.",
    )
    parser.add_argument(
        "--layered-surface-shacl",
        action="store_true",
        help=(
            "Parallel oracle: validate each layer with that iteration's owned-surface "
            "SHACL (iter2 included). Default remains full-graph SHACL from iter3. "
            "Paper merge still uses the full-graph shapes."
        ),
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        choices=("ontospecies", "ontomops"),
        default=None,
        help="After the main OntoSyn graph, attach this extension. Repeatable.",
    )
    parser.add_argument(
        "--from-main-run",
        type=Path,
        default=None,
        help=(
            "Reuse existing OntoLogX entity TTLs as the inherited main graph and "
            "skip OntoSyn layers. Directory may be a run root or contain shard*."
        ),
    )
    args = parser.parse_args()
    if args.official_onepass_guidance and not args.full_hints:
        parser.error("--official-onepass-guidance requires --full-hints")
    if args.official_onepass_guidance and args.prompt_profile != "ontosynthesis":
        parser.error(
            "--official-onepass-guidance requires --prompt-profile ontosynthesis"
        )
    if args.hint_runs:
        set_hint_runs(args.hint_runs)

    ontology_path = REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
    shacl_path = HERE / "resources" / "ontosynthesis_shacl.ttl"
    if not shacl_path.exists():
        raise FileNotFoundError(f"SHACL file missing: {shacl_path}. Run generate_shacl.py first.")
    per_entity = not args.oneshot or args.from_extraction
    if args.from_extraction:
        per_entity = True
    if args.layered:
        args.from_extraction = True
        per_entity = True
    if args.full_hints:
        args.from_extraction = True
        per_entity = True
    if args.from_main_run is not None:
        args.from_extraction = True
        per_entity = True
        if not args.from_main_run.is_absolute():
            args.from_main_run = REPO_ROOT / args.from_main_run
    if args.extensions:
        args.from_extraction = True
        per_entity = True
    entity_reuse = bool(per_entity and not args.no_entity_reuse)

    if not args.top_entities_root.is_absolute():
        args.top_entities_root = REPO_ROOT / args.top_entities_root
    papers = _load_papers(REPO_ROOT, args.papers)
    if args.hashes:
        wanted = set(args.hashes)
        papers = [paper for paper in papers if paper["hash"] in wanted]
    if not papers:
        raise SystemExit("No papers selected.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    profile_suffix = (
        ""
        if args.prompt_profile == "ontosynthesis"
        else f"_{args.prompt_profile}"
    )
    out_dir = args.out_dir or HERE / "runs" / (
        f"{stamp}_{args.model.replace('/', '-')}{profile_suffix}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.prompt_profile == "ontologx-original":
        prompt = load_original_ontologx_prompt()
        prompt_source = DEFAULT_ONTOLOGX_PROMPT
        input_label = "Event"
    elif args.prompt_profile == "ontologx-faithful":
        prompt = load_faithful_ontologx_prompt()
        prompt_source = FAITHFUL_ONTOLOGX_PROMPT
        input_label = "Source"
    elif args.prompt_profile == "ontosynthesis-strict-noprompt":
        prompt = build_strict_noprompt_system_prompt()
        prompt_source = SURFACE_PATH
        input_label = "Paper"
        (HERE / "resources" / "ontosyn_system.generated.md").write_text(
            prompt,
            encoding="utf-8",
        )
    else:
        prompt = build_system_prompt(
            args.tbox_prompt,
            per_entity=per_entity,
            from_extraction=args.from_extraction,
            layered=args.layered,
            full_hints=args.full_hints,
            entity_reuse=entity_reuse,
            official_onepass_guidance=args.official_onepass_guidance,
        )
        prompt_source = (
            OFFICIAL_ONEPASS_OX_CONTRACT
            if args.official_onepass_guidance
            else args.tbox_prompt
        )
        input_label = "Paper"
        (HERE / "resources" / "ontosyn_system.generated.md").write_text(
            prompt,
            encoding="utf-8",
        )
    effective_prompt_path = out_dir / "system_prompt.md"
    effective_prompt_path.write_text(prompt, encoding="utf-8")

    provider_routing = (
        {
            "provider": {
                "order": [args.openrouter_provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        }
        if args.openrouter_provider
        else None
    )
    llm = LLMCreator(
        model=args.model,
        remote_model=True,
        model_config=ModelConfig(
            timeout=600,
            temperature=0.0,
            seed=args.seed,
            extra_body=provider_routing,
        ),
        structured_output=False,
    ).setup_llm()
    ontologx = OntoSynParser(
        llm=llm,
        ontology_path=str(ontology_path),
        shacl_path=str(shacl_path),
        prompt=prompt,
        correction_steps=args.correction_steps,
        spend_full_budget=args.spend_full_budget,
        max_rounds=args.max_rounds,
        input_label=input_label,
    )
    extension_parsers: dict[str, OntoSynParser] = {}
    extension_assets: dict[str, tuple[Path, Path]] = {}
    for domain in args.extensions or []:
        if domain == "ontospecies":
            ext_ontology = REPO_ROOT / "data" / "ontologies" / "ontospecies-subgraph.ttl"
            ext_shacl = HERE / "resources" / "ontospecies_shacl.ttl"
            if not ext_shacl.exists():
                from generate_ontospecies_shacl import write_shapes

                write_shapes(ext_shacl)
            ext_prompt = build_ontospecies_prompt(DEFAULT_ONTOSPECIES_TBOX)
        else:
            ext_ontology = REPO_ROOT / "data" / "ontologies" / "ontomops-subgraph.ttl"
            ext_shacl = HERE / "resources" / "ontomops_shacl.ttl"
            if not ext_shacl.exists():
                from generate_ontomops_shacl import write_shapes as write_om_shapes

                write_om_shapes(ext_shacl)
            ext_prompt = build_ontomops_prompt(DEFAULT_ONTOMOPS_TBOX)
        (out_dir / f"{domain}_system_prompt.md").write_text(ext_prompt, encoding="utf-8")
        extension_assets[domain] = (ext_ontology, ext_shacl)
        extension_parsers[domain] = OntoSynParser(
            llm=llm,
            ontology_path=str(ext_ontology),
            shacl_path=str(ext_shacl),
            prompt=ext_prompt,
            correction_steps=args.correction_steps,
            spend_full_budget=args.spend_full_budget,
            max_rounds=args.max_rounds,
            input_label="Ledger",
        )

    summary = {
        "model": args.model,
        "seed": args.seed,
        "openrouter_provider": args.openrouter_provider,
        "openrouter_fallbacks": False if args.openrouter_provider else None,
        "parser": (
            "ontologx-extension"
            if args.from_main_run is not None and args.extensions
            else "ontologx-full-hints"
            if args.full_hints
            else "ontologx-layered"
            if args.layered
            else "ontologx-from-extraction"
            if args.from_extraction
            else "ontologx-per-top-entity"
            if per_entity
            else "ontologx-main"
        ),
        "layered": bool(args.layered),
        "layered_surface_shacl": bool(args.layered_surface_shacl),
        "full_hints": bool(args.full_hints),
        "official_onepass_guidance": bool(args.official_onepass_guidance),
        "extension": list(args.extensions or []),
        "from_main_run": str(args.from_main_run) if args.from_main_run else None,
        "entity_reuse": entity_reuse,
        "prompt_profile": args.prompt_profile,
        "system_prompt": str(effective_prompt_path),
        "prompt_source": str(prompt_source),
        "input_label": input_label,
        "correction_steps": args.correction_steps,
        "ontology": str(ontology_path),
        "shacl": str(shacl_path),
        "extension_ontology": {
            domain: str(paths[0]) for domain, paths in extension_assets.items()
        },
        "extension_shacl": {
            domain: str(paths[1]) for domain, paths in extension_assets.items()
        },
        "tbox_prompt": (
            str(args.tbox_prompt)
            if args.prompt_profile == "ontosynthesis"
            else None
        ),
        "strict_noprompt": args.prompt_profile == "ontosynthesis-strict-noprompt",
        "top_entities_root": str(args.top_entities_root) if per_entity else None,
        "hint_runs": list(args.hint_runs) if args.hint_runs else None,
        "match_kg_budget": bool(args.from_extraction and not args.no_kg_budget),
        "spend_full_budget": bool(args.spend_full_budget),
        "papers": [],
    }
    central_graph = None
    for paper in papers:
        logger.info("Parsing %s (%s)", paper["hash"], paper["doi"])
        paper_dir = out_dir / paper["hash"]
        paper_dir.mkdir(parents=True, exist_ok=True)
        entity_records = []
        if per_entity:
            if args.from_extraction:
                entities = load_hint_entities(paper["hash"])
            else:
                entities = load_top_entities(args.top_entities_root / paper["hash"])
            graphs = []
            paper_graph = None
            paper_conforms = True
            paper_messages: list[str] = []
            if args.labels:
                wanted_labels = [item.casefold() for item in args.labels]
                entities = [
                    entity
                    for entity in entities
                    if any(item in entity.label.casefold() for item in wanted_labels)
                ]
                if not entities:
                    raise SystemExit(f"No entities matched --label {args.labels} for {paper['hash']}")
            for entity in entities:
                identity_root = (
                    entity.path.parent.parent
                    if args.from_extraction
                    else args.top_entities_root / paper["hash"]
                )
                entity = attach_pipeline_identity(entity, identity_root)
                logger.info("  %s %s", paper["hash"], getattr(entity, "line", entity.label))
                entity_dir = paper_dir / entity.slug
                entity_dir.mkdir(parents=True, exist_ok=True)
                layer_records = []
                full_hints_record = None
                seed_graph = _reuse_seed(
                    paper_graph,
                    central_graph,
                    enabled=entity_reuse,
                )
                if seed_graph is not None:
                    logger.info(
                        "  %s reuse seed nodes=%s",
                        getattr(entity, "line", entity.label),
                        len(seed_graph.nodes),
                    )
                if args.from_main_run is not None:
                    main_ttl = _find_main_entity_ttl(
                        args.from_main_run, paper["hash"], entity.slug
                    )
                    if main_ttl is None:
                        raise FileNotFoundError(
                            f"No inherited main TTL for {paper['hash']}/{entity.slug} "
                            f"under {args.from_main_run}"
                        )
                    graph = read_ttl(main_ttl, paper["hash"])
                    write_ttl(graph, paper["hash"], entity_dir / f"{entity.slug}_main.ttl")
                    conforms = True
                    messages = []
                    parse_usage = ParseUsage()
                    logger.info(
                        "  %s inherited main %s nodes=%s",
                        getattr(entity, "line", entity.label),
                        main_ttl,
                        len(graph.nodes),
                    )
                elif args.full_hints:
                    graph, conforms, messages, parse_usage, full_hints_record = (
                        _parse_full_hints_entity(
                            ontologx,
                            entity,
                            paper,
                            args,
                            entity_dir,
                            paper_graph=paper_graph if entity_reuse else None,
                            central_graph=central_graph if entity_reuse else None,
                        )
                    )
                elif args.layered:
                    graph, conforms, messages, parse_usage, layer_records = _parse_layered_entity(
                        ontologx,
                        entity,
                        paper,
                        args,
                        entity_dir=entity_dir,
                        seed_graph=seed_graph,
                    )
                else:
                    source_text = entity.text if args.from_extraction else paper["text"]
                    context = {
                        "doi": paper["doi"],
                        "hash": paper["hash"],
                        "title": paper.get("title", ""),
                        "entity_key": entity.key,
                        "entity_label": entity.label,
                        "entity_uri": getattr(entity, "uri", "") or "",
                        "source": "iter3_hints" if args.from_extraction else "paper",
                    }
                    token_budget = None
                    extra = entity_human_suffix(
                        entity.key,
                        entity.label,
                        entity_uri=getattr(entity, "uri", "") or "",
                        identity_dossier=getattr(entity, "identity_dossier", None),
                    )
                    if seed_graph is not None:
                        extra = existing_graph_suffix(reusable_inventory(seed_graph)) + extra
                    extra = _inject_entity_global_context(extra, entity)
                    if args.from_extraction:
                        context["hint_path"] = str(entity.path)
                        context["hint_run"] = entity.run
                        if not args.no_kg_budget:
                            token_budget = entity.token_budget
                            context["token_budget"] = token_budget
                            logger.info(
                                "  %s budget=%s tokens (pipeline KG building)",
                                getattr(entity, "line", entity.label),
                                token_budget,
                            )
                    graph, conforms, messages, parse_usage = ontologx.parse(
                        source_text,
                        context,
                        paper["hash"],
                        extra_human=extra,
                        token_budget=token_budget,
                        existing_graph=seed_graph,
                    )
                extension_records = []
                for domain, ext_parser in extension_parsers.items():
                    if graph is None:
                        break
                    graph, ext_ok, ext_messages, ext_usage, extension_record = (
                        _parse_extension_entity(
                            ext_parser,
                            entity,
                            paper,
                            args,
                            existing=graph,
                            entity_dir=entity_dir,
                            domain=domain,
                        )
                    )
                    extension_records.append(extension_record)
                    conforms = bool(conforms and ext_ok)
                    messages = list(ext_messages) + list(messages)
                    parse_usage.calls += ext_usage.calls
                    parse_usage.prompt_tokens += ext_usage.prompt_tokens
                    parse_usage.completion_tokens += ext_usage.completion_tokens
                    parse_usage.total_tokens += ext_usage.total_tokens
                    parse_usage.call_details.extend(ext_usage.call_details)
                    parse_usage.stop_reason = ext_usage.stop_reason or parse_usage.stop_reason
                entity_ttl = None
                reused = reused_node_ids(seed_graph, graph)
                if graph is not None:
                    graphs.append(graph)
                    entity_ttl = write_ttl(graph, paper["hash"], entity_dir / f"{entity.slug}.ttl")
                    if entity_reuse:
                        paper_graph = (
                            attach_subgraph(paper_graph, graph, reuse="paper")
                            if paper_graph is not None
                            else graph
                        )
                paper_conforms = paper_conforms and bool(conforms and graph is not None)
                paper_messages.extend(messages[:8])
                entity_record = {
                    "key": entity.key,
                    "label": entity.label,
                    "uri": getattr(entity, "uri", "") or "",
                    "conforms": conforms,
                    "n_nodes": len(graph.nodes) if graph is not None else 0,
                    "n_relationships": len(graph.relationships) if graph is not None else 0,
                    "ttl": str(entity_ttl) if entity_ttl else None,
                    "reused_ids": reused,
                }
                if args.from_extraction:
                    entity_record["hint"] = str(entity.path)
                    entity_record["hint_run"] = entity.run
                    entity_record["token_budget"] = entity.token_budget
                    entity_record["budget_detail"] = entity.budget_detail
                if layer_records:
                    entity_record["layers"] = layer_records
                if full_hints_record:
                    entity_record.update(full_hints_record)
                if extension_records:
                    entity_record["extensions"] = extension_records
                    entity_record["extension"] = extension_records[-1]
                entity_record["token_usage"] = _usage_record(parse_usage, entity.token_budget)
                entity_records.append(entity_record)
            graph = (
                paper_graph
                if entity_reuse and paper_graph is not None
                else merge_graphs(graphs, reuse="prefix" if args.no_entity_reuse else "paper")
            )
            if graph is not None:
                merged_ok, merged_messages, _ = validate_graph(
                    graph, ontology_path, shacl_path, paper["hash"]
                )
                paper_conforms = paper_conforms and merged_ok
                paper_messages = merged_messages + paper_messages
                for _domain, (ext_ontology, ext_shacl) in extension_assets.items():
                    ext_ok, ext_msgs, _ = validate_graph(
                        graph, ext_ontology, ext_shacl, paper["hash"]
                    )
                    paper_conforms = paper_conforms and ext_ok
                    paper_messages = ext_msgs + paper_messages
            conforms = paper_conforms
            messages = paper_messages
            if entity_reuse and paper_graph is not None:
                central_graph = _publish_global_reuse(central_graph, paper_graph)
        else:
            context = {"doi": paper["doi"], "hash": paper["hash"], "title": paper.get("title", "")}
            graph, conforms, messages, parse_usage = ontologx.parse(paper["text"], context, paper["hash"])
        ttl_path = None
        if graph is not None:
            ttl_path = write_ttl(graph, paper["hash"], paper_dir / f"{paper['hash']}.ttl")
            (paper_dir / "graph_preview.json").write_text(
                json.dumps(
                    {
                        "n_nodes": len(graph.nodes),
                        "n_relationships": len(graph.relationships),
                        "types": sorted({node.type for node in graph.nodes}),
                        "entities": entity_records,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        record = {
            "hash": paper["hash"],
            "doi": paper["doi"],
            "conforms": conforms,
            "n_nodes": len(graph.nodes) if graph is not None else 0,
            "n_relationships": len(graph.relationships) if graph is not None else 0,
            "ttl": str(ttl_path) if ttl_path else None,
            "entities": entity_records,
            "shacl_messages": messages[:30],
            "token_budget": sum(int(item.get("token_budget") or 0) for item in entity_records),
            "token_usage": {
                "calls": sum(int((item.get("token_usage") or {}).get("calls") or 0) for item in entity_records),
                "total_tokens": sum(
                    int((item.get("token_usage") or {}).get("total_tokens") or 0) for item in entity_records
                ),
                "estimated_rounds_at_first": [
                    {
                        "label": item["label"],
                        "budget": item.get("token_budget"),
                        "first_round": (item.get("token_usage") or {}).get("first_round_tokens"),
                        "rounds": (item.get("token_usage") or {}).get("estimated_rounds_at_first"),
                    }
                    for item in entity_records
                ],
            },
        }
        summary["papers"].append(record)
        logger.info(
            "%s done: nodes=%s rels=%s entities=%s shacl_conforms=%s",
            paper["hash"],
            record["n_nodes"],
            record["n_relationships"],
            len(entity_records),
            conforms,
        )

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %s", out_dir / "summary.json")


if __name__ == "__main__":
    main()
