"""Per-paper OntoLogX KG building. No revision loop beyond SHACL correction rounds."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.kg_building.ontologx.paths import HERE, REPO_ROOT
from extraction_hints import (
    bound_chemical_outputs,
    load_extension_ledger,
    load_hint_entities,
    set_hint_runs,
)
from generate_medical_shacl import write_shapes as write_medical_shapes
from generate_ontomops_shacl import write_shapes as write_ontomops_shapes
from generate_ontospecies_shacl import write_shapes as write_ontospecies_shapes
from graph_merge import (
    attach_subgraph,
    bound_mop_outputs,
    canonicalize_reused,
    reusable_subgraph,
    scope_occurrence_ids,
    scoped_reuse_inventory,
    seed_mop_targets,
    seed_reusable,
    seed_species_outputs,
)
from iteration_guides import attach_pipeline_identity
from medical_hints import load_medical_entities
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from parser import OntoSynParser
from prompt_builder import (
    build_full_prompt_system_prompt,
    build_ontomops_prompt,
    build_ontospecies_prompt,
    entity_human_suffix,
    main_ttl_extension_suffix,
)
from src.kg_building.experiment_protocol import ox_summary_fields, resolve_protocol
from publish_runtime import entity_write_scope, publish_spliced_runtime
from shacl_validate import validate_graph
from splice import peel_product_step_types, splice_extension_layer
from strict_noprompt import (
    build_generic_noprompt_medical_prompt,
    build_generic_noprompt_system_prompt,
    build_strict_noprompt_extension_prompt,
    build_strict_noprompt_medical_prompt,
    build_strict_noprompt_system_prompt,
    build_with_prompt_hops_system_prompt,
    build_with_prompt_medical_prompt,
    build_with_prompt_qty_system_prompt,
    build_with_prompt_system_prompt,
)
from ttl_export import graph_to_turtle, instance_iri, read_ttl, write_ttl

logger = logging.getLogger("kg_building.ontologx")

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


def reuse_extra_human(entity, paper_graph, central_graph) -> str:
    suffix = entity_human_suffix(
        getattr(entity, "key", "") or getattr(entity, "label", ""),
        getattr(entity, "label", ""),
        entity_uri=getattr(entity, "uri", "") or "",
        identity_dossier=getattr(entity, "identity_dossier", None),
    )
    return (
        suffix
        + "\nReusable entity inventories (reuse only when actually used):\n"
        + scoped_reuse_inventory(paper_graph, central_graph)
        + "\n"
    )


def _entity_scope(entity) -> str:
    return entity_write_scope(
        getattr(entity, "uri", "") or "",
        getattr(entity, "slug", "") or "",
        getattr(entity, "label", "") or "",
    )


def _bind_occurrence_ids(graph, entity):
    """Namespace occurrence-local ids before any TTL write or paper attach."""
    if graph is None:
        return None
    return scope_occurrence_ids(graph, _entity_scope(entity))


def compose_ox_human(ledger: str, context: dict, extra_human: str = "") -> str:
    return f"Paper:\n{ledger}\n\nContext: {context}{extra_human}"


def canonicalize_with_seed(graph, paper_graph, central_graph):
    return canonicalize_reused(graph, seed_reusable(paper_graph, central_graph))


def accumulate_central(central_graph, paper_graph):
    global_part = reusable_subgraph(paper_graph, scope="global")
    if global_part is None:
        return central_graph
    if central_graph is None:
        return global_part
    return attach_subgraph(central_graph, global_part, reuse="paper")


def _find_main_entity_ttl(main_run: Path, paper_hash: str, slug: str, label: str = "") -> Path | None:
    direct = main_run / paper_hash / slug / f"{slug}.ttl"
    if direct.exists():
        return direct
    for root in (main_run, main_run / "runtime"):
        folder = root / paper_hash / "ontosynthesis_output"
        if not folder.is_dir():
            continue
        ttls = [path for path in folder.glob("*.ttl") if path.name != "top.ttl"]
        token = (label or "").replace(" ", "_")
        if token:
            matched = [path for path in ttls if token in path.name or label in path.name]
            if len(matched) == 1:
                return matched[0]
        if len(ttls) == 1:
            return ttls[0]
    return None


def _parse_extension(
    parser,
    entity,
    paper,
    existing,
    entity_dir: Path,
    domain: str,
    extra_reuse: str = "",
):
    hint_path, ledger = load_extension_ledger(
        paper["hash"],
        entity.label,
        domain,
        uri=getattr(entity, "uri", "") or "",
    )
    if hint_path is None:
        return existing, {"extension": domain, "skipped": "no_hint"}
    if domain == "ontospecies":
        existing = seed_species_outputs(existing)
        bound = bound_chemical_outputs(existing)
        target_key = "output_id"
    else:
        existing = seed_mop_targets(existing)
        bound = bound_mop_outputs(existing)
        target_key = "mop_id"
    class_iri, sparql = _EXTENSION_CLASS[domain]
    extra = ""
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
    extra += extra_reuse
    human = compose_ox_human(ledger, {
            "doi": paper["doi"],
            "hash": paper["hash"],
            "entity_label": entity.label,
            "entity_uri": getattr(entity, "uri", "") or "",
            "source": f"{domain}_hints",
            "extension": domain,
            "hint_path": str(hint_path),
        }, extra)
    (entity_dir / f"{entity.slug}_{domain}.human.md").write_text(human, encoding="utf-8")
    graph, conforms, messages, usage = parser.parse(
        ledger,
        {
            "doi": paper["doi"],
            "hash": paper["hash"],
            "entity_label": entity.label,
            "entity_uri": getattr(entity, "uri", "") or "",
            "source": f"{domain}_hints",
            "extension": domain,
            "hint_path": str(hint_path),
            "graph_fix": lambda graph, _domain=domain: splice_extension_layer(graph, _domain),
        },
        paper["hash"],
        extra_human=extra,
        existing_graph=existing,
        require_shacl=True,
        token_budget=None,
    )
    graph = splice_extension_layer(graph, domain)
    if graph is not None:
        graph = canonicalize_with_seed(graph, existing, None)
        graph = _bind_occurrence_ids(graph, entity)
    ttl = None
    if graph is not None:
        ttl = write_ttl(
            graph,
            paper["hash"],
            entity_dir / f"{entity.slug}_{domain}.ttl",
            scope=_entity_scope(entity),
        )
    return graph, {
        "extension": domain,
        "hint": str(hint_path),
        "conforms": conforms,
        "n_nodes": len(graph.nodes) if graph is not None else 0,
        "ttl": str(ttl) if ttl else None,
        "shacl_messages": messages[:8],
        "token_usage": {
            "calls": usage.calls,
            "total_tokens": usage.total_tokens,
            "stop_reason": usage.stop_reason,
        },
        "bound_outputs": bound,
    }


_MEDICAL_SHACL_MARKERS = (
    "ExclusiveSurgicalApproachShape",
    "sh:datatype xsd:integer",
    'sh:in ( "1"^^xsd:string )',
)


def _assert_medical_shacl_oracle(path: Path) -> None:
    """Refuse the old MedicalCase-label stub so OX cannot skip datatype repair."""
    text = Path(path).read_text(encoding="utf-8")
    missing = [marker for marker in _MEDICAL_SHACL_MARKERS if marker not in text]
    if missing:
        raise RuntimeError(
            f"Medical OX SHACL at {path} is missing {missing}; "
            "regenerate with generate_medical_shacl.py"
        )


def _make_parser(llm, ontology: Path, shacl: Path, prompt: str) -> OntoSynParser:
    return OntoSynParser(
        llm=llm,
        ontology_path=str(ontology),
        shacl_path=str(shacl),
        prompt=prompt,
        correction_steps=3,
        input_label="Paper",
    )


def run_papers(args) -> dict:
    if args.hint_runs:
        set_hint_runs(args.hint_runs)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime_root = out_dir / "score_runtime"
    llm = LLMCreator(
        model=args.model,
        remote_model=True,
        model_config=ModelConfig(timeout=1800, temperature=0.0, seed=args.seed),
    ).setup_llm()

    profile = str(getattr(args, "prompt_profile", "") or "").strip()
    if args.domain == "medical":
        ontology = REPO_ROOT / "data" / "ontologies" / "medical_case_schema_de_non_flat_v4.ttl"
        shacl = HERE / "resources" / "medical_shacl.ttl"
        write_medical_shapes(shacl)
        _assert_medical_shacl_oracle(shacl)
        if profile == "generic-noprompt":
            prompt = build_generic_noprompt_medical_prompt()
        elif profile == "with-prompt":
            prompt = build_with_prompt_medical_prompt()
        else:
            prompt = build_strict_noprompt_medical_prompt()
        (out_dir / "system_prompt.md").write_text(prompt, encoding="utf-8")
        parser = _make_parser(llm, ontology, shacl, prompt)
        extension_parsers = {}
    else:
        ontology = REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
        shacl = HERE / "resources" / "ontosynthesis_shacl.ttl"
        if profile == "generic-noprompt":
            prompt = build_generic_noprompt_system_prompt()
        elif profile == "with-prompt":
            prompt = build_with_prompt_system_prompt()
        elif profile == "with-prompt-qty":
            prompt = build_with_prompt_qty_system_prompt()
        elif profile == "with-prompt-hops":
            prompt = build_with_prompt_hops_system_prompt()
        elif profile == "full-prompt":
            prompt = build_full_prompt_system_prompt()
        else:
            prompt = build_strict_noprompt_system_prompt()
        (out_dir / "system_prompt.md").write_text(prompt, encoding="utf-8")
        parser = _make_parser(llm, ontology, shacl, prompt)
        extension_parsers = {}
        for domain in args.extensions or []:
            if domain == "ontospecies":
                ext_ont = REPO_ROOT / "data" / "ontologies" / "ontospecies-subgraph.ttl"
                ext_shacl = HERE / "resources" / "ontospecies_shacl.ttl"
                write_ontospecies_shapes(ext_shacl)
                ext_prompt = (
                    build_ontospecies_prompt()
                    if profile in ("generic-noprompt", "full-prompt")
                    else build_strict_noprompt_extension_prompt("ontospecies")
                )
            else:
                ext_ont = REPO_ROOT / "data" / "ontologies" / "ontomops-subgraph.ttl"
                ext_shacl = HERE / "resources" / "ontomops_shacl.ttl"
                write_ontomops_shapes(ext_shacl)
                ext_prompt = (
                    build_ontomops_prompt()
                    if profile in ("generic-noprompt", "full-prompt")
                    else build_strict_noprompt_extension_prompt("ontomops")
                )
            (out_dir / f"{domain}_system_prompt.md").write_text(ext_prompt, encoding="utf-8")
            extension_parsers[domain] = _make_parser(llm, ext_ont, ext_shacl, ext_prompt)

    papers = json.loads(Path(args.papers).read_text(encoding="utf-8"))["papers"]
    if args.hashes:
        papers = [item for item in papers if item["hash"] in set(args.hashes)]
    spec = resolve_protocol(
        str(getattr(args, "prompt_profile", "") or ""),
        allow_ox_overlays=True,
    )
    summary = ox_summary_fields(spec)
    summary["model"] = args.model
    summary["domain"] = args.domain
    summary["extension"] = list(args.extensions or [])
    summary["papers"] = []
    central_graph = None
    for paper in papers:
        paper_dir = out_dir / paper["hash"]
        paper_dir.mkdir(parents=True, exist_ok=True)
        if args.domain == "medical":
            entities = load_medical_entities(paper["hash"])
        else:
            entities = load_hint_entities(paper["hash"])
        paper_graph = None
        entity_records = []
        for entity in entities:
            identity_root = entity.path.parent.parent
            entity = attach_pipeline_identity(entity, identity_root)
            entity_dir = paper_dir / entity.slug
            entity_dir.mkdir(parents=True, exist_ok=True)
            main_record = {}
            if args.from_main_run is not None:
                main_ttl = _find_main_entity_ttl(
                    args.from_main_run,
                    paper["hash"],
                    entity.slug,
                    label=entity.label,
                )
                if main_ttl is None:
                    raise FileNotFoundError(
                        f"No inherited main TTL for {paper['hash']}/{entity.label}"
                    )
                graph = _bind_occurrence_ids(read_ttl(main_ttl, paper["hash"]), entity)
                write_ttl(
                    graph,
                    paper["hash"],
                    entity_dir / f"{entity.slug}_main.ttl",
                    scope=_entity_scope(entity),
                )
                main_graph = graph
                main_record = {"source": "inherited", "ttl": str(main_ttl)}
            else:
                ledger = entity.full_hints().text if hasattr(entity, "full_hints") else entity.text
                context = {
                    "doi": paper["doi"],
                    "hash": paper["hash"],
                    "entity_label": entity.label,
                    "entity_uri": getattr(entity, "uri", "") or "",
                    "source": "full_hints" if args.domain != "medical" else "iter2_hints",
                }
                extra_human = reuse_extra_human(entity, paper_graph, central_graph)
                human = compose_ox_human(ledger, context, extra_human)
                (entity_dir / "human.md").write_text(human, encoding="utf-8")
                budget = int(getattr(entity, "token_budget", 0) or 0)
                if budget <= 0:
                    raise FileNotFoundError(
                        "No pipeline KG token budget for "
                        f"{paper['hash']} / {entity.label!r}; "
                        "refusing unbounded OX. Need "
                        "responses/main_kg_building/*.trace.json "
                        "(or official iter2/3/4_kg_building traces)."
                    )
                graph, main_ok, main_msgs, main_usage = parser.parse(
                    ledger,
                    context,
                    paper["hash"],
                    extra_human=extra_human,
                    token_budget=budget,
                )
                if graph is not None:
                    graph = peel_product_step_types(graph)
                    graph = canonicalize_with_seed(graph, paper_graph, central_graph)
                    graph = _bind_occurrence_ids(graph, entity)
                    write_ttl(
                        graph,
                        paper["hash"],
                        entity_dir / f"{entity.slug}_main.ttl",
                        scope=_entity_scope(entity),
                    )
                main_graph = graph
                main_record = {
                    "source": "ox_main",
                    "conforms": main_ok,
                    "n_nodes": len(graph.nodes) if graph is not None else 0,
                    "shacl_messages": main_msgs[:8],
                    "pipeline_token_budget": budget,
                    "token_usage": {
                        "calls": main_usage.calls,
                        "total_tokens": main_usage.total_tokens,
                        "stop_reason": main_usage.stop_reason,
                    },
                }
            ext_records = []
            if graph is None:
                ext_records = [
                    {"extension": domain, "skipped": "no_main"}
                    for domain in extension_parsers
                ]
            else:
                for domain, ext_parser in extension_parsers.items():
                    graph, record = _parse_extension(
                        ext_parser,
                        entity,
                        paper,
                        graph,
                        entity_dir,
                        domain,
                        extra_reuse=reuse_extra_human(entity, paper_graph, central_graph),
                    )
                    ext_records.append(record)
            if graph is not None:
                graph = peel_product_step_types(graph)
                graph = _bind_occurrence_ids(graph, entity)
                write_ttl(
                    graph,
                    paper["hash"],
                    entity_dir / f"{entity.slug}.ttl",
                    scope=_entity_scope(entity),
                )
                publish_spliced_runtime(
                    dest_root=runtime_root,
                    paper_hash=paper["hash"],
                    entity_label=entity.label,
                    spliced=graph,
                    main=main_graph,
                    write_extensions=bool(extension_parsers),
                    entity_uri=getattr(entity, "uri", "") or "",
                    slug=entity.slug,
                    main_output_dir=(
                        "medical_output"
                        if args.domain == "medical"
                        else "ontosynthesis_output"
                    ),
                )
                paper_graph = (
                    attach_subgraph(paper_graph, graph, reuse="paper")
                    if paper_graph is not None
                    else graph
                )
            entity_records.append(
                {
                    "key": entity.key,
                    "label": entity.label,
                    "uri": getattr(entity, "uri", "") or "",
                    "n_nodes": len(graph.nodes) if graph is not None else 0,
                    "main": main_record,
                    "extensions": ext_records,
                }
            )
        central_graph = accumulate_central(central_graph, paper_graph)
        paper_ttl = None
        conforms = True
        if paper_graph is not None:
            paper_ttl = write_ttl(
                paper_graph, paper["hash"], paper_dir / f"{paper['hash']}.ttl"
            )
            write_ttl(
                paper_graph,
                paper["hash"],
                runtime_root / paper["hash"] / f"{paper['hash']}.ttl",
            )
            ok, messages, _ = validate_graph(paper_graph, ontology, shacl, paper["hash"])
            conforms = ok
            logger.info(
                "%s done: nodes=%s rels=%s shacl=%s",
                paper["hash"],
                len(paper_graph.nodes),
                len(paper_graph.relationships),
                ok,
            )
        summary["papers"].append(
            {
                "hash": paper["hash"],
                "doi": paper["doi"],
                "conforms": conforms,
                "ttl": str(paper_ttl) if paper_ttl else None,
                "entities": entity_records,
            }
        )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary
