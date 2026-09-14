# KG-building MCP generation (v2)

Copy of `src/kg_building_mcp_generation` with one extra structural rule:
a public `create_*` may omit `parent_iri` for an extension adopted focus,
or when a unique incoming parent compiles `parent_iri`. If that selection
fails *and* the class is already a nested fresh dependent of another
public owner, it is not emitted as a public heading; the nested arguments
stay. Unnested parentless headings (no unique parent, not nested) stay
public so other MCP tools are unchanged.

Do not point `generated/current.json` at packs from this generator.

```
python -m src.kg_building_mcp_generation_v2 --from-existing-pack generated/runs/<frozen> --output-root generated/runs/<new>
```

Occurrence compile is otherwise the v1 surface: candidates, unique parent
links, nested fresh dependents, and leftover linkers.

OM-2 runtime is emitted with that same script slice: T-Box +
`om2_unit_aliases.json` → `compile_quantity_surface` → generic compact matcher
in `_fixed_om2_runtime.py` (not a copy of `om2.py`). Labels must be
`<number> <unit>`. Rebuilds that only rewrite scripts still get a class-aware
matcher.
