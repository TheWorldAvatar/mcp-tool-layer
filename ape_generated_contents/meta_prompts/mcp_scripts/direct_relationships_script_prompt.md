You are an expert Python developer specializing in RDF/semantic web and MCP server development.

## Task

Generate `{ontology_name}_creation_relationships.py` for the ontology `{ontology_name}`.

This module provides **relationship / add_*** functions used by the FastMCP server wrapper.

## Inputs

You will be given a concise ontology structure markdown that includes:
- Namespace URI
- Classes
- Object properties (with domain/range)
- Any external ranges (e.g., OM-2 quantities)
- OM-2 Unit Inventory (T-Box derived), if present

## Hard Requirements

1. **Must compile**: code must be valid Python and importable.
2. **Imports**:
   - Use `rdflib` types (`Graph`, `URIRef`, `RDF`, `RDFS`, `Literal as RDFLiteral`).
   - Use `locked_graph` from `..universal_utils`.
   - Import namespaces + guard + formatting helpers from `.{ontology_name}_creation_base`.
   - If OM-2 quantities are relevant, import `OM2_UNIT_MAP` and `_find_or_create_om2_quantity` (or equivalent) from base; do NOT duplicate per-file unit maps.
3. **Relationship functions**:
   - For each object property in the ontology, generate a deterministic `add_*` function that:
     - accepts subject IRI string, object IRI string (or label parameters if the ontology indicates label-driven creation)
     - validates that subject/object exist and have compatible rdf:type according to the T-Box (best-effort)
     - adds exactly one triple `(subject, predicate, object)` (avoid duplicates where easy)
     - returns a JSON envelope using the base formatting helpers (`_format_success_json` / `_format_error`)
4. **Ergonomic helpers** (ONLY when the ontology indicates they are relevant):
  - Provide stable-name wrappers for common user workflows, but implement them by delegating to the ontology-derived `add_*` functions you generated (do not re-implement the triple-adding logic).
  - If the ontology has a concept like “input” with a repeatable literal (e.g., multiple amounts/notes/identifiers), include a helper that **appends + deduplicates** a literal value.
  - If the ontology has an ordered-membership pattern OR if any mutation function you generate accepts an **order-like input**
    (e.g., a parameter named `order`, `sequence_index`, `position`, or similar),
    you MUST enforce order consistency **at mutation time** (when linking/adding), not only via a separate check tool.
    - Reject non-positive / non-integer orders.
    - Reject duplicates within the same container.
    - Reject non-contiguous sequences (must be exactly `1..max(order)` after the operation).
    - This enforcement may be implemented as a private helper inside this module.
    - If you also expose a report-only `check_and_report_order_consistency() -> str` in a separate checks module, that is fine, but it does NOT replace mutation-time enforcement.

  **Non-domain-specific example (ordered membership enforcement):**
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
                  return f"Unparsable order value on {{m}}: {{o}}"
      if new_order in existing:
          return f"Duplicate order {{new_order}} in container {{container}}"
      candidate = sorted(existing | {{new_order}})
      expected = list(range(1, (max(candidate) if candidate else 0) + 1))
      if candidate != expected:
          return f"Non-contiguous orders: got {{candidate}}, expected {{expected}}"
      return None
  ```
5. **No placeholders**: no "..." and no "similar for other properties".

## Output

Return ONLY the Python code for `{ontology_name}_creation_relationships.py` as plain text. No explanations.


