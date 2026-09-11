"""Derive one iteration's complete property surface from the active T-Box.

Used by prompt contracts so GPT-5 sees every owned datatype and object
property, not only planner-assigned bridges. See tbox/README.md.
"""

from __future__ import annotations

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
