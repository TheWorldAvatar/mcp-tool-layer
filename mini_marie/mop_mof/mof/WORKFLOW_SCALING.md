# MOF TWA — execute → record → replay at scale

Same pattern as `mini_marie/zaha/twa_city/WORKFLOW_SCALING.md`.

| Phase | Entry | LIMIT | Purpose |
|-------|--------|-------|---------|
| Online probe | MCP `run_workflow_online` / `run_workflow.py` | **10** | Validate workflow; compact TSV |
| Record | `workflow_runs/*.json` | Stores SPARQL + variables |
| Offline replay | `replay_workflow_offline` / `replay_workflow.py` | up to **500000** | Full rank/list results |

## Example workflows (`workflows/`)

- `top10_tobassco_co2_properties` — rank CO2 → top-N → property sheet
- `dominant_topology_leader` — topology counts → branch rank → properties
- `tobassco_co2_corpus_share` — corpus counts + Tobassco stats (fixed-limit tools)

## MCP tools

Reload `mof-twa` in Cursor after code changes.

```bash
python -m mini_marie.mop_mof.mof.run_workflow --workflow top10_tobassco_co2_properties --online-limit 10
python -m mini_marie.mop_mof.mof.replay_workflow --recording mini_marie/mop_mof/mof/workflow_runs/<file>.json
python -m mini_marie.mop_mof.mof.test_workflow_scaling
```
