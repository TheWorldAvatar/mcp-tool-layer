# KG building

Pipeline KG and OntoLogX both live here. Extraction, PDF conversion, and
MCP compilation stay outside this package.

Python imports cannot use a space, so the folder is `src/kg_building`.

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

`python -m src.kg_building.ontologx --protocol generic-strict` is the locked
1:1 CLI. For PDFs, frozen MCP packs, and a one-click extract → Pipeline → OX
path, see [docs/ONE_CLICK_RUN.md](../../docs/ONE_CLICK_RUN.md). Do not pass
`--from-main-run` together with `--protocol`.


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
  --protocol generic-strict `
  --hint-runs scenarios/mops/runs/<pipeline_run> `
  --hash 0c57bac8 `
  --out-dir scenarios/mops/runs/ox_replay `
  --score
```

`--scorer-repo` is read-only. Isolated convert/score output goes under
`--out-dir/merged` and `--out-dir/scores` (chemicals, steps,
characterisation, CBU). Do not copy the 4k-line scorers into this repo.

MOP derivation is a post-publish hook. OX only writes
`ontomops_extension_*.ttl`. If `src.agents.mops.cbu_derivation` is not
in this clone, the hook records a skip.

## Extraction vs KG attribution

After convert/score, split official FNs by whether the ledger already
stated the fact (`openai/gpt-5.6-sol`, same model as steps synonyms):

```powershell
python -m src.kg_building.attribution `
  --pred-root scenarios/mops/runs/<RUN>/merged `
  --hint-runs scenarios/mops/runs/<PIPE_RUN> `
  --hash 0c57bac8 `
  --out-dir scenarios/mops/runs/<RUN>/attribution
```

One LLM call per paper: FNs are packed together; FPs stay on the
fingerprint heuristic (they do not move counterfactual F1).
`--heuristic-only` skips the LLM. Extraction counterfactual F1 is the
official score after promoting ledger-supported FNs to TP.
