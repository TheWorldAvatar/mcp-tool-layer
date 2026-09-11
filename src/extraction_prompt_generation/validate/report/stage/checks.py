"""Check-layer stage probes: existing-entity lookup and ordered-member integrity.

Runs against `_creation_checks.py`. See stage/README.md.
"""

from __future__ import annotations

import ast
import inspect
import json
from typing import Any

from rdflib import Literal, URIRef
from rdflib.namespace import RDF

from src.extraction_prompt_generation.compile.reuse_policy import (
    EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES,
)
from src.extraction_prompt_generation.generate.artifact_surface import (
    _literal_all_manifest,
)
from src.extraction_prompt_generation.validate.report.common import (
    _graph_fingerprint,
    _ordered_violation_code_report,
    _required_operation_fixture_triples,
    empty_existing_check_probe_failures,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def _existing_entity_check_contracts(context: Any) -> list[dict[str, Any]]:
    from src.extraction_prompt_generation.compile.reuse_policy import (
        existing_entity_check_contracts as _reuse_existing_entity_check_contracts,
    )

    return _reuse_existing_entity_check_contracts(
        parsed=getattr(context, "parsed", {}) or {},
        contract=context.contract,
    )


def _existing_entity_check_manifest(context: Any) -> list[str]:
    return [
        "check_ordered_members",
        *(str(item["public_tool"]) for item in _existing_entity_check_contracts(context)),
    ]


def probe_existing_checks(probe: StageProbe) -> None:
    """Validate check_* surface, empty-lookup envelopes, and ordered-member codes."""
    graph = probe.graph
    assert graph is not None
    checker = getattr(probe.imported_module, "check_ordered_members", None)
    expected_existing_checks = [
        str(item.get("public_tool") or "")
        for item in _existing_entity_check_contracts(probe.context)
        if str(item.get("public_tool") or "")
    ]
    expected_checks = {
        "check_ordered_members",
        *expected_existing_checks,
    }
    public_checks = {
        symbol
        for symbol, value in vars(probe.imported_module).items()
        if symbol.startswith("check_") and callable(value)
    }
    if public_checks != expected_checks:
        probe.fail(
            f"{probe.name}: check surface differs from T-Box-derived checks; "
            f"expected={sorted(expected_checks)} actual={sorted(public_checks)}"
        )
    expected_manifest = _existing_entity_check_manifest(probe.context)
    if getattr(probe.imported_module, "__all__", None) != expected_manifest:
        probe.fail(f"{probe.name}: __all__ must equal {expected_manifest}")
    try:
        if _literal_all_manifest(probe.path) != expected_manifest:
            probe.fail(f"{probe.name}: literal __all__ must equal {expected_manifest}")
    except Exception as exc:
        probe.fail(
            f"{probe.name}: invalid literal __all__ manifest: "
            f"{type(exc).__name__}: {exc}"
        )
    check_contracts = _existing_entity_check_contracts(probe.context)
    if any(
        str(item.get("lookup_scope") or "") in EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES
        for item in check_contracts
    ):
        tree = ast.parse(probe.path.read_text(encoding="utf-8"))
        called_names = {
            (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        for required_call in (
            "judge_reuse_pairs",
            "register_central_reuse_authorization",
        ):
            if required_call not in called_names:
                probe.fail(
                    f"{probe.name}: evidence-required existing checks "
                    f"must call {required_call}"
                )
        judge_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "judge_reuse_pairs")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "judge_reuse_pairs"
                )
            )
        ]
        if not judge_calls or any(
            len(node.args) != 1 or node.keywords for node in judge_calls
        ):
            probe.fail(
                f"{probe.name}: judge_reuse_pairs must be called with "
                "exactly one positional requests list"
            )
        authorization_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "register_central_reuse_authorization"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register_central_reuse_authorization"
                )
            )
        ]
        required_authorization_keywords = {
            "candidate_iri",
            "pair_id",
            "judgement",
        }
        if not authorization_calls or any(
            node.args
            or {keyword.arg for keyword in node.keywords}
            != required_authorization_keywords
            for node in authorization_calls
        ):
            probe.fail(
                f"{probe.name}: register_central_reuse_authorization must "
                "use exactly candidate_iri, pair_id, and judgement"
            )
    for check_contract in check_contracts:
        tool_name = str(check_contract.get("public_tool") or "")
        check_tool = getattr(probe.imported_module, tool_name, None)
        if not callable(check_tool):
            continue
        signature = inspect.signature(check_tool)
        proposed_parameter = signature.parameters.get("proposed_entity_json")
        label_parameter = signature.parameters.get("label")
        if (
            proposed_parameter is None
            or proposed_parameter.default != ""
            or label_parameter is None
            or label_parameter.default != ""
            or label_parameter.kind is not inspect.Parameter.KEYWORD_ONLY
        ):
            probe.fail(
                f"{probe.name}: {tool_name} must expose "
                "proposed_entity_json: str = '' and keyword-only "
                "label: str = '' compatibility inputs"
            )
        try:
            payload = json.loads(check_tool())
        except Exception as exc:
            probe.fail(
                f"{probe.name}: {tool_name} did not return a JSON report: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        probe.failures.extend(
            empty_existing_check_probe_failures(
                artifact_name=probe.name,
                tool_name=tool_name,
                lookup_scope=str(check_contract.get("lookup_scope") or ""),
                payload=payload,
                expected_reuse_authorized=bool(check_contract.get("reuse_authorized")),
                expected_reference_resolution_only=bool(
                    check_contract.get("reference_resolution_only")
                ),
            )
        )
    _probe_ordered_members(probe, checker)


