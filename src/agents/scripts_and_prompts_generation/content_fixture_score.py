"""Content-level scoring for mock document extraction and KG prompt loops."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import abox_graph


def _normalise_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return " ".join(str(value).strip().casefold().split())


def _normalise_property_values(prop: str, raw_value: Any) -> list[str]:
    """Normalize a hint property into independently scored scalar values."""
    values = raw_value if isinstance(raw_value, list) else [raw_value]
    normalized: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = json.dumps(value, sort_keys=True, ensure_ascii=False)
        parts = (
            str(value or "").split(";")
            if prop == "hasAlternativeNames"
            else [value]
        )
        for part in parts:
            scalar = _normalise_scalar(part)
            if scalar:
                normalized.append(scalar)
    return normalized


def flatten_hint_facts(hints: dict[str, Any]) -> Counter[tuple[str, str, str, str]]:
    """Flatten class-section hints into a duplicate-aware content fact multiset."""
    facts: Counter[tuple[str, str, str, str]] = Counter()
    for class_local, raw_items in sorted((hints or {}).items()):
        if str(class_local).startswith("__"):
            continue
        items = raw_items if isinstance(raw_items, list) else [raw_items]
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                facts[(str(class_local), str(index), "value", _normalise_scalar(item))] += 1
                continue
            identity = _normalise_scalar(item.get("label") or item.get("name") or index)
            for prop, raw_value in sorted(item.items()):
                for value in _normalise_property_values(str(prop), raw_value):
                    facts[
                        (
                            str(class_local),
                            identity,
                            str(prop),
                            value,
                        )
                    ] += 1
    return facts


def _metric(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def score_hint_content(
    gold_hints: dict[str, Any],
    predicted_hints: dict[str, Any],
) -> dict[str, Any]:
    """Score extracted hints against source-grounded fixture hints."""
    gold = flatten_hint_facts(gold_hints)
    predicted_all = flatten_hint_facts(predicted_hints)
    # Content fixtures define the facts/properties being evaluated. Tool-backed
    # enrichment (for example PubChem aliases) and extension-only classes remain
    # valid output but must not become false positives when the fixture does not
    # declare those fields as scored.
    scored_slots = {(fact[0], fact[1], fact[2]) for fact in gold}
    gold_aliases = {
        fact
        for fact in gold
        if fact[2] == "hasAlternativeNames"
    }
    predicted = Counter(
        {
            fact: count
            for fact, count in predicted_all.items()
            if (fact[0], fact[1], fact[2]) in scored_slots
            and (fact[2] != "hasAlternativeNames" or fact in gold_aliases)
        }
    )
    forbidden_classes = {
        str(value).strip()
        for value in (
            ((gold_hints or {}).get("__absent_classes__") or [])
            if isinstance((gold_hints or {}).get("__absent_classes__"), list)
            else []
        )
        if str(value).strip()
    }
    for fact, count in predicted_all.items():
        if fact[0] in forbidden_classes:
            predicted[fact] += count
    matched = gold & predicted
    missing = gold - predicted
    unexpected = predicted - gold
    overall = _metric(sum(matched.values()), sum(unexpected.values()), sum(missing.values()))

    class_names = sorted({fact[0] for fact in gold} | {fact[0] for fact in predicted})
    property_names = sorted({fact[2] for fact in gold} | {fact[2] for fact in predicted})

    def grouped(key_index: int, names: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in names:
            gold_count = Counter({fact: count for fact, count in gold.items() if fact[key_index] == name})
            pred_count = Counter(
                {fact: count for fact, count in predicted.items() if fact[key_index] == name}
            )
            common = gold_count & pred_count
            result[name] = _metric(
                sum(common.values()),
                sum((pred_count - gold_count).values()),
                sum((gold_count - pred_count).values()),
            )
        return result

    def serialise(counter: Counter[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "class": fact[0],
                "entity": fact[1],
                "property": fact[2],
                "value": fact[3],
                "count": count,
            }
            for fact, count in sorted(counter.items())
        ]

    return {
        "ok": overall["f1"] == 1.0,
        "overall": overall,
        "scoring_scope": {
            "classes": sorted({fact[0] for fact in gold}),
            "class_properties": [
                {"class": class_local, "property": property_local}
                for class_local, property_local in sorted(
                    {(fact[0], fact[2]) for fact in gold}
                )
            ],
            "entity_properties": [
                {
                    "class": class_local,
                    "entity": entity,
                    "property": property_local,
                }
                for class_local, entity, property_local in sorted(scored_slots)
            ],
            "policy": (
                "Only gold-declared entity/property slots are scored; enrichment is retained. "
                "Alternative names are semicolon-aware required subsets."
            ),
        },
        "per_class": grouped(0, class_names),
        "per_property": grouped(2, property_names),
        "missing": serialise(missing),
        "unexpected": serialise(unexpected),
    }


def _graph_fact_counter(path: Path) -> Counter[tuple[str, str, str]]:
    """Create an IRI-insensitive A-Box fact multiset for diagnostics."""
    loaded = Graph()
    loaded.parse(str(path), format="turtle")
    graph = abox_graph(loaded)

    def node_key(node: Any) -> str:
        if isinstance(node, Literal):
            return _normalise_scalar(node)
        if isinstance(node, (URIRef, BNode)):
            labels = sorted(_normalise_scalar(value) for value in graph.objects(node, RDFS.label))
            types = sorted(str(value).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for value in graph.objects(node, RDF.type))
            return f"{'|'.join(types)}:{'|'.join(labels)}"
        return _normalise_scalar(node)

    facts: Counter[tuple[str, str, str]] = Counter()
    for subject, predicate, obj in graph:
        facts[
            (
                node_key(subject),
                str(predicate).rsplit("/", 1)[-1].rsplit("#", 1)[-1],
                node_key(obj),
            )
        ] += 1
    return facts


def score_graph_content(gold_abox: Path, predicted_abox: Path) -> dict[str, Any]:
    """Compare oracle and predicted graphs without requiring identical minted IRIs."""
    gold = _graph_fact_counter(gold_abox)
    predicted = _graph_fact_counter(predicted_abox)
    matched = gold & predicted
    missing = gold - predicted
    unexpected = predicted - gold

    def serialise(counter: Counter[tuple[str, str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "subject": fact[0],
                "predicate": fact[1],
                "object": fact[2],
                "count": count,
            }
            for fact, count in sorted(counter.items())
        ]

    return {
        "ok": gold == predicted,
        "overall": _metric(
            sum(matched.values()),
            sum(unexpected.values()),
            sum(missing.values()),
        ),
        "missing": serialise(missing),
        "unexpected": serialise(unexpected),
    }


def load_predicted_hints(case_dir: Path) -> dict[str, Any]:
    """Merge the final per-iteration hint files emitted by the runtime pipeline."""
    run_dir = case_dir / "mcp_run"
    candidates: dict[int, list[Path]] = {}
    for path in run_dir.glob("iter*_hints_*.txt"):
        iteration_text = path.name.split("_", 1)[0].removeprefix("iter")
        try:
            iteration = int(iteration_text)
        except ValueError:
            continue
        candidates.setdefault(iteration, []).append(path)

    merged: dict[str, Any] = {}
    for iteration in sorted(candidates):
        for path in sorted(candidates[iteration]):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            for class_local, value in payload.items():
                if class_local not in merged:
                    merged[class_local] = value
                    continue
                old = merged[class_local]
                old_items = old if isinstance(old, list) else [old]
                new_items = value if isinstance(value, list) else [value]
                by_label: dict[str, dict[str, Any]] = {}
                for item in [*old_items, *new_items]:
                    if not isinstance(item, dict):
                        continue
                    key = _normalise_scalar(item.get("label"))
                    by_label[key] = {**by_label.get(key, {}), **item}
                merged[class_local] = list(by_label.values()) or new_items
    return merged
