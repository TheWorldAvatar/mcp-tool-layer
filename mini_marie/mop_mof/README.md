# MOP/MOF — metal-organic frameworks & polyhedra

**mop_mof** covers MOF corpus queries and local MOP synthesis TWAs. These are separate from Zaha (buildings) and Marie (chemistry Blazegraph).

| Package | MCP server | Data |
|---------|------------|------|
| `mof/` | `mof-twa` | Remote OntoMOFs SPARQL (~850k MOFs) |
| `mops/` | `twa-mops` | Local merged TTL (`evaluation/data/merged_tll`) |

Also includes `marie_agent.py` (LLM agent for MOP synthesis) and `webapp/` (Flask UI).

## Quick start

```bash
python -m mini_marie.mop_mof.mof.main
python -m mini_marie.mop_mof.mops.main
python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ06_TOPOLOGY_ZIF8
```

Config: `configs/mof_twa.json`, `configs/marie_twa.json`