def _probe_ordered_members(probe: StageProbe, checker: Any) -> None:
    graph = probe.graph
    assert graph is not None
    profile = probe.context.contract.get("ordered_member_profile") or {}
    member_locals = list(profile.get("individually_linked_object_properties") or [])
    order_locals = list(profile.get("single_valued_ordering_properties") or [])
    properties = probe.context.parsed.get("properties") or {}
    classes = probe.context.parsed.get("classes") or {}
    member_iri = (
        str((properties.get(member_locals[0]) or {}).get("iri") or "")
        if member_locals
        else ""
    )
    order_iri = (
        str((properties.get(order_locals[0]) or {}).get("iri") or "")
        if order_locals
        else ""
    )
    ordered_locals = list(profile.get("ordered_member_classes") or [])
    concrete_local = next(
        (
            local
            for local in ordered_locals
            if (classes.get(local) or {}).get("parent_classes")
        ),
        ordered_locals[0] if ordered_locals else "",
    )
    concrete_iri = str((classes.get(concrete_local) or {}).get("iri") or "")
    parent_types = [
        str((classes.get(parent) or {}).get("iri") or "")
        for parent in ((classes.get(concrete_local) or {}).get("parent_classes") or [])
        if str((classes.get(parent) or {}).get("iri") or "")
    ]
    ordered_response_schema_failed = False

    def run_order_probe(
        triples: list[tuple[URIRef, URIRef, Any]],
        expected_codes: set[str],
    ) -> None:
        nonlocal ordered_response_schema_failed
        graph.remove((None, None, None))
        for triple in triples:
            graph.add(triple)
        before = _graph_fingerprint(graph)
        try:
            raw_result = checker()
            result = json.loads(raw_result)
        except Exception as exc:
            probe.fail(
                f"{probe.name}: ordered-member behavior probe failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return
        if before != _graph_fingerprint(graph):
            probe.fail(f"{probe.name}: check_ordered_members mutated the graph")
        codes, response_schema_errors = _ordered_violation_code_report(result)
        if expected_codes:
            if result.get("status") not in {"rejected", "error"}:
                probe.fail(
                    f"{probe.name}: invalid ordered graph did not return rejection"
                )
            if response_schema_errors:
                if not ordered_response_schema_failed:
                    probe.fail(
                        f"{probe.name}: FIELD_SCHEMA_ERROR ordered violation "
                        "response schema invalid: "
                        + response_schema_errors[0]
                        + "; repair_hint=Every `violations` item must use "
                        "the exact required discriminator key `code`, for example "
                        '`{"code": "missing_order", ...}`. Rename '
                        "`violation_code` to `code`; do not change the "
                        "ordered-member detection algorithm to repair this "
                        "schema failure."
                    )
                    ordered_response_schema_failed = True
                return
            missing_codes = expected_codes - codes
            if missing_codes:
                repair_hint = ""
                if "non_contiguous_order" in missing_codes:
                    repair_hint = (
                        "; repair_hint=For each parent, collect every linked "
                        "ordered member before filtering invalid/missing order "
                        "values. Let N be that full ordered-member count and "
                        "compare the set of valid observed orders with "
                        "set(range(1, N + 1)). Do not derive N from "
                        "max(observed), len(unique observed orders), or only "
                        "members having valid order literals. Generic example: "
                        "three linked ordered members with observed orders 1, 2, "
                        "and missing must report both missing_order and "
                        "non_contiguous_order because expected={1,2,3} and "
                        "observed={1,2}."
                    )
                probe.fail(
                    f"{probe.name}: ordered check missed violations "
                    f"{sorted(missing_codes)}{repair_hint}"
                )
        elif result.get("status") != "ok" or codes:
            probe.fail(
                f"{probe.name}: valid ordered graph was not accepted; "
                f"observed violation codes={sorted(codes)}"
            )

    if callable(checker) and member_iri and order_iri and concrete_iri:
        member_predicate = URIRef(member_iri)
        order_predicate = URIRef(order_iri)
        class_ref = URIRef(concrete_iri)
        parent = URIRef("urn:validator:ordered-parent")
        other_parent = URIRef("urn:validator:other-parent")
        first = URIRef("urn:validator:ordered-1")
        second = URIRef("urn:validator:ordered-2")
        required_operation_triples = [
            *_required_operation_fixture_triples(
                operation_units=(
                    probe.context.contract.get("materialization_operation_units") or {}
                ),
                owner_class_iri=concrete_iri,
                owner=first,
                parent=parent,
                token="ordered-1",
            ),
            *_required_operation_fixture_triples(
                operation_units=(
                    probe.context.contract.get("materialization_operation_units") or {}
                ),
                owner_class_iri=concrete_iri,
                owner=second,
                parent=parent,
                token="ordered-2",
            ),
        ]
        type_triples = [
            (first, RDF.type, class_ref),
            (second, RDF.type, class_ref),
            *[
                (node, RDF.type, URIRef(parent_iri))
                for node in (first, second)
                for parent_iri in parent_types
            ],
            *required_operation_triples,
        ]
        links = [
            (parent, member_predicate, first),
            (parent, member_predicate, second),
        ]
        run_order_probe(
            [
                *type_triples,
                *links,
                (first, order_predicate, Literal(1)),
                (second, order_predicate, Literal(2)),
            ],
            set(),
        )
        run_order_probe(
            [
                *type_triples,
                *links,
                (second, order_predicate, Literal(2)),
            ],
            {"missing_order", "non_contiguous_order"},
        )
        run_order_probe(
            [
                *type_triples,
                *links,
                (first, order_predicate, Literal(1)),
                (second, order_predicate, Literal(1)),
            ],
            {"duplicate_order", "non_contiguous_order"},
        )
        run_order_probe(
            [
                *type_triples,
                *links,
                (first, order_predicate, Literal(1)),
                (second, order_predicate, Literal(3)),
            ],
            {"non_contiguous_order"},
        )
        run_order_probe(
            [
                *type_triples,
                *links,
                (other_parent, member_predicate, first),
                (first, order_predicate, Literal(1)),
                (second, order_predicate, Literal(2)),
            ],
            {"multiple_parents"},
        )
        if parent_types:
            run_order_probe(
                [
                    (first, RDF.type, class_ref),
                    *links,
                    (first, order_predicate, Literal(1)),
                    (second, order_predicate, Literal(2)),
                    (second, RDF.type, class_ref),
                    *[
                        (second, RDF.type, URIRef(parent_iri))
                        for parent_iri in parent_types
                    ],
                    *required_operation_triples,
                ],
                {"missing_explicit_ancestor_type"},
            )
