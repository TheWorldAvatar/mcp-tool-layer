"""Isolated deterministic experiment for iteration-scoped T-Box properties."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _iteration_number(iteration: dict[str, Any]) -> str:
    return str(iteration.get("iteration_number") or "").strip()


def _responsibility_locals(
    iteration: dict[str, Any],
    key: str,
) -> set[str]:
    responsibilities = iteration.get("responsibilities") or {}
    return {
        str(value).strip()
        for value in responsibilities.get(key) or []
        if str(value).strip()
    }


def canonical_contract_json(contract: dict[str, Any]) -> str:
    """Serialize a derived contract independently of input dictionary order."""
    return json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def contract_sha256(contract: dict[str, Any]) -> str:
    """Return the canonical digest used by the stability experiment."""
    return hashlib.sha256(canonical_contract_json(contract).encode("utf-8")).hexdigest()


def derive_iteration_property_contract(
    *,
    parsed: dict[str, Any],
    compiled_plan: dict[str, Any],
    iteration_number: int | float | str,
) -> dict[str, Any]:
    """Derive one iteration's complete property surface from the active T-Box.

    Explicit planner assignments define cross-iteration ownership and bridge
    properties. Properties attached by the parser to an owned class complete
    the local datatype/object-property surface. An explicit assignment to
    another iteration wins over automatic class-based inclusion.
    """
    classes = parsed.get("classes") or {}
    properties = parsed.get("properties") or {}
    iterations = compiled_plan.get("iterations") or []
    target_number = str(iteration_number)
    target = next(
        (
            iteration
            for iteration in iterations
            if isinstance(iteration, dict)
            and _iteration_number(iteration) == target_number
        ),
        None,
    )
    if target is None:
        raise ValueError(f"Iteration {target_number} is absent from the compiled plan")

    explicit_owners: dict[str, set[str]] = {}
    for iteration in iterations:
        if not isinstance(iteration, dict):
            continue
        owner = _iteration_number(iteration)
        for local in _responsibility_locals(iteration, "object_properties"):
            explicit_owners.setdefault(local, set()).add(owner)

    owned_classes = _responsibility_locals(target, "classes")
    explicit_properties = _responsibility_locals(target, "object_properties")
    unknown_classes = sorted(owned_classes - set(classes))
    unknown_explicit_properties = sorted(explicit_properties - set(properties))

    class_sources: dict[str, set[str]] = {}
    class_declared_kind: dict[str, set[str]] = {}
    for class_local in sorted(owned_classes):
        class_spec = classes.get(class_local) or {}
        for key, kind in (
            ("datatype_properties", "datatype"),
            ("object_properties", "object"),
        ):
            for property_local in (class_spec.get(key) or {}):
                local = str(property_local).strip()
                if not local:
                    continue
                class_sources.setdefault(local, set()).add(class_local)
                class_declared_kind.setdefault(local, set()).add(kind)

    candidates = set(class_sources) | explicit_properties
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    kind_mismatches: list[dict[str, Any]] = []
    unresolved = set(unknown_explicit_properties)

    for local in sorted(candidates):
        foreign_owners = sorted(explicit_owners.get(local, set()) - {target_number})
        explicitly_owned_here = local in explicit_properties
        if foreign_owners and not explicitly_owned_here:
            excluded.append(
                {
                    "local": local,
                    "reason": "explicitly_owned_by_other_iteration",
                    "owners": foreign_owners,
                    "class_sources": sorted(class_sources.get(local, set())),
                }
            )
            continue

        spec = properties.get(local)
        if not isinstance(spec, dict):
            unresolved.add(local)
            continue
        parsed_kind = str(spec.get("kind") or "unknown")
        declared_kinds = sorted(class_declared_kind.get(local, set()))
        if declared_kinds and (
            parsed_kind not in declared_kinds or len(declared_kinds) != 1
        ):
            kind_mismatches.append(
                {
                    "local": local,
                    "property_kind": parsed_kind,
                    "class_declared_kinds": declared_kinds,
                }
            )
        included.append(
            {
                "local": local,
                "iri": str(spec.get("iri") or ""),
                "kind": parsed_kind,
                "domains": sorted(
                    {
                        _local_name(domain)
                        for domain in spec.get("domains") or []
                        if _local_name(domain)
                    }
                ),
                "range": _local_name(spec.get("range")),
                "comment": str(spec.get("comment") or ""),
                "sources": {
                    "explicit_assignment": explicitly_owned_here,
                    "owned_classes": sorted(class_sources.get(local, set())),
                    "bridge": explicitly_owned_here
                    and not class_sources.get(local),
                },
            }
        )

    return {
        "schema_version": "tbox-property-contract-experiment.v1",
        "iteration": target_number,
        "owned_classes": sorted(owned_classes),
        "excluded_classes": [],
        "excluded_class_rules": [],
        "properties": included,
        "excluded_properties": excluded,
        "diagnostics": {
            "unknown_classes": unknown_classes,
            "unresolved_properties": sorted(unresolved),
            "kind_mismatches": kind_mismatches,
            "multiple_explicit_owners": [
                {"local": local, "owners": sorted(owners)}
                for local, owners in sorted(explicit_owners.items())
                if len(owners) > 1
            ],
        },
    }


def render_property_contract_block(contract: dict[str, Any]) -> str:
    """Render a compact deterministic block suitable for experimental injection."""
    lines = [
        "BEGIN GENERATED TBOX PROPERTY CONTRACT",
        "This block is authoritative for the available property surface.",
        (
            "Extract each property whenever the source or an explicitly declared "
            "procedure inheritance makes it applicable; otherwise omit it."
        ),
        "Never invent a value merely because a property is listed.",
    ]
    excluded_classes = contract.get("excluded_classes") or []
    if excluded_classes:
        lines.append(
            "Excluded classes and their exclusive properties: "
            + ", ".join(str(value) for value in excluded_classes)
        )
    for item in contract.get("properties") or []:
        if not isinstance(item, dict):
            continue
        domains = ",".join(str(value) for value in item.get("domains") or []) or "-"
        value_range = str(item.get("range") or "-")
        lines.append(
            f"- {item.get('local')} | {item.get('kind')} | "
            f"domain={domains} | range={value_range}"
        )
    lines.append("END GENERATED TBOX PROPERTY CONTRACT")
    return "\n".join(lines)


def reverse_mapping_order(value: Any) -> Any:
    """Recursively reverse mapping order for determinism stress tests."""
    if isinstance(value, dict):
        return {
            key: reverse_mapping_order(item)
            for key, item in reversed(list(value.items()))
        }
    if isinstance(value, list):
        return [reverse_mapping_order(item) for item in value]
    return deepcopy(value)


def main() -> int:
    """Run the isolated contract-injection extraction experiment."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--baseline-case", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--iteration", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--parallelism", type=int, default=5)
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--judge-model", default="gpt-5")
    parser.add_argument("--generate-prompts", action="store_true")
    parser.add_argument("--generation-model", default="gpt-5")
    parser.add_argument(
        "--prompt-target",
        action="append",
        choices=["PRE_EXTRACTION_ITER_3.md", "EXTRACTION_ITER_3.md"],
    )
    parser.add_argument("--runtime-contract-injection", action="store_true")
    args = parser.parse_args()

    from src.agents.scripts_and_prompts_generation.domain_generation_resume import (
        load_domain_generation_checkpoint,
    )
    from src.agents.scripts_and_prompts_generation.prompt_enhancement_pipeline import (
        _judge_target_extraction,
        run_targeted_extraction_trials,
    )

    artifact_root = Path(args.artifact_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if args.generate_prompts:
        from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
            _generate_artifact_wave,
        )

        generated_root = output_dir / "generated_package"
        if generated_root.exists():
            shutil.rmtree(generated_root)
        generated_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(artifact_root, generated_root)
        generated_context = load_domain_generation_checkpoint(
            output_root=generated_root,
            ontology_name="ontosynthesis",
        )
        from src.agents.scripts_and_prompts_generation.ttl_parser import (
            parse_ontology_ttl,
        )

        fresh_parsed = parse_ontology_ttl(generated_context.ontology.ttl_file)
        parsed_path = (
            generated_root
            / "ontology_structures"
            / "ontosynthesis"
            / "parsed.json"
        )
        parsed_path.write_text(
            json.dumps(fresh_parsed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        generated_context = replace(generated_context, parsed=fresh_parsed)
        prompt_names = args.prompt_target or [
            "PRE_EXTRACTION_ITER_3.md",
            "EXTRACTION_ITER_3.md",
        ]
        targets = [
            generated_root / "prompts" / "ontosynthesis" / prompt_name
            for prompt_name in prompt_names
        ]
        generation = _generate_artifact_wave(
            context=generated_context,
            report={},
            targets=targets,
            model_name=args.generation_model,
            edit_backend="exact_edits",
            max_workers=2,
        )
        result = {
            "schema_version": "integrated-property-prompt-generation.v1",
            "model": args.generation_model,
            "generated_package": str(generated_root),
            "targets": {
                str(target.relative_to(generated_root)): report
                for target, report in generation.items()
            },
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "prompt_generation_results.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    context = load_domain_generation_checkpoint(
        output_root=artifact_root,
        ontology_name="ontosynthesis",
    )
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if args.score_only:
        trials = json.loads(
            (output_dir / "trials_summary.json").read_text(encoding="utf-8")
        ).get("trials") or []

        def score_trial(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
            index, trial = item
            judgement = _judge_target_extraction(
                context=context,
                fixture=fixture,
                case_dir=Path(str(trial["case_dir"])),
                iteration=args.iteration,
                judge_model=args.judge_model,
            )
            return {"trial": index, "case_dir": trial["case_dir"], **judgement}

        with ThreadPoolExecutor(
            max_workers=min(max(1, args.parallelism), max(1, len(trials)))
        ) as executor:
            scores = list(executor.map(score_trial, enumerate(trials, start=1)))
        result = {
            "schema_version": "property-contract-extraction-scores.v1",
            "judge_model": args.judge_model,
            "parallelism": min(max(1, args.parallelism), max(1, len(trials))),
            "scores": scores,
        }
        (output_dir / "scorer_results.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    parsed = json.loads(
        (
            artifact_root / "ontology_structures" / "ontosynthesis" / "parsed.json"
        ).read_text(encoding="utf-8")
    )
    plan = json.loads(
        (
            artifact_root / "iterations" / "ontosynthesis" / "iterations.json"
        ).read_text(encoding="utf-8")
    )
    contract = derive_iteration_property_contract(
        parsed=parsed,
        compiled_plan=plan,
        iteration_number=args.iteration,
    )
    block = render_property_contract_block(contract)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "property_contract.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "property_contract.txt").write_text(block, encoding="utf-8")

    result = run_targeted_extraction_trials(
        context=context,
        artifact_root=artifact_root,
        fixture=fixture,
        baseline_case_dir=Path(args.baseline_case).resolve(),
        iteration=args.iteration,
        sub_iteration=None,
        output_dir=output_dir,
        repeats=args.repeats,
        judge_model=None,
        freeze_pre_extraction=False,
        parallelism=args.parallelism,
        property_contract_block=block if args.runtime_contract_injection else None,
    )
    print(
        json.dumps(
            {
                "repeats": result["repeats"],
                "parallelism": result["parallelism"],
                "ok": [trial["ok"] for trial in result["trials"]],
                "property_contract_sha256": contract_sha256(contract),
                "runtime_contract_injection": args.runtime_contract_injection,
                "summary": str(output_dir / "trials_summary.json"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
