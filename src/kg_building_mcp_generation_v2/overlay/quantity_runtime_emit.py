"""Emit overlay quantity helpers into generated occurrence operations."""

from __future__ import annotations

from typing import Any, Mapping


def quantity_runtime_source(surface: Mapping[str, Any]) -> str:
    facets = {
        str(name): (str(spec.get("predicate_iri") or ""), str(spec.get("range_iri") or ""))
        for name, spec in (surface.get("facets") or {}).items()
    }
    examples = {
        str(class_iri): list(spec.get("example_labels") or ["<number> <unit>"])
        for class_iri, spec in (surface.get("classes") or {}).items()
    }
    aliases = {
        str(class_iri): list(spec.get("allowed_unit_aliases") or [])
        for class_iri, spec in (surface.get("classes") or {}).items()
    }
    qualitative = {
        str(class_iri): list(spec.get("qualitative_labels") or [])
        for class_iri, spec in (surface.get("classes") or {}).items()
    }
    example_calls = {
        str(name): str(value)
        for name, value in (surface.get("example_calls") or {}).items()
    }
    unit_local_to_class = {
        str(name): str(value)
        for name, value in (surface.get("unit_local_to_class") or {}).items()
    }
    return f'''
_ABS_IRI = re.compile(r"^(https?://|urn:)", re.IGNORECASE)
_QUANTITY_FACETS: dict[str, tuple[str, str]] = {facets!r}
_CLASS_EXAMPLES: dict[str, list[str]] = {examples!r}
_PREFERRED_ALIASES: dict[str, list[str]] = {aliases!r}
_QUALITATIVE_LABELS: dict[str, list[str]] = {qualitative!r}
_FACET_EXAMPLE_CALLS: dict[str, str] = {example_calls!r}
_UNIT_LOCAL_TO_CLASS: dict[str, str] = {unit_local_to_class!r}
_UNATTACHED_OM2: dict[tuple[str, str], str] = {{}}
_OM2_FACET_PREDICATES = tuple(spec[0] for spec in _QUANTITY_FACETS.values())


def _is_absolute_iri(value: str) -> bool:
    return bool(_ABS_IRI.match(str(value).strip()))


def _om2_cache_key(quantity_class_iri: str, label: str) -> tuple[str, str]:
    return (str(quantity_class_iri or "").strip(), _normalized(label))


def om2_quantity_is_attached(iri: str) -> bool:
    graph = rdf_runtime.retained_graph()
    target = URIRef(str(iri).strip())
    for predicate_iri in _OM2_FACET_PREDICATES:
        if any(graph.subjects(URIRef(predicate_iri), target)):
            return True
    return False


def unattached_om2_quantity_iri(quantity_class_iri: str, label: str) -> str | None:
    key = _om2_cache_key(quantity_class_iri, label)
    iri = _UNATTACHED_OM2.get(key)
    if not iri:
        return None
    graph = rdf_runtime.retained_graph()
    subject = URIRef(iri)
    expected = URIRef(str(quantity_class_iri).strip())
    if (subject, RDF.type, expected) not in graph:
        _UNATTACHED_OM2.pop(key, None)
        return None
    if om2_quantity_is_attached(iri):
        _UNATTACHED_OM2.pop(key, None)
        return None
    return iri


def remember_unattached_om2_quantity(quantity_class_iri: str, label: str, iri: str) -> None:
    text = str(iri or "").strip()
    if text:
        _UNATTACHED_OM2[_om2_cache_key(quantity_class_iri, label)] = text


def _class_local(range_iri: str) -> str:
    return str(range_iri).rsplit("/", 1)[-1]


def _example_labels(range_iri: str) -> list[str]:
    return list(_CLASS_EXAMPLES.get(range_iri, ["<number> <unit>"]))


def _facets_for_range(range_iri: str) -> list[str]:
    return sorted(name for name, spec in _QUANTITY_FACETS.items() if spec[1] == range_iri)


def _qualitative_class(label: str) -> str | None:
    for class_iri in _CLASS_EXAMPLES:
        if om2_runtime.resolve_qualitative_quantity_preset(URIRef(class_iri), label):
            return class_iri
    return None


def _class_for_unit_iri(unit_iri: str) -> str | None:
    return _UNIT_LOCAL_TO_CLASS.get(str(unit_iri).rsplit("/", 1)[-1])


def _lexeme_recovery(range_iri: str, facet: str, source: str) -> dict[str, object]:
    examples = _example_labels(range_iri)
    local = _class_local(range_iri)
    example = examples[0] if examples else "<number> <unit>"
    return {{
        "action": "retry_same_create_with_corrected_compact_label",
        "facet": facet or None,
        "expected_class": range_iri,
        "expected_class_local": local,
        "example_labels": examples,
        "allowed_unit_aliases": list(_PREFERRED_ALIASES.get(range_iri, [])),
        "qualitative_labels": list(_QUALITATIVE_LABELS.get(range_iri, [])),
        "hint": (
            f"Pass compact {{local}} text on {{facet or 'this field'}}, "
            f"e.g. {{example!r}}. Do not pass an IRI. Do not skip. "
            "Retry the same create_* call."
        ),
        "correct_shape": _FACET_EXAMPLE_CALLS.get(facet) or (
            "create_*(..., <compiled_quantity_argument>='<number> <unit>')"
        ),
    }}


def _quantity_iri_required(range_iri: str, label: str, *, facet: str = "") -> str:
    text = str(label).strip()
    examples = _example_labels(range_iri)
    local = _class_local(range_iri)
    return rdf_runtime.error_json(
        code="QUANTITY_IRI_REQUIRED",
        message=(
            f"link_om2_quantity.{{facet or 'facet'}} requires an OM-2 {{local}} IRI from "
            f"create_om2_quantity. Compact label {{text!r}} is not accepted here; "
            f"either mint that {{local}} first or, on create_*, pass compact text such as "
            f"{{', '.join(repr(item) for item in examples)}}."
        ),
        expected_class=range_iri,
        expected_class_local=local,
        facet=facet or None,
        source_value=text,
        example_labels=examples,
        skippable=False,
        retryable=True,
        recovery={{
            "action": "create_om2_quantity_then_link_iri",
            "create_om2_quantity": {{
                "quantity_class_iri": range_iri,
                "label": text,
            }},
            "then": (
                "Pass the returned IRI into link_om2_quantity. Preferred path for a "
                f"new occurrence is create_* with compact {{local}} text such as "
                f"{{examples[0]!r}}, not this linker."
            ),
        }},
    )


def _quantity_label_required(range_iri: str, label: str, *, facet: str = "") -> str:
    text = str(label).strip()
    examples = _example_labels(range_iri)
    local = _class_local(range_iri)
    return rdf_runtime.error_json(
        code="QUANTITY_LABEL_REQUIRED",
        message=(
            f"{{facet or 'quantity'}} requires compact {{local}} text such as "
            f"{{', '.join(repr(item) for item in examples)}}, not an IRI. "
            f"Got {{text!r}}. Do not pass a create_om2_quantity handle into create_*. "
            f"Retry this same create_* call with a {{local}} lexeme on {{facet or 'this field'}}."
        ),
        expected_class=range_iri,
        expected_class_local=local,
        facet=facet or None,
        source_value=text,
        example_labels=examples,
        allowed_unit_aliases=list(_PREFERRED_ALIASES.get(range_iri, [])),
        skippable=False,
        retryable=True,
        recovery=_lexeme_recovery(range_iri, facet, text),
    )


def _unit_class_mismatch(range_iri: str, label: str, *, facet: str = "", actual_class: str, actual_unit: str | None = None) -> str:
    text = str(label).strip()
    expected_local = _class_local(range_iri)
    actual_local = _class_local(actual_class)
    examples = _example_labels(range_iri)
    move_to = _facets_for_range(actual_class)
    unit_note = f" (unit {{actual_unit}})" if actual_unit else ""
    move_note = f" Move {{text!r}} onto {{', '.join(move_to)}}." if move_to else ""
    return rdf_runtime.error_json(
        code="QUANTITY_UNIT_CLASS_MISMATCH",
        message=(
            f"{{facet or 'quantity'}} requires compact {{expected_local}} text such as "
            f"{{', '.join(repr(item) for item in examples)}}. "
            f"Got {{text!r}}, which is {{actual_local}}{{unit_note}}.{{move_note}} "
            "The whole create_* was rejected; retry the same call with the "
            "corrected compact labels. Do not skip."
        ),
        expected_class=range_iri,
        expected_class_local=expected_local,
        actual_class=actual_class,
        actual_class_local=actual_local,
        actual_unit=actual_unit,
        move_value_to_facets=move_to,
        facet=facet or None,
        source_value=text,
        example_labels=examples,
        allowed_unit_aliases=list(_PREFERRED_ALIASES.get(range_iri, [])),
        skippable=False,
        retryable=True,
        recovery={{
            **_lexeme_recovery(range_iri, facet, text),
            "actual_class": actual_class,
            "move_value_to_facets": move_to,
        }},
    )


def _invalid_lexeme(range_iri: str, label: str, *, facet: str = "", reason: str) -> str:
    text = str(label).strip()
    examples = _example_labels(range_iri)
    local = _class_local(range_iri)
    aliases = list(_PREFERRED_ALIASES.get(range_iri, []))
    qualitative = list(_QUALITATIVE_LABELS.get(range_iri, []))
    extras = ""
    if qualitative:
        extras = f" Qualitative {{local}} labels: {{', '.join(repr(item) for item in qualitative)}}."
    return rdf_runtime.error_json(
        code="INVALID_OM2_QUANTITY",
        message=(
            f"{{facet or 'quantity'}} rejected {{text!r}} as {{local}}. {{reason}} "
            f"Correct shape: '<number> <unit>', e.g. {{', '.join(repr(item) for item in examples)}}."
            f"{{extras}} Allowed unit aliases: {{', '.join(aliases)}}. "
            "Retry this same create_* call with a corrected compact label. Do not skip."
        ),
        expected_class=range_iri,
        expected_class_local=local,
        facet=facet or None,
        source_value=text,
        example_labels=examples,
        allowed_unit_aliases=aliases,
        qualitative_labels=qualitative,
        parser_reason=reason,
        skippable=False,
        retryable=True,
        recovery=_lexeme_recovery(range_iri, facet, text),
    )


def _assert_lexeme_matches_class(range_iri: str, label: str, *, facet: str = "") -> None:
    qualitative = _qualitative_class(label)
    if qualitative == range_iri:
        return
    if qualitative is not None:
        raise _Rejected(_unit_class_mismatch(range_iri, label, facet=facet, actual_class=qualitative))
    try:
        _value, unit = om2_runtime.parse_om2_quantity_label(label)
        unit_iri = str(om2_runtime.resolve_om2_unit(unit))
    except ValueError as exc:
        reason = str(exc).split("allowed aliases:", 1)[0].strip(" ;,")
        raise _Rejected(_invalid_lexeme(range_iri, label, facet=facet, reason=reason)) from exc
    actual_class = _class_for_unit_iri(unit_iri)
    if actual_class and actual_class != range_iri:
        raise _Rejected(
            _unit_class_mismatch(
                range_iri,
                label,
                facet=facet,
                actual_class=actual_class,
                actual_unit=str(unit),
            )
        )


def _bind_quantity(range_iri: str, label: str, *, facet: str = "", allow_iri: bool = False) -> str:
    text = str(label).strip()
    if _is_absolute_iri(text):
        if not allow_iri:
            raise _Rejected(_quantity_label_required(range_iri, text, facet=facet))
        graph = rdf_runtime.retained_graph()
        subject = URIRef(text)
        expected = URIRef(range_iri)
        if (subject, RDF.type, expected) in graph:
            return text
        actual_types = sorted({{str(item) for item in graph.objects(subject, RDF.type)}})
        expected_local = _class_local(range_iri)
        raise _Rejected(
            rdf_runtime.error_json(
                code="RANGE_TYPE_MISMATCH" if actual_types else "OBJECT_TYPE_MISSING",
                message=(
                    f"{{text}} has type {{actual_types or ['none']}}, not {{range_iri}} "
                    f"({{expected_local}}). Pass an already-minted {{expected_local}} IRI "
                    f"into link_om2_quantity, or retry create_* with compact "
                    f"{{expected_local}} text such as {{_example_labels(range_iri)[0]!r}}. "
                    "Do not mint again if create_om2_quantity already returned an IRI "
                    "for that class and label."
                ),
                expected_class=range_iri,
                expected_class_local=expected_local,
                actual_types=actual_types,
                object_iri=text,
                facet=facet or None,
                example_labels=_example_labels(range_iri),
                skippable=False,
                retryable=True,
                recovery={{
                    "action": "pass_existing_iri_of_expected_class_or_compact_label_on_create",
                    "hint": (
                        f"The IRI you passed is not {{expected_local}}. "
                        f"Use a {{expected_local}} IRI here, or pass compact text "
                        f"such as {{_example_labels(range_iri)[0]!r}} on create_*. "
                        "Do not mint again for a class+label you already minted."
                    ),
                }},
            )
        )
    _assert_lexeme_matches_class(range_iri, text, facet=facet)
    created = _payload(rdf_runtime.create_om2_quantity(range_iri, text))
    return str(created["iri"])


def _link_quantity_fields(owner_iri: str, quantities: dict[str, str | None]) -> list[dict]:
    warnings: list[dict] = []
    for facet, raw in quantities.items():
        value = _optional_label(raw)
        if not value:
            continue
        spec = _QUANTITY_FACETS.get(facet)
        if spec is None:
            raise _Rejected(
                rdf_runtime.error_json(
                    code="UNKNOWN_OM2_FACET",
                    message=(
                        f"Unknown quantity facet {{facet!r}}. Allowed facets: "
                        f"{{', '.join(sorted(_QUANTITY_FACETS))}}."
                    ),
                    allowed_facets=sorted(_QUANTITY_FACETS),
                    skippable=False,
                    retryable=True,
                )
            )
        predicate_iri, range_iri = spec
        warning = _try_attach_quantity(owner_iri, facet, predicate_iri, range_iri, value)
        if warning is not None:
            warnings.append(warning)
    return warnings
'''


