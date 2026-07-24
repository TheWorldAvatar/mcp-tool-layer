# Zaha — buildings & city knowledge graphs

**Zaha** covers built-environment TWAs: European city buildings and the Singapore legacy stack.

| Package | MCP server | Data |
|---------|------------|------|
| `twa_city/` | `twa-city` | Bremen + Kaiserslautern building heights, locations, usage |
| `sg_old/` | `sg-old` | Singapore buildings, land-use, carpark, dispersion/emissions |

## Quick start

```bash
python -m mini_marie.zaha.twa_city.main
python -m mini_marie.zaha.sg_old.main
python -m mini_marie.zaha.twa_city.run_workflow --workflow top10_buildings_locations_bremen
python -m mini_marie.zaha.probe_zaha_stack
```

Config: `configs/twa_city.json`, `configs/sg_old.json`

**Note:** MOF and MOP stacks live under `mop_mof/`, not here.
