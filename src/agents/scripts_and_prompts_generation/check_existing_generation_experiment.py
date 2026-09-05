from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _checks_script,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    attach_reuse_policy,
    existing_entity_check_contracts,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _literal_manifest(source: str) -> list[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return value
    raise ValueError("generated checks script must contain a literal __all__ list")


def _behavior_probe(source: str, check_contract: dict[str, Any]) -> dict[str, Any]:
    graph = Graph()
    class_iri = URIRef(str(check_contract["class_iri"]))
    candidate = URIRef("https://example.test/candidate")
    related = URIRef("https://example.test/related")
    owner = URIRef("https://example.test/owner")
    detail_predicate = URIRef("https://example.test/detail")
    relation_predicate = URIRef("https://example.test/relation")
    incoming_predicate = URIRef("https://example.test/incoming")
    graph.add((candidate, RDF.type, class_iri))
    graph.add((candidate, RDFS.label, Literal("Candidate label")))
    graph.add(
        (
            candidate,
            detail_predicate,
            Literal("42", datatype=URIRef("http://www.w3.org/2001/XMLSchema#integer")),
        )
    )
    graph.add((candidate, relation_predicate, related))
    graph.add((related, RDFS.label, Literal("Related label")))
    graph.add((owner, incoming_predicate, candidate))
    graph.add((owner, RDFS.label, Literal("Owner label")))

    executable = re.sub(
        r"from \._fixed_rdf_runtime import \([\s\S]*?\)\n"
        r"from \._reuse_pair_judge import judge_reuse_pairs",
        (
            "def load_central_reuse_memory(_ontology_name):\n"
            "    return _PROBE_GRAPH, _PROBE_PROVENANCE\n"
            "def load_document_reuse_memory(_ontology_name):\n"
            "    return _PROBE_GRAPH, _PROBE_PROVENANCE\n"
            "def retained_graph():\n"
            "    return _PROBE_GRAPH\n"
            "def current_memory_scope():\n"
            "    return {'doi': 'probe-document', 'top_level_entity_name': 'probe-top'}\n"
            "def register_central_reuse_authorization(**_kwargs):\n"
            "    return 'probe-token'\n"
            "def judge_reuse_pairs(requests):\n"
            "    return [{'reuse_authorized': True, 'pair_id': item['pair_id']} "
            "for item in requests]"
        ),
        source,
        count=1,
    )
    namespace: dict[str, Any] = {
        "__name__": "_check_existing_probe",
        "_PROBE_GRAPH": graph,
        "_PROBE_PROVENANCE": {
            str(candidate): [
                {
                    "doi": "probe-document",
                    "top_level_entity_name": "probe-top",
                }
            ]
        },
    }
    exec(compile(executable, "<generated-checks>", "exec"), namespace)
    raw = namespace[str(check_contract["public_tool"])](
        json.dumps({"label": "Candidate label"})
    )
    payload = json.loads(raw)
    instances = payload.get("instances") or []
    if len(instances) != 1:
        raise ValueError("behavior probe expected one reusable candidate")
    detail = instances[0]
    required = {
        "iri",
        "labels",
        "types",
        "datatype_values",
        "outgoing_relations",
        "incoming_relations",
        "central_provenance",
    }
    missing = sorted(required - set(detail))
    if missing:
        raise ValueError("behavior probe missing detail fields: " + ", ".join(missing))
    if detail["iri"] != str(candidate) or detail["labels"] != ["Candidate label"]:
        raise ValueError("behavior probe returned incorrect candidate identity")
    return payload


def run_experiment(
    *,
    ontology_name: str,
    meta_task_config_path: Path,
    reuse_policy_path: Path,
    output_dir: Path,
    trials: int = 3,
) -> dict[str, Any]:
    if trials < 1:
        raise ValueError("trials must be at least 1")
    context = build_agentic_generation_context(
        ontology_name=ontology_name,
        meta_task_config_path=meta_task_config_path,
        output_root=output_dir / "_context",
        write_files=False,
    )
    policy = attach_reuse_policy(context.contract, reuse_policy_path)
    checks = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
        legacy_all_classes_when_absent=False,
    )
    if not checks:
        raise ValueError(
            "reuse policy authorizes no checks on the active T-Box surface"
        )
    expected_manifest = [
        "check_ordered_members",
        *(str(item["public_tool"]) for item in checks),
    ]
    expected_scoped_tools = {
        f"check_existing_{item['class_local']}"
        for item in policy["classes"]
        if item.get("reusable") is False
    }

    trial_results: list[dict[str, Any]] = []
    for trial in range(1, trials + 1):
        source = _checks_script(context)
        ast.parse(source)
        manifest = _literal_manifest(source)
        scoped_tools = sorted(set(manifest) & expected_scoped_tools)
        if manifest != expected_manifest:
            raise ValueError(
                f"trial {trial} manifest mismatch: {manifest!r} != {expected_manifest!r}"
            )
        probe = _behavior_probe(source, checks[0])
        script_path = (
            output_dir / f"trial_{trial}" / f"{ontology_name}_creation_checks.py"
        )
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(source, encoding="utf-8")
        result = {
            "trial": trial,
            "ok": True,
            "script_path": str(script_path),
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "manifest": manifest,
            "authorized_check_count": len(checks),
            "scoped_reference_checks": scoped_tools,
            "behavior_probe": probe,
        }
        _write_json(output_dir / f"trial_{trial}.json", result)
        trial_results.append(result)

    hashes = {item["sha256"] for item in trial_results}
    summary = {
        "schema_version": "check-existing-generation-experiment.v1",
        "ontology": ontology_name,
        "requested_trials": trials,
        "valid_trials": sum(bool(item["ok"]) for item in trial_results),
        "all_trials_valid": all(bool(item["ok"]) for item in trial_results),
        "deterministic_across_trials": len(hashes) == 1,
        "authorized_checks": checks,
        "script_hashes": sorted(hashes),
        "reuse_policy_sha256": policy["source_sha256"],
        "trials": trial_results,
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and review isolated reusable-class check_existing tools."
    )
    parser.add_argument("--ontology", default="ontosynthesis")
    parser.add_argument("--meta-task-config", type=Path, required=True)
    parser.add_argument("--reuse-policy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    summary = run_experiment(
        ontology_name=args.ontology,
        meta_task_config_path=args.meta_task_config,
        reuse_policy_path=args.reuse_policy,
        output_dir=args.output_dir,
        trials=args.trials,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_trials_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