def overlay_replay_source() -> str:
    return '''def _replay(
    iri: str,
    fingerprint: str,
    quantities: dict[str, str | None] | None = None,
    parent_quantities: dict[str, str | None] | None = None,
    quantity_owner_iri: str | None = None,
    **metadata: object,
) -> str:
    if not quantities and not parent_quantities:
        return rdf_runtime.success_json(
            iri=iri,
            semantic_fingerprint=fingerprint,
            already_committed=True,
            graph_changed=False,
            graph_revision=_graph_revision(),
            message="Semantic mutation is already committed; continue.",
            **metadata,
        )
    before = set(rdf_runtime.retained_graph())
    try:
        warnings: list[dict] = []
        if quantities:
            warnings.extend(_link_quantity_fields(iri, quantities))
        if parent_quantities:
            warnings.extend(
                _link_quantity_fields(quantity_owner_iri or iri, parent_quantities)
            )
    except Exception as exc:
        return _rejection(exc, fingerprint, tool_name=str(metadata.get("tool_name") or ""))
    changed = set(rdf_runtime.retained_graph()) != before
    return rdf_runtime.success_json(
        iri=iri,
        semantic_fingerprint=fingerprint,
        already_committed=True,
        graph_changed=changed,
        graph_revision=_graph_revision(),
        facet_warnings=warnings,
        omitted_facet=bool(warnings),
        message=(
            "Occurrence reused; missing quantity facets linked."
            if changed
            else "Semantic mutation is already committed; continue."
        ),
        **metadata,
    )


def _reuse_unique_parent(
    iri: str,
    fingerprint: str,
    datatype_pairs: list,
    ensure: Callable[[str], None] | None = None,
    quantities: dict[str, str | None] | None = None,
    parent_quantities: dict[str, str | None] | None = None,
    quantity_owner_iri: str | None = None,
    **metadata: object,
) -> str:
    before = set(rdf_runtime.retained_graph())
    warnings: list[dict] = []
    try:
        with rdf_runtime.atomic_graph_transaction():
            _apply_missing_datatypes(iri, datatype_pairs)
            if ensure is not None:
                ensure(iri)
            if quantities:
                warnings.extend(_link_quantity_fields(iri, quantities))
            if parent_quantities:
                warnings.extend(
                    _link_quantity_fields(quantity_owner_iri or iri, parent_quantities)
                )
            if _existing_fingerprint(fingerprint) is None:
                _commit_fingerprint(fingerprint, iri)
    except Exception as exc:
        return _rejection(exc, fingerprint, tool_name=str(metadata.get("tool_name") or ""))
    changed = set(rdf_runtime.retained_graph()) != before
    return rdf_runtime.success_json(
        iri=iri,
        semantic_fingerprint=fingerprint,
        already_committed=True,
        graph_changed=changed,
        graph_revision=_graph_revision(),
        facet_warnings=warnings,
        omitted_facet=bool(warnings),
        message=(
            "Unique parent occurrence reused; missing owner facets filled."
            if changed
            else "Semantic mutation is already committed; continue."
        ),
        **metadata,
    )
'''


