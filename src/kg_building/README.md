# KG building

Pipeline KG and OntoLogX both live here. Extraction, PDF conversion, and
MCP compilation stay outside this package.

Python imports cannot use a space, so the folder is `src/kg_building`
(the destination you asked for as `src/kg building`).

## Pipeline (generated MCP)

`pipeline/` is the no-revision KG path. The agent calls occurrence
`create_*` / `link_*` tools from a generated package
(`generated/runs/<id>/` or `TWA_GENERATED_ARTIFACT_ROOT`). There is no
KG-building judge, hint revision, or structural retry loop.

`src.extraction_runtime` step modules are thin wrappers:

```text
main_kg_building        → src.kg_building.pipeline.main_kg.run
extensions_kg_building  → src.kg_building.pipeline.extension
```

## OntoLogX

`ontologx/` is the structured-output baseline. The default profile is
**generic graph rules + strict no-prompt** (same family as the OntoSyn
official no-contract / strict no-prompt work). Extension layers do not
get a TBox handbook or `EXTENSION_CONTRACT` recipes.

After each extension parse, `splice.py` attaches facts onto the inherited
main graph: one Species output, one seeded MOP, formula/CCDC as value
nodes. `publish_runtime.py` writes a Pipeline-shaped folder so the four
scorers (chemicals, steps, characterisation, CBU) and mop derivation can
run. OX does not derive CBUs; `--mop-derivation` calls the existing v2
derivation step.

```powershell
python -m src.kg_building.ontologx `
  --from-main-run <pipe_run> `
  --hint-runs <pipe_run> `
  --extension ontospecies --extension ontomops `
  --hash 0c57bac8 `
  --out-dir scenarios/mops/runs/ox_ext1 `
  --mop-derivation `
  --score --scorer-repo C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction
```

`--scorer-repo` is read-only. Isolated convert/score output goes under
`--out-dir/merged` and `--out-dir/scores` (chemicals, steps,
characterisation, CBU). Do not copy the 4k-line scorers into this repo.

MOP derivation is a post-publish hook. OX only writes
`ontomops_extension_*.ttl`. If `src.agents.mops.cbu_derivation` is not
in this clone, the hook records a skip.
