You are an expert Python developer specializing in RDF/semantic web and MCP server development.

## Task

Generate `{ontology_name}_creation_relationships.py` for the ontology `{ontology_name}`.

This module provides relationship / add_* functions used by the FastMCP server wrapper.

## Inputs

You will be given a concise ontology structure markdown that includes:
- Namespace URI
- Classes
- Object properties (with domain/range)
- Any external ranges (e.g., OM-2 quantities)
- OM-2 Unit Inventory (T-Box derived), if present
- A machine-readable per-property `relationship_tool_contracts` section compiled from the T-Box

## Hard Requirements

1. Must compile: code must be valid Python and importable.
2. Imports:
   - Use rdflib types (Graph, URIRef, RDF, RDFS, Literal as RDFLiteral).
   - Use locked_graph from `..universal_utils`.
   - Import namespaces + guard + formatting helpers from `.{ontology_name}_creation_base`.
   - If OM-2 quantities are relevant, import OM2_UNIT_MAP and `_find_or_create_om2_quantity` from base; do NOT duplicate per-file unit maps.
   - Add: `from typing import Annotated` and `from pydantic import Field` when you annotate parameters with Annotated/Field.
3. Relationship functions:
   - For each object property in the ontology, generate a deterministic `add_*` function that:
     - accepts subject_iri: str and object_iri: Annotated[str, Field(description=...)]
     - MUST NOT accept a graph/g parameter in the public add_* signature
     - validates that subject/object exist and have compatible rdf:type according to the T-Box (best-effort)
     - adds exactly one triple `(subject, predicate, object)` (avoid duplicates where easy)
     - returns a JSON envelope using `_format_success_json` / `_format_error`
     - manages graph persistence internally via `with locked_graph() as g:`
   - If you use a private helper such as `_add_relationship`, that private helper MAY accept `graph: Graph`.
   - `_format_success_json` must be called as `_format_success_json(iri, message, created=...)`.
   - NEVER call `_format_success_json({ ... })` with a dict positional argument.
4. Parameter annotation + docstring contract (concise, machine-readable intent):
   - Treat each entry in `relationship_tool_contracts` as authoritative. Do not infer ranges, creator availability, or target handling from domain knowledge.
   - The `object_iri` parameter MUST be annotated as `Annotated[str, Field(description=...)]`.
   - The Field(description) MUST include the exact phrases `absolute IRI` and `never a label/name/literal/plain text`.
   - Mention only the exact range locals and `creator_tools` listed by the corresponding contract entry.
   - When `creator_tools` is non-empty, require the subject IRI returned by a successful call to the listed creator tool.
   - When an external range is allowed, require an existing absolute IRI for that range.
   - When `creator_tools` is empty, the description MUST NOT contain a `create_*` tool name.
   - Each public `add_*` MUST include a concise tool docstring that states the same core contract (concise; do not paste long T-Box/domain prose).
5. Ergonomic helpers (ONLY when the ontology indicates they are relevant):
  - Provide stable-name wrappers for common workflows by delegating to the ontology-derived `add_*` functions (do not re-implement the triple logic).
  - If the ontology has an ordered-membership pattern OR if any mutation accepts an order-like input
    (e.g., `order`, `sequence_index`, `position`), enforce order consistency at mutation time.
    - Reject non-positive / non-integer orders.
    - Reject duplicates within the same container.
    - Reject non-contiguous sequences (exactly `1..max(order)` after the operation).
    - This enforcement may be implemented as a private helper.

  Non-domain-specific example (ordered membership enforcement):
  ```python
  def _enforce_contiguous_orders(
      *,
      g: Graph,
      container: URIRef,
      has_member: URIRef,
      has_order: URIRef,
      new_order: int,
  ) -> str | None:
      # Return None if OK; otherwise return an error message.
      existing: set[int] = set()
      for m in g.objects(container, has_member):
          for o in g.objects(m, has_order):
              try:
                  existing.add(int(str(o)))
              except Exception:
                  return f"Unparsable order value on {m}: {o}"
      if new_order in existing:
          return f"Duplicate order {new_order} in container {container}"
      candidate = sorted(existing | {new_order})
      expected = list(range(1, (max(candidate) if candidate else 0) + 1))
      if candidate != expected:
          return f"Non-contiguous orders: got {candidate}, expected {expected}"
      return None
  ```
6. No placeholders: no "..." and no "similar for other properties".

## Output

Return ONLY the Python code for `{ontology_name}_creation_relationships.py` as plain text. No explanations.

