# Human competency questions → `mof-twa` MCP tools

Source: [mini_marie/data/CompetencyQs.md](../data/CompetencyQs.md)

## Covered by dedicated MCP tools (smoke-tested)

| Q | Question (short) | MCP tool |
|---|------------------|----------|
| 1 | UiO-66 PLD avg/variance | `get_pld_stats_by_mof_name` |
| 2 | Zn MOFs and sources | `get_mofs_by_metal` (`list_sources=true`) |
| 3 | Experimental Cu count | `get_mofs_by_metal` (`metal=Cu`, `experimental_only=true`, `count_only=true`) |
| 4 | UiO-66 synthesis routes | `get_synthesis_by_mof_name` |
| 5 | DUT-67 space group | `get_mof_identity_by_name` |
| 6 | ZIF-8 topology | `get_mof_identity_by_name` |
| 7 | Same topology as ZIF-8 | `get_mofs_with_same_topology_as` (`count_only` for totals) |
| 8 | HKUST-1 linker | `get_linkers_by_mof_name` |
| 9 | Hypothetical MIL-53 topology count | `count_hypothetical_same_topology_as` |
| 10 | Experimental pcu count | `count_experimental_by_topology` |
| 11 | HKUST-1 max LCD | `get_pore_metrics_by_mof_name` |
| 12 | GSA ≥ 2500 | `get_mofs_by_gsa_min` |
| 13 | MIL-101 publication | `get_publications_by_mof_name` |
| 14 | MIL-53 ASA | `get_asa_by_mof_name` |
| 15 | DUT-67 density | `get_pore_metrics_by_mof_name` |
| 16 | LCD < 20 Å | `get_mofs_by_lcd_max` |
| 17 | Top solvents (excl. DMF/DMA) | `get_top_synthesis_solvents` |
| D1 | Zr + carboxylate linker | `get_mofs_by_metal_and_linker` |
| D2 | pcu topology variants | `get_mofs_by_topology_all_sources` |
| D3a | ZIF-8 refcodes | `get_refcodes_by_mof_name` |
| D4 | Aqueous low-temp synthesis | `get_aqueous_low_temp_syntheses` |
| D5 | Water stable | `get_water_stable_mofs` |
| D6 | Thermal stable | `get_thermal_stable_mofs` |
| D7 | High binary gas uptake | `get_high_binary_gas_uptake_mofs` |

## Heavy / multi-join questions (run via batch eval, not single MCP call)

Use `python -m mini_marie.mop_mof.mof.run_competency_eval` to execute raw SPARQL from the markdown and record pass/fail/timeouts.

Examples: David Q3b (ZIF-8 refcode UNION synthesis), Q8 pred vs exp adsorption, Q10 full MOF-5 profile, stability cross-joins, ARC_MOF UNION adsorption blocks.

## Fixes applied to source questions

- Q4: `hascsd_refcode` → `hasCsdRefcode` (correct OntoMOFs predicate).

## Cached probe / offline replay

See [COMPETENCY_CACHING.md](COMPETENCY_CACHING.md). Atomics are stored in SQLite (`data/mini_marie_cache/mof_competency/`) with facet indexes for fast local joins.

```bash
python -m mini_marie.mop_mof.mof.test_competency_cache
python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ01_PLD_UIO66
python -m mini_marie.mop_mof.mof.replay_competency_offline --workflow CQ07_SAME_TOPO_ZIF8
```

## How to test

```bash
python -m mini_marie.mop_mof.mof.test_competency_mcp
python -m mini_marie.mop_mof.mof.run_competency_eval   # full SPARQL batch (slow)
```

Reload **`mof-twa`** in Cursor MCP after pulling these changes.
