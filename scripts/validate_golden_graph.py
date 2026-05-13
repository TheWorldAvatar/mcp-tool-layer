#!/usr/bin/env python3
"""Validate graph-level golden cases for generated ontology pipeline outputs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from rdflib import Graph, Namespace, RDF, RDFS


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1] if text else ""


def _first_label(graph: Graph, node: Any) -> str:
    for label in graph.objects(node, RDFS.label):
        return str(label)
    return ""


def _load_graph(paths: list[str]) -> Graph:
    graph = Graph()
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise FileNotFoundError(f"TTL path not found: {path}")
        graph.parse(str(path), format="turtle")
    return graph


def _step_type_counts(graph: Graph, steps: list[Any]) -> Counter:
    counts: Counter = Counter()
    for step in steps:
        concrete = [
            _local_name(t)
            for t in graph.objects(step, RDF.type)
            if str(t).startswith(str(ONTOSYN)) and _local_name(t) != "SynthesisStep"
        ]
        if concrete:
            counts.update(concrete)
        else:
            counts.update(["SynthesisStep"])
    return counts


def _quantity_matches(graph: Graph, step: Any, predicate_local: str, value: float, unit_local: str) -> bool:
    predicate = getattr(ONTOSYN, predicate_local)
    for quantity in graph.objects(step, predicate):
        raw_value = next(graph.objects(quantity, OM2.hasNumericalValue), None)
        raw_unit = next(graph.objects(quantity, OM2.hasUnit), None)
        try:
            numeric_ok = abs(float(raw_value) - float(value)) < 1e-6
        except Exception:
            numeric_ok = False
        if numeric_ok and _local_name(raw_unit) == unit_local:
            return True
    return False


def validate_case(config: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    graph = _load_graph(list(config.get("ttl_paths") or []))
    failures: list[str] = []
    summary: dict[str, Any] = {"triples": len(graph), "syntheses": []}

    syntheses = list(graph.subjects(RDF.type, ONTOSYN.ChemicalSynthesis))
    synth_by_label = {_first_label(graph, node): node for node in syntheses}
    all_steps = {s for s in graph.subjects(RDF.type, ONTOSYN.SynthesisStep)}
    linked_steps = {o for s in syntheses for o in graph.objects(s, ONTOSYN.hasSynthesisStep)}
    orphan_steps = all_steps - linked_steps

    for expected in config.get("expected_syntheses") or []:
        label = str(expected.get("label") or "").strip()
        synth = synth_by_label.get(label)
        if synth is None:
            failures.append(f"Missing synthesis label: {label}")
            continue
        steps = list(graph.objects(synth, ONTOSYN.hasSynthesisStep))
        outputs = list(graph.objects(synth, ONTOSYN.hasChemicalOutput))
        counts = _step_type_counts(graph, steps)
        summary["syntheses"].append(
            {
                "label": label,
                "steps": len(steps),
                "outputs": len(outputs),
                "step_types": dict(counts),
            }
        )
        if len(steps) < int(expected.get("min_steps") or 0):
            failures.append(f"{label}: expected at least {expected.get('min_steps')} steps, found {len(steps)}")
        if not outputs:
            failures.append(f"{label}: missing hasChemicalOutput link")
        for step_type, min_count in (expected.get("required_step_types") or {}).items():
            if counts.get(step_type, 0) < int(min_count):
                failures.append(f"{label}: expected {min_count} {step_type} step(s), found {counts.get(step_type, 0)}")
        for quantity_req in expected.get("required_quantities") or []:
            step_type = str(quantity_req.get("step_type") or "").strip()
            matching_steps = [
                step
                for step in steps
                if (step, RDF.type, getattr(ONTOSYN, step_type)) in graph
            ]
            if not any(
                _quantity_matches(
                    graph,
                    step,
                    str(quantity_req.get("predicate") or ""),
                    float(quantity_req.get("value")),
                    str(quantity_req.get("unit_local") or ""),
                )
                for step in matching_steps
            ):
                failures.append(
                    f"{label}: missing quantity {quantity_req.get('predicate')}="
                    f"{quantity_req.get('value')} {quantity_req.get('unit_local')} on {step_type}"
                )

    max_orphans = max(int(x.get("max_orphan_steps") or 0) for x in (config.get("expected_syntheses") or [{}]))
    if len(orphan_steps) > max_orphans:
        failures.append(f"Expected at most {max_orphans} orphan step(s), found {len(orphan_steps)}")
    summary["orphan_steps"] = len(orphan_steps)
    return not failures, failures, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated graph against a golden case config.")
    parser.add_argument("--config", required=True, help="Path to golden case JSON config")
    parser.add_argument("--summary-output", help="Optional path to write JSON validation summary")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    ok, failures, summary = validate_case(config)
    report = {"ok": ok, "failures": failures, "summary": summary}
    if args.summary_output:
        out = Path(args.summary_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
