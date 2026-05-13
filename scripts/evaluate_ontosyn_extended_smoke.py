from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from rdflib import Graph, Namespace, RDF, RDFS, URIRef


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
ONTOSPECIES = Namespace(
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
)
OWL = Namespace("http://www.w3.org/2002/07/owl#")

CONCRETE_STEP_CLASSES = {
    ONTOSYN.Add,
    ONTOSYN.Stir,
    ONTOSYN.HeatChill,
    ONTOSYN.Evaporate,
    ONTOSYN.Sonicate,
    ONTOSYN.Transfer,
    ONTOSYN.Separate,
    ONTOSYN.Filter,
    ONTOSYN.Dry,
    ONTOSYN.Crystallize,
    ONTOSYN.SeparationType,
}


def _load_ttl(path: Path) -> Graph:
    graph = Graph()
    if path.is_file() and path.stat().st_size > 0:
        graph.parse(path, format="turtle")
    return graph


def _first_existing(patterns: list[Path]) -> Path | None:
    for path in patterns:
        if path.is_file():
            return path
    return None


def _score(checks: dict[str, bool]) -> float:
    return round(sum(1 for ok in checks.values() if ok) / max(len(checks), 1), 3)


def _axis(name: str, checks: dict[str, bool], details: dict[str, Any]) -> dict[str, Any]:
    score = _score(checks)
    return {
        "name": name,
        "score": score,
        "ok": score == 1.0,
        "checks": checks,
        "details": details,
    }


def _literal_texts(graph: Graph, subject: URIRef, predicate: URIRef) -> list[str]:
    return [str(value) for value in graph.objects(subject, predicate)]


def _integer_orders(graph: Graph, steps: list[URIRef]) -> list[int]:
    orders: list[int] = []
    for step in steps:
        for value in graph.objects(step, ONTOSYN.hasOrder):
            try:
                orders.append(int(value))
            except (TypeError, ValueError):
                pass
    return sorted(orders)


def _duplicate_label_count(graph: Graph) -> int:
    groups: dict[tuple[str, str], set[URIRef]] = defaultdict(set)
    for subject, _, class_iri in graph.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef):
            continue
        for label in graph.objects(subject, RDFS.label):
            label_text = str(label or "").strip()
            if label_text:
                groups[(str(class_iri), label_text)].add(subject)
    return sum(1 for subjects in groups.values() if len(subjects) > 1)


def _unreachable_count(graph: Graph, root: URIRef | None) -> int:
    if root is None:
        return 0
    typed_nodes = {
        subject
        for subject, _, _ in graph.triples((None, RDF.type, None))
        if isinstance(subject, URIRef)
    }
    reachable: set[URIRef] = set()
    queue: deque[URIRef] = deque([root])
    while queue:
        subject = queue.popleft()
        if subject in reachable:
            continue
        reachable.add(subject)
        for _, predicate, obj in graph.triples((subject, None, None)):
            if predicate in {RDF.type, RDFS.label}:
                continue
            if isinstance(obj, URIRef):
                queue.append(obj)
    return len(typed_nodes - reachable)


def _evaluate_steps(graph: Graph) -> dict[str, Any]:
    syntheses = list(graph.subjects(RDF.type, ONTOSYN.ChemicalSynthesis))
    top = syntheses[0] if len(syntheses) == 1 else None
    steps = list(graph.objects(top, ONTOSYN.hasSynthesisStep)) if top else []
    orders = _integer_orders(graph, steps)
    generic_steps = [
        step
        for step in graph.subjects(RDF.type, ONTOSYN.SynthesisStep)
        if not any((step, RDF.type, cls) in graph for cls in CONCRETE_STEP_CLASSES)
    ]
    add_steps = [step for step in steps if (step, RDF.type, ONTOSYN.Add) in graph]
    checks = {
        "single_synthesis_root": len(syntheses) == 1,
        "has_steps": len(steps) > 0,
        "all_linked_steps_are_concrete": bool(steps)
        and all(any((step, RDF.type, cls) in graph for cls in CONCRETE_STEP_CLASSES) for step in steps),
        "no_generic_placeholder_steps": len(generic_steps) == 0,
        "orders_are_contiguous": orders == list(range(1, len(orders) + 1)),
        "add_steps_link_inputs": bool(add_steps)
        and all((step, ONTOSYN.hasAddedChemicalInput, None) in graph for step in add_steps),
    }
    return _axis(
        "Steps",
        checks,
        {
            "synthesis_count": len(syntheses),
            "step_count": len(steps),
            "orders": orders,
            "generic_step_count": len(generic_steps),
            "duplicate_label_count": _duplicate_label_count(graph),
            "unreachable_typed_node_count": _unreachable_count(graph, top),
        },
    )


def _evaluate_chemicals(graph: Graph) -> dict[str, Any]:
    syntheses = list(graph.subjects(RDF.type, ONTOSYN.ChemicalSynthesis))
    top = syntheses[0] if len(syntheses) == 1 else None
    inputs = list(graph.objects(top, ONTOSYN.hasChemicalInput)) if top else []
    outputs = list(graph.objects(top, ONTOSYN.hasChemicalOutput)) if top else []
    yields = list(graph.objects(top, ONTOSYN.hasYield)) if top else []
    input_amount_count = sum(1 for item in inputs if (item, ONTOSYN.hasAmount, None) in graph)
    checks = {
        "has_three_or_more_inputs": len(inputs) >= 3,
        "all_inputs_have_amounts": bool(inputs) and input_amount_count == len(inputs),
        "has_single_output": len(outputs) == 1,
        "output_label_matches_target": any(
            "UMC-1" in _literal_texts(graph, output, RDFS.label) for output in outputs
        ),
        "yield_is_linked": len(yields) == 1,
        "yield_value_present": any(
            any("45" in label for label in _literal_texts(graph, yield_node, RDFS.label))
            for yield_node in yields
        ),
    }
    return _axis(
        "Chemicals",
        checks,
        {
            "input_count": len(inputs),
            "input_amount_count": input_amount_count,
            "output_count": len(outputs),
            "yield_count": len(yields),
        },
    )