def overlay_attach_source() -> str:
    return '''def _attach_quantity(
    subject_iri: str,
    predicate_local: str,
    predicate_iri: str,
    range_iri: str,
    label: str,
    *,
    allow_iri: bool = False,
) -> None:
    writer = getattr(relationships, f"add_{predicate_local}")
    if _existing_link(subject_iri, predicate_iri, range_iri, label) is not None:
        return
    quantity_iri = _bind_quantity(
        range_iri, label, facet=predicate_local, allow_iri=allow_iri
    )
    if _existing_link(subject_iri, predicate_iri, range_iri, quantity_iri) is not None:
        return
    _link(writer, subject_iri, quantity_iri)


def _try_attach_quantity(subject_iri: str, predicate_local: str, predicate_iri: str, range_iri: str, label: str) -> dict | None:
    try:
        with rdf_runtime.atomic_graph_transaction():
            _attach_quantity(
                subject_iri,
                predicate_local,
                predicate_iri,
                range_iri,
                label,
                allow_iri=False,
            )
    except Exception as exc:
        payload = json.loads(_rejection(exc))
        payload["skippable"] = False
        payload["retryable"] = True
        payload["facet"] = predicate_local
        payload["source_value"] = str(label)
        payload.setdefault("expected_class", range_iri)
        payload.setdefault("example_labels", _example_labels(range_iri))
        payload.setdefault(
            "recovery",
            _lexeme_recovery(range_iri, predicate_local, str(label)),
        )
        raise _Rejected(json.dumps(payload, ensure_ascii=False, sort_keys=True)) from exc
    return None
'''


