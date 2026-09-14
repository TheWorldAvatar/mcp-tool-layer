"""Import a generated occurrence package and execute a mock script."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from rdflib import Graph, URIRef

from src.kg_building_mcp_generation_v2.abox_mock.facts import _match_object_by_label
from src.kg_building_mcp_generation_v2.abox_mock.instantiate import (
    NESTED_SENTINEL,
    ROOT_SENTINEL,
    MockScript,
    decode_nested_ref,
)


def _bind_kwargs(fn: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    accepted = {
        name
        for name, param in signature.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {key: value for key, value in arguments.items() if key in accepted}


def _resolve(
    value: Any,
    *,
    root_iri: str,
    iri_by_call: Mapping[str, str],
    graph: Graph | None = None,
) -> Any:
    if value == ROOT_SENTINEL:
        return root_iri
    if isinstance(value, str) and value.startswith("$call:"):
        name = value.split(":", 1)[1]
        if name not in iri_by_call:
            raise KeyError(f"parent occurrence {name} has not been created")
        return iri_by_call[name]
    if isinstance(value, str) and value.startswith(NESTED_SENTINEL):
        host, predicate_iri, label = decode_nested_ref(value)
        if host not in iri_by_call:
            raise KeyError(f"host occurrence {host} has not been created")
        if graph is None:
            raise KeyError("nested parent resolution requires the retained graph")
        found = _match_object_by_label(
            graph, URIRef(iri_by_call[host]), predicate_iri, label
        )
        if not found:
            raise KeyError(
                f"nested parent {label!r} via {predicate_iri} on {host} was not created"
            )
        return found
    return value


def load_package(scripts_dir: Path, package_name: str):
    parent = str(scripts_dir.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(f"{package_name}.main")


def unload_generated_package(package_name: str, scripts_root: Path | None = None) -> None:
    """Drop a previously imported generated MCP package so another pack can load."""
    prefix = f"{package_name}."
    for key in list(sys.modules):
        if key == package_name or key.startswith(prefix):
            del sys.modules[key]
    if scripts_root is not None:
        text = str(Path(scripts_root))
        sys.path[:] = [item for item in sys.path if item != text]


def load_frozen_package(scripts_root: Path, package_name: str):
    """Import ``<scripts_root>/<package_name>/main.py`` from a frozen pack."""
    unload_generated_package(package_name)
    root = str(Path(scripts_root))
    sys.path[:] = [item for item in sys.path if item != root]
    sys.path.insert(0, root)
    importlib.invalidate_caches()
    return importlib.import_module(f"{package_name}.main")


def execute_script(
    module: Any,
    script: MockScript,
    *,
    doi: str,
    data_dir: Path,
) -> dict[str, Any]:
    """Call init_memory, generated create_*/link_*, and export_memory."""
    os.environ["TWA_AGENTIC_DATA_DIR"] = str(data_dir)
    rdf_runtime = module.rdf_runtime
    operations = module.operations
    init = json.loads(
        rdf_runtime.init_memory(
            doi,
            script.root_label,
            root_iri=script.root_iri,
        )
    )
    if str(init.get("status") or "").lower() != "ok":
        raise RuntimeError(f"init_memory failed: {init}")
    root_iri = str(init.get("bound_root_iri") or script.root_iri)
    iri_by_call: dict[str, str] = {}
    receipts: list[dict[str, Any]] = []
    for call in script.calls:
        fn = getattr(operations, call.name)
        try:
            bound = {
                key: _resolve(
                    value,
                    root_iri=root_iri,
                    iri_by_call=iri_by_call,
                    graph=rdf_runtime.retained_graph(),
                )
                for key, value in call.arguments.items()
            }
            payload = json.loads(fn(**_bind_kwargs(fn, bound)))
        except (TypeError, KeyError) as exc:
            payload = {
                "status": "rejected",
                "code": "MISSING_REQUIRED_ARGUMENT",
                "message": str(exc),
            }
        receipts.append({"name": call.name, "kind": call.kind, "result": payload})
        if str(payload.get("status") or "").lower() != "ok":
            return {
                "ok": False,
                "init": init,
                "root_iri": root_iri,
                "iri_by_call": iri_by_call,
                "receipts": receipts,
                "export": None,
                "graph": rdf_runtime.retained_graph(),
            }
        iri = str(payload.get("iri") or "").strip()
        if call.kind == "create" and iri:
            iri_by_call[call.name] = iri
    exported = json.loads(module.export_memory(doi, script.root_label))
    graph: Graph = rdf_runtime.retained_graph()
    return {
        "ok": str(exported.get("status") or "").lower() == "ok",
        "init": init,
        "root_iri": root_iri,
        "iri_by_call": iri_by_call,
        "receipts": receipts,
        "export": exported,
        "graph": graph,
    }
