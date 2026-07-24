# MOPs TWA — execute → record → replay at scale

Same pattern as `mini_marie/zaha/twa_city/WORKFLOW_SCALING.md` (local RDF graph, not remote SPARQL).

| Phase | Entry | LIMIT | Purpose |
|-------|--------|-------|---------|
| Online probe | MCP `run_workflow_online` | **10** on `list_mops` / `list_syntheses` |
| Record | `workflow_runs/*.json` | |
| Offline replay | `replay_workflow_offline` | up to **50000** (local graph size) |

## Example workflows

- `list_mops_catalog` — sample MOP labels online, full catalog offline
- `synthesis_profile` — lookup + recipe + steps for `VMOP-17` (tool steps, not limit-scaled)

## MCP tools

Reload `mops-twa` in Cursor after code changes.

```bash
python -m mini_marie.mop_mof.mops.run_workflow --workflow list_mops_catalog
python -m mini_marie.mop_mof.mops.replay_workflow --recording mini_marie/mop_mof/mops/workflow_runs/<file>.json
python -m mini_marie.mop_mof.mops.test_workflow_scaling
```

Requires `evaluation/data/merged_tll` for local graph load.
