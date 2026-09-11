"""Domain-independent RDF graph state and Turtle serialization.

Source lives as this package. Generated MCP scripts still receive one flat
``_fixed_rdf_runtime.py`` from ``flatten_rdf_runtime()`` / ``write_fixed_rdf_runtime``.
File map: README.md in this folder.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .capabilities import (
    _PYTHON_DATATYPES,
    _bound_datatype_writer,
    _bound_entity_creator,
    _bound_relationship_writer,
    _clone_occurrence_local_object,
    _compatible_type,
    _compile_datatype_capabilities,
    _compile_entity_capabilities,
    _compile_ordered_entity_capabilities,
    _compile_relationship_capabilities,
    _hydrate_reusable_object_from_central_memory,
    _om2_quantity_range_iris,
    _typed_subject_candidates,
    create_om2_quantity,
    package_datatype_capabilities,
    package_entity_capabilities,
    package_om2_quantity_creator,
    package_ordered_entity_capabilities,
    package_relationship_capabilities,
)
from .constants import (
    RelationshipContractError,
    _GRAPH_TRANSACTION_LOCK,
    configured_main_ontology_name,
    generated_graph_iri,
    instance_base_iri,
    _PACKAGE_NAMESPACE,
    _REGISTRY_KEY,
    _REGISTRY_NAME,
    _REJECTION_REGISTRY_NAME,
    _REUSE_GRANT_REGISTRY_NAME,
    _SCOPE_REGISTRY_NAME,
    _SKIP_SANITIZE_PARAMS,
    _TOOL_TEXT_MAX_CHARS,
    _looks_like_iri,
    _short_random_iri,
    load_runtime_namespace,
    namespace_sidecar_path,
    om2_runtime_package,
    relationship_contract_path,
    sanitize_tool_text,
    wrap_public_tool,
)
from .envelopes import (
    _ERROR_RESERVED_ENVELOPE_KEYS,
    _SUCCESS_RESERVED_ENVELOPE_KEYS,
    _envelope_metadata,
    error_json,
    error_result,
    result_json,
    success_json,
    success_result,
)
from .lifecycle import (
    _canonical_runtime_scope,
    _enrichment_targets_from_global_state,
    _ensure_locked_identity_from_sidecar,
    _materialize_bound_root,
    _package_top_entity_class_iri,
    _seed_enrichment_targets_into_retained_graph,
    export_memory,
    init_memory,
    initialize_retained_graph,
    load_from_turtle_file,
    prepare_graph_for_export,
)
from .paths import (
    _package_ontology_name,
    _package_relationship_contract,
    central_memory_paths,
    document_memory_paths,
    resolve_case_dirname,
    safe_filename_component,
    scoped_memory_paths,
)
from .registry import (
    _graph_registry,
    _rejection_registry,
    _reuse_grant_registry,
    _scope_registry,
    _write_text_long_path_safe,
    abox_graph,
    atomic_graph_transaction,
    bind_parent_occurrence_argument,
    bind_root_argument,
    bound_enrichment_target_iri,
    bound_root_iri,
    current_memory_scope,
    export_graph_result,
    new_graph,
    register_semantic_rejection,
    reset_graph,
    reset_retained_graph,
    resolve_semantic_skip,
    retained_graph,
    serialize_turtle,
)
from .reuse import (
    _atomic_write_text,
    _central_memory_lock,
    _package_reuse_policy,
    _publish_candidate_projection,
    _validate_central_reuse_authorization,
    load_central_reuse_memory,
    load_document_reuse_memory,
    publish_reusable_entities_to_central_memory,
    publish_reusable_entities_to_document_memory,
    register_central_reuse_authorization,
)

_FLATTEN_MODULES = (
    "constants",
    "envelopes",
    "registry",
    "paths",
    "reuse",
    "lifecycle",
    "capabilities",
)


def _blank_relative_imports(source: str) -> str:
    """Drop intra-package imports so flatten can emit one module."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            for index in range(start, end):
                lines[index] = ""
    return "".join(lines)


def flatten_rdf_runtime() -> str:
    """Emit one flat ``_fixed_rdf_runtime.py`` equivalent of this package."""
    package_dir = Path(__file__).resolve().parent
    collected_imports: list[str] = []
    seen_imports: set[str] = set()
    bodies: list[str] = []

    for name in _FLATTEN_MODULES:
        source = _blank_relative_imports(
            (package_dir / f"{name}.py").read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
        header_end = 0
        for node in tree.body:
            if (
                header_end == 0
                and isinstance(node, ast.Expr)
                and isinstance(getattr(node, "value", None), ast.Constant)
                and isinstance(node.value.value, str)
            ):
                header_end = node.end_lineno or node.lineno
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                header_end = max(header_end, node.end_lineno or node.lineno)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                key = ast.unparse(node)
                if key not in seen_imports:
                    seen_imports.add(key)
                    collected_imports.append(key)
                header_end = max(header_end, node.end_lineno or node.lineno)
                continue
            break
        body = "".join(source.splitlines(keepends=True)[header_end:]).strip("\n")
        if body:
            bodies.append(body)

    parts = [
        '"""Domain-independent RDF graph state and Turtle serialization."""',
        "",
        "from __future__ import annotations",
        "",
        *collected_imports,
        "",
        "",
        "\n\n\n".join(bodies),
        "",
    ]
    return "\n".join(parts)


def write_fixed_rdf_runtime(destination: str | Path) -> Path:
    """Write the flattened runtime used by generated MCP packages."""
    path = Path(destination)
    if path.exists() and path.is_dir() or path.suffix != ".py":
        path = path / "_fixed_rdf_runtime.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(flatten_rdf_runtime(), encoding="utf-8", newline="\n")
    try:
        from src.extraction_prompt_generation.config.namespace import (
            write_runtime_namespace_sidecar,
        )

        write_runtime_namespace_sidecar(path.parent)
    except Exception:
        pass
    return path.resolve()


__all__ = [
    "RelationshipContractError",
    "abox_graph",
    "atomic_graph_transaction",
    "bind_parent_occurrence_argument",
    "bind_root_argument",
    "bound_enrichment_target_iri",
    "bound_root_iri",
    "central_memory_paths",
    "create_om2_quantity",
    "current_memory_scope",
    "document_memory_paths",
    "error_json",
    "error_result",
    "export_graph_result",
    "export_memory",
    "flatten_rdf_runtime",
    "init_memory",
    "initialize_retained_graph",
    "load_central_reuse_memory",
    "load_document_reuse_memory",
    "load_from_turtle_file",
    "new_graph",
    "package_datatype_capabilities",
    "package_entity_capabilities",
    "package_om2_quantity_creator",
    "package_ordered_entity_capabilities",
    "package_relationship_capabilities",
    "prepare_graph_for_export",
    "publish_reusable_entities_to_central_memory",
    "publish_reusable_entities_to_document_memory",
    "register_central_reuse_authorization",
    "register_semantic_rejection",
    "reset_graph",
    "reset_retained_graph",
    "resolve_case_dirname",
    "resolve_semantic_skip",
    "result_json",
    "retained_graph",
    "safe_filename_component",
    "sanitize_tool_text",
    "scoped_memory_paths",
    "serialize_turtle",
    "success_json",
    "success_result",
    "wrap_public_tool",
    "write_fixed_rdf_runtime",
]