def link_om2_quantity_source() -> str:
    from src.kg_building_mcp_generation_v2.overlay.instruction import LINK_OM2_DOC

    return f'''
def link_om2_quantity(owner_iri: str, facet: str, quantity_iri: str) -> str:
    """{LINK_OM2_DOC}"""
    owner = str(owner_iri or "").strip()
    facet_name = str(facet or "").strip()
    quantity = _optional_label(quantity_iri)
    if not owner or not _is_absolute_iri(owner):
        return rdf_runtime.error_json(
            code="MISSING_OWNER_IRI",
            message="owner_iri must be the occurrence IRI returned by create_*.",
            skippable=False,
            retryable=True,
        )
    if quantity is None or not _is_absolute_iri(quantity):
        spec = _QUANTITY_FACETS.get(facet_name)
        range_iri = spec[1] if spec else ""
        return _quantity_iri_required(range_iri or owner, str(quantity_iri or ""), facet=facet_name)
    spec = _QUANTITY_FACETS.get(facet_name)
    if spec is None:
        return rdf_runtime.error_json(
            code="UNKNOWN_OM2_FACET",
            message=f"Unknown quantity facet {{facet_name!r}}.",
            allowed_facets=sorted(_QUANTITY_FACETS),
            skippable=False,
            retryable=True,
        )
    predicate_iri, range_iri = spec
    graph = rdf_runtime.retained_graph()
    if not any(graph.predicate_objects(URIRef(owner))):
        return rdf_runtime.error_json(
            code="OBJECT_TYPE_MISSING",
            message=f"{{owner}} is not in the current graph. Create the occurrence first.",
            object_iri=owner,
            skippable=False,
            retryable=True,
        )
    fingerprint = _fingerprint(
        "link_om2_quantity",
        {{"owner_iri": owner, "facet": facet_name, "quantity_iri": quantity}},
    )
    committed_iri = _existing_fingerprint(fingerprint)
    if committed_iri is not None:
        return _replay(committed_iri, fingerprint)
    evidenced = _existing_link(owner, predicate_iri, range_iri, quantity)
    if evidenced is not None:
        return _replay(owner, fingerprint)
    before = set(rdf_runtime.retained_graph())
    try:
        with rdf_runtime.atomic_graph_transaction():
            committed_iri = _existing_fingerprint(fingerprint)
            if committed_iri is not None:
                return _replay(committed_iri, fingerprint)
            _attach_quantity(
                owner,
                facet_name,
                predicate_iri,
                range_iri,
                quantity,
                allow_iri=True,
            )
            _commit_fingerprint(fingerprint, owner)
        return rdf_runtime.success_json(
            iri=owner,
            object_iri=quantity,
            facet=facet_name,
            semantic_fingerprint=fingerprint,
            already_committed=False,
            graph_changed=set(rdf_runtime.retained_graph()) != before,
            graph_revision=_graph_revision(),
            message="Quantity IRI linked.",
        )
    except Exception as exc:
        return _rejection(exc, fingerprint, tool_name="link_om2_quantity")
'''
