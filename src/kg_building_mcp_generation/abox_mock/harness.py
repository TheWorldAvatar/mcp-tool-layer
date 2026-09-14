"""Run a T-Box covering A-Box mock against generated occurrence MCP tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from src.kg_building_mcp_generation.abox_mock.compile import (
    compile_pack_surface,
    compile_surface_without_llm,
)
from src.kg_building_mcp_generation.abox_mock.emit import emit_occurrence_package
from src.kg_building_mcp_generation.abox_mock.facts import (
    actual_facts,
    compare_facts,
    expected_facts,
)
from src.kg_building_mcp_generation.abox_mock.instantiate import (
    MockScript,
    drop_required_identity_argument,
    instantiate_covering_script,
)
from src.kg_building_mcp_generation.abox_mock.runner import (
    execute_script,
    load_frozen_package,
    load_package,
    unload_generated_package,
)

TYPE_PRED = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def _allowed_extra(fact: tuple[str, str, str], expected: set[tuple[str, str, str]]) -> bool:
    subject, predicate, _obj = fact
    if predicate == TYPE_PRED:
        return any(item[0] == subject and item[1] == TYPE_PRED for item in expected)
    return False


def run_covering_abox_mock(
    *,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    ontology_name: str,
    work_dir: Path,
    root_iri: str = "https://example.test/abox-mock/root",
    doi: str = "abox-mock",
    script: MockScript | None = None,
) -> dict[str, Any]:
    """Compile, instantiate, emit, run generated tools, and compare keyed facts."""
    compiled_bundle = compile_surface_without_llm(
        parsed, contract, ontology_name=ontology_name
    )
    compiled = compiled_bundle["compiled"]
    surface = compiled_bundle["quantity_surface"]
    if compiled.get("errors"):
        raise ValueError("occurrence surface compile errors: " + "; ".join(compiled["errors"]))
    mock = script or instantiate_covering_script(
        compiled,
        surface,
        contract=contract,
        root_iri=root_iri,
    )
    package_name = f"abox_mock_{uuid4().hex[:10]}"
    scripts_dir = Path(work_dir) / package_name
    output_root = Path(work_dir) / "output"
    emit_occurrence_package(
        ontology_name=ontology_name,
        parsed=parsed,
        contract=contract,
        scripts_dir=scripts_dir,
        output_root=output_root,
    )
    module = load_package(scripts_dir, package_name)
    data_dir = Path(work_dir) / "data"
    executed = execute_script(module, mock, doi=doi, data_dir=data_dir)
    return _compare_executed(mock=mock, compiled=compiled, executed=executed)


def _compare_executed(
    *,
    mock: MockScript,
    compiled: Mapping[str, Any],
    executed: Mapping[str, Any],
) -> dict[str, Any]:
    expected = expected_facts(mock, compiled)
    actual = actual_facts(
        mock,
        compiled,
        executed["graph"],
        iri_by_call=executed["iri_by_call"],
        root_iri=executed["root_iri"],
    )
    diff = compare_facts(expected, actual)
    unexpected = [
        fact for fact in diff["extra"] if not _allowed_extra(fact, expected)
    ]
    return {
        "compiled": compiled,
        "script": mock,
        "executed": {
            "ok": executed["ok"],
            "init": executed["init"],
            "root_iri": executed["root_iri"],
            "iri_by_call": executed["iri_by_call"],
            "receipts": executed["receipts"],
            "export": executed["export"],
        },
        "expected_facts": sorted(expected),
        "actual_facts": sorted(actual),
        "missing": diff["missing"],
        "extra": unexpected,
        "ok": bool(
            executed["ok"]
            and not diff["missing"]
            and not unexpected
        ),
    }


def _restrict_compiled_to_package(compiled: Mapping[str, Any], module: Any) -> dict[str, Any]:
    operations = getattr(module, "operations", None)
    available = {
        name
        for name in dir(operations)
        if callable(getattr(operations, name, None))
    }
    restricted = dict(compiled)
    compiled_tools = list(compiled.get("public_tools") or [])
    compiled_linkers = list(compiled.get("public_linkers") or [])
    restricted["public_tools"] = [
        item for item in compiled_tools if str(item.get("name") or "") in available
    ]
    restricted["public_linkers"] = [
        item for item in compiled_linkers if str(item.get("name") or "") in available
    ]
    return restricted


def run_covering_abox_mock_on_package(
    *,
    pack_root: Path,
    ontology_name: str,
    work_dir: Path,
    root_iri: str | None = None,
    doi: str = "abox-mock",
    script: MockScript | None = None,
) -> dict[str, Any]:
    """Instantiate and run a covering mock against a frozen generated MCP pack."""
    bundle = compile_pack_surface(Path(pack_root), ontology_name)
    compiled = bundle["compiled"]
    surface = bundle["quantity_surface"]
    contract = bundle["contract"]
    if compiled.get("errors"):
        raise ValueError("occurrence surface compile errors: " + "; ".join(compiled["errors"]))
    pack = Path(pack_root)
    bound_root = root_iri or f"https://example.test/abox-mock/{ontology_name}/root"
    os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(pack)
    os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", None)
    os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME", None)
    scripts_root = pack / "scripts"
    module = load_frozen_package(scripts_root, ontology_name)
    compiled = _restrict_compiled_to_package(compiled, module)
    mock = script or instantiate_covering_script(
        compiled,
        surface,
        contract=contract,
        root_iri=bound_root,
    )
    data_dir = Path(work_dir) / "data"
    try:
        executed = execute_script(module, mock, doi=doi, data_dir=data_dir)
        result = _compare_executed(mock=mock, compiled=compiled, executed=executed)
    finally:
        unload_generated_package(ontology_name, scripts_root)
    result["pack_root"] = str(pack)
    result["ontology_name"] = ontology_name
    return result


def run_invalid_identity_abox_mock(
    *,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    ontology_name: str,
    work_dir: Path,
    root_iri: str = "https://example.test/abox-mock/root",
    doi: str = "abox-mock-invalid",
) -> dict[str, Any]:
    compiled_bundle = compile_surface_without_llm(
        parsed, contract, ontology_name=ontology_name
    )
    compiled = compiled_bundle["compiled"]
    surface = compiled_bundle["quantity_surface"]
    covering = instantiate_covering_script(
        compiled, surface, contract=contract, root_iri=root_iri
    )
    invalid = drop_required_identity_argument(covering, compiled)
    result = run_covering_abox_mock(
        parsed=parsed,
        contract=contract,
        ontology_name=ontology_name,
        work_dir=work_dir,
        root_iri=root_iri,
        doi=doi,
        script=invalid,
    )
    rejected = [
        item
        for item in result["executed"]["receipts"]
        if str((item.get("result") or {}).get("status") or "").lower() != "ok"
    ]
    result["ok"] = bool(rejected)
    result["rejected"] = rejected
    return result
