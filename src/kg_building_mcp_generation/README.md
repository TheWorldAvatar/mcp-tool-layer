# KG-building MCP generation

Compile public occurrence MCP tools (`create_*` / `link_*`) from a T-Box plus
one human domain config. Scripts are compiled. Leftover facets are rare; the
current four domains usually have `llm_judged_count=0`.

Top-class / extension-focus planning still uses GPT-5 through the existing
`extraction_prompt_generation` compiler. This package does not modify that
compiler, and it does not use `--llm-generation`.

## Invocation

```
python -m src.kg_building_mcp_generation
python -m src.kg_building_mcp_generation ontosynthesis --output-root generated
```

Arguments: ontology name (optional), `--output-root`, `--stage`,
`--selected-top-entity`, `--json`. Omit the ontology to compile
ontosynthesis → ontomops → ontospecies → medical into one output root.

`--stage` default is `all`. `context` stops after planning and occurrence
compile. `scripts` rewrites the MCP package. `prompts` rewrites deterministic
prompt templates and SPARQL.

## Packages

| Folder | Role |
| --- | --- |
| `surface/` | Discover candidates, compile units, leftover-facet judge |
| `emit/` | Creation modules, occurrence operations, prompts, launchers |
| `overlay/` | Compact OM-2 lexeme overlay: compiled facets take source text on `create_*`; unlisted measurements still mint via `create_om2_quantity` |

Occurrence emit uses the overlay path. Listed quantity arguments reject invalid lexemes (`skippable` false) instead of omitting the facet. This does not rewrite locked `generated/current.json` packs; regenerate a pack to pick up the overlay.

Root modules: `cli.py`, `compile_hook.py`, `__main__.py`.
