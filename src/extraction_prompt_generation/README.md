# Extraction prompt generation

The default model is `gpt-5`. Domain config `models.*` can name other
models. The configured model writes full extraction prompts from a T-Box
plus one human domain config. Companion `*.materializable.inc` files,
SPARQL, iteration plans, and generation contracts are compiled, not
authored.

MCP scripts are produced by a separate chain. This package still compiles the
shared RDF/OM-2 runtime and validates generated script packages when those
artifacts are present.

## Pipeline

```
cli / __main__
  → pipeline.experiment
      → compile.artifact_compiler   (T-Box + config → AgenticGenerationContext)
          → planning                (top-class / extension focus)
          → compile.reuse_judgment  (10/10 class-reuse policy)
          → compile.iteration_plan  (slot ownership + semantic scope)
      → generate.extraction_prompts.slots   (empty .md + compiled .inc)
      → generate.extraction_prompts.author  (exact-edits)
      → validate.report                     (mechanical + semantic gates)
      → pipeline.runtime_support            (iterations.json + flattened RDF)
```

`--stage reuse` stops after planning and the 10/10 class-reuse generation.
`--stage context` writes compile + planning + reuse. `--stage prompts` / `all`
also author prompts and compile SPARQL.

Set `models.prompt_generation`, `models.top_entity_planning` (or
`models.extension_focus_planning`), `models.reuse_judgment`, and related
keys in the domain JSON to use a model other than `gpt-5`. CLI `--model`
overrides those generation-facing keys for one batch without editing the
JSON. Each batch is `generated/runs/<timestamp>_<tag>/`.

## Packages

| Folder | Role |
| --- | --- |
| [`config/`](config/README.md) | Load and reject-check the human domain JSON |
| [`tbox/`](tbox/README.md) | Parse TTL; derive property and runtime contracts |
| [`planning/`](planning/README.md) | Root/focus planning; deterministic slot ownership |
| [`compile/`](compile/README.md) | Contracts, reuse policy, iteration plan, SPARQL, context |
| [`generate/`](generate/README.md) | Exact-edits authoring of extraction prompts |
| [`pipeline/`](pipeline/README.md) | CLI-facing orchestration and deterministic scaffolds |
| [`llm/`](llm/README.md) | Remote model calls, exact-edits, invocation journal |
| [`validate/`](validate/README.md) | Mechanical gates on prompts and generated scripts |
| [`runtime_support/`](runtime_support/README.md) | Human RDF/OM-2 libraries copied into generated packages |

Root modules:

| File | Role |
| --- | --- |
| `cli.py` | Argument parsing and the `python -m` entry |
| `__main__.py` | Delegates to `cli.main` |
| `paths.py` | Repository-root path resolution (cwd-independent) |