def _evaluate_cbu(graph: Graph) -> dict[str, Any]:
    mops = list(graph.subjects(RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
    mop = mops[0] if len(mops) == 1 else None
    cbus = list(graph.objects(mop, ONTOMOPS.hasChemicalBuildingUnit)) if mop else []
    same_as_count = sum(1 for cbu in cbus if (cbu, OWL.sameAs, None) in graph)
    checks = {
        "has_single_mop": len(mops) == 1,
        "mop_formula_present": bool(mop and (mop, ONTOMOPS.hasMOPFormula, None) in graph),
        "ccdc_number_present": bool(mop and (mop, ONTOMOPS.hasCCDCNumber, None) in graph),
        "has_two_or_more_cbus": len(cbus) >= 2,
        "cbu_labels_present": bool(cbus)
        and all((cbu, RDFS.label, None) in graph for cbu in cbus),
        "cbu_source_species_link_present": same_as_count > 0,
    }
    return _axis(
        "CBU",
        checks,
        {
            "mop_count": len(mops),
            "cbu_count": len(cbus),
            "cbu_same_as_count": same_as_count,
        },
    )


def _evaluate_characterisation(graph: Graph) -> dict[str, Any]:
    species = list(graph.subjects(RDF.type, ONTOSPECIES.Species))
    target = species[0] if len(species) == 1 else None
    ir_nodes = list(graph.objects(target, ONTOSPECIES.hasInfraredSpectroscopyData)) if target else []
    hnmr_nodes = list(graph.objects(target, ONTOSPECIES.hasHNMRData)) if target else []
    ccdc_nodes = list(graph.objects(target, ONTOSPECIES.hasCCDCNumber)) if target else []
    formula_nodes = list(graph.objects(target, ONTOSPECIES.hasChemicalFormula)) if target else []
    checks = {
        "has_single_species": len(species) == 1,
        "has_ccdc_number": bool(ccdc_nodes),
        "has_formula": bool(formula_nodes),
        "has_ir_data": bool(ir_nodes),
        "ir_bands_present": any((node, ONTOSPECIES.hasBands, None) in graph for node in ir_nodes),
        "has_hnmr_data": bool(hnmr_nodes),
        "hnmr_shifts_present": any((node, ONTOSPECIES.hasShifts, None) in graph for node in hnmr_nodes),
        "hnmr_solvent_present": any((node, ONTOSPECIES.usesSolvent, None) in graph for node in hnmr_nodes),
    }
    return _axis(
        "Characterisation",
        checks,
        {
            "species_count": len(species),
            "ir_data_count": len(ir_nodes),
            "hnmr_data_count": len(hnmr_nodes),
            "ccdc_node_count": len(ccdc_nodes),
            "formula_node_count": len(formula_nodes),
        },
    )


def evaluate_case(case_dir: Path) -> dict[str, Any]:
    ontosyn_path = case_dir / "ontosynthesis_output" / "UMC-1.ttl"
    ontomops_path = _first_existing(
        sorted((case_dir / "ontomops_output").glob("*.ttl"))
    )
    ontospecies_path = case_dir / "ontospecies_output" / "UMC-1.ttl"
    if ontomops_path is None:
        ontomops_path = case_dir / "missing_ontomops.ttl"

    ontosyn_graph = _load_ttl(ontosyn_path)
    ontomops_graph = _load_ttl(ontomops_path)
    ontospecies_graph = _load_ttl(ontospecies_path)

    axes = {
        "Steps": _evaluate_steps(ontosyn_graph),
        "Chemicals": _evaluate_chemicals(ontosyn_graph),
        "CBU": _evaluate_cbu(ontomops_graph),
        "Characterisation": _evaluate_characterisation(ontospecies_graph),
    }
    return {
        "case_dir": str(case_dir),
        "ttl_files": {
            "ontosynthesis": str(ontosyn_path),
            "ontomops": str(ontomops_path),
            "ontospecies": str(ontospecies_path),
        },
        "overall_score": round(
            sum(axis["score"] for axis in axes.values()) / max(len(axes), 1), 3
        ),
        "axes": axes,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# OntoSynthesis Extended Smoke Metrics",
        "",
        f"- Case: `{report['case_dir']}`",
        f"- Overall score: `{report['overall_score']}`",
        "",
    ]
    for name, axis in report["axes"].items():
        lines.extend(
            [
                f"## {name}",
                f"- Score: `{axis['score']}`",
                f"- OK: `{axis['ok']}`",
                "- Failed checks: "
                + (
                    ", ".join(k for k, ok in axis["checks"].items() if not ok)
                    or "none"
                ),
                f"- Details: `{json.dumps(axis['details'], ensure_ascii=False)}`",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate an extended OntoSynthesis smoke case across four axes."
    )
    parser.add_argument(
        "case_dir",
        nargs="?",
        default="data_ontosyn_generated_extended_smoke/extsyn1",
    )
    args = parser.parse_args()

    case_dir = Path(args.case_dir)
    report = evaluate_case(case_dir)
    out_dir = case_dir / "evaluation_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ontosyn_extended_smoke_metrics.json"
    md_path = out_dir / "ontosyn_extended_smoke_metrics.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps({"ok": True, "json": str(json_path), "markdown": str(md_path), "overall_score": report["overall_score"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
