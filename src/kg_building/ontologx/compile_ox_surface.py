"""Compile an OX strict-no-prompt occurrence surface from a Pipeline MCP package."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


from paths import HERE, REPO_ROOT
DEFAULT_ARTIFACT = (
    REPO_ROOT / "ai_generated_contents_occurrence_surface_20260904_from_human_domain"
)
SCHEMA = "ox-strict-noprompt-surface.v1"
CREATE_RE = re.compile(r"^create_(?P<owner>.+)$")
PARENT_LINK_RE = re.compile(
    r"_link\(relationships\.add_(?P<predicate>[A-Za-z0-9_]+),\s*parent_iri"
)
BOUND_ROOT_PARENTS = {
    "ontospecies": set(),
    "ontomops": set(),
    "medical": {
        "hasTimeline",
        "hasDiagnosis",
        "hasPatientInfo",
        "hasProcedure",
        "hasSurgicalApproach",
        "hasSurgicalTeam",
        "hasPathologyOutcome",
        "hasComplication",
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parent_predicates(operations_path: Path) -> dict[str, str]:
    if not operations_path.is_file():
        return {}
    text = operations_path.read_text(encoding="utf-8")
    current = ""
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^def create_([A-Za-z0-9_]+)\(", line)
        if match:
            current = match.group(1)
            continue
        if not current:
            continue
        if line.startswith("def "):
            current = ""
            continue
        link = PARENT_LINK_RE.search(line)
        if link and current not in mapping:
            mapping[current] = link.group("predicate")
    return mapping


def _identity_kind(loop_guard: dict[str, Any], tool_name: str) -> str:
    for item in loop_guard.get("mutation_tools") or []:
        if str(item.get("name") or "") == tool_name:
            return str(item.get("identity_kind") or "semantic_occurrence")
    return "semantic_occurrence"


def compile_domain_surface(
    *,
    domain: str,
    artifact_root: Path,
    bound_root: str,
) -> dict[str, Any]:
    scripts = artifact_root / "scripts" / domain
    ownership = _load_json(scripts / "_occurrence_argument_ownership.json")
    loop_guard = _load_json(scripts / "_occurrence_loop_guard.json")
    parent_predicates = _parent_predicates(scripts / f"{domain}_occurrence_operations.py")
    bound_parents = BOUND_ROOT_PARENTS.get(domain, set())
    owners: list[dict[str, Any]] = []
    for tool in ownership.get("tools") or []:
        name = str(tool.get("name") or "")
        match = CREATE_RE.match(name)
        if not match:
            continue
        owner_class = match.group("owner")
        parameters = tool.get("parameters") or {}
        allowed = [str(item) for item in tool.get("allowed_arguments") or [] if item]
        self_facets: list[str] = []
        nested: list[dict[str, Any]] = []
        has_parent = False
        for argument, spec in parameters.items():
            role = str(spec.get("role") or "")
            path = str(spec.get("owner_path") or "")
            prop = str(spec.get("property_local") or spec.get("property") or "")
            if role == "parent_iri":
                has_parent = True
                continue
            if role in {"occurrence_label"}:
                continue
            if path == "self" and role == "datatype" and prop:
                self_facets.append(prop)
                continue
            if path.startswith("self.") and prop:
                nested.append(
                    {
                        "argument": argument,
                        "owner_path": path,
                        "property": prop,
                        "role": role,
                        "requires": list(spec.get("requires") or []),
                    }
                )
        parent_predicate = parent_predicates.get(owner_class, "")
        identity_kind = _identity_kind(loop_guard, name)
        owners.append(
            {
                "owner_class": owner_class,
                "parent_predicate": parent_predicate,
                "parent_is_bound_root": bool(
                    parent_predicate and parent_predicate in bound_parents
                )
                or (has_parent and parent_predicate in bound_parents),
                "ordered": identity_kind == "ordered" or "hasOrder" in allowed,
                "allowed_arguments": allowed,
                "self_facets": self_facets,
                "nested_ownership": nested,
            }
        )
    owners.sort(key=lambda item: str(item.get("owner_class") or ""))
    return {
        "schema_version": SCHEMA,
        "source_artifact": str(artifact_root.name),
        "domain": domain,
        "bound_root": bound_root,
        "note": (
            "Compiled from the Pipeline occurrence MCP ownership map and "
            "parent-link writers. No TBox comments."
        ),
        "owner_occurrences": owners,
        "root_linkers": [],
        "reusable_classes": [],
    }


def write_domain_surface(
    domain: str,
    *,
    artifact_root: Path,
    dest: Path,
    bound_root: str,
) -> dict[str, Any]:
    payload = compile_domain_surface(
        domain=domain,
        artifact_root=artifact_root,
        bound_root=bound_root,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--out-dir", type=Path, default=HERE / "resources")
    parser.add_argument(
        "--domain",
        action="append",
        dest="domains",
        help="Compile only this domain (repeatable). Default: ontospecies, ontomops, medical.",
    )
    args = parser.parse_args()
    artifact = args.artifact_root
    if not artifact.is_absolute():
        artifact = REPO_ROOT / artifact
    specs = (
        ("ontospecies", "ontosyn:ChemicalSynthesis", "ontospecies_occurrence_surface_ox.json"),
        ("ontomops", "ontosyn:ChemicalSynthesis", "ontomops_occurrence_surface_ox.json"),
        ("medical", "medical:MedicalCase", "medical_occurrence_surface_ox.json"),
    )
    wanted = {str(name).strip() for name in (args.domains or []) if str(name).strip()}
    if wanted:
        specs = tuple(item for item in specs if item[0] in wanted)
        missing = wanted - {item[0] for item in specs}
        if missing:
            raise SystemExit(f"Unknown --domain: {', '.join(sorted(missing))}")
    for domain, bound_root, filename in specs:
        payload = write_domain_surface(
            domain,
            artifact_root=artifact,
            dest=args.out_dir / filename,
            bound_root=bound_root,
        )
        print(
            f"{domain}: {len(payload['owner_occurrences'])} owners -> {args.out_dir / filename}"
        )


if __name__ == "__main__":
    main()
