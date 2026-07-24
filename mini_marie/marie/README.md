# Marie — chemistry competency demo

**Marie** is the [Marie chemistry demo](https://theworldavatar.io/demos/marie) runtime: Blazegraph namespaces, MQ1–48 competency questions, probe/warm/replay caching.

| Package | MCP servers | Namespaces |
|---------|-------------|------------|
| `chemistry/` | `chemistry-ontospecies`, `chemistry-ontokin`, `chemistry-ontocompchem`, `chemistry-ontozeolite`, `chemistry-ontoprovenance`, `chemistry-ontopesscan`, `chemistry-ontomops` (routing stub) | Remote Blazegraph |

## Quick start

```bash
python -m mini_marie.marie.chemistry.ontospecies.main
python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive
python -m mini_marie.marie.chemistry.cache_status --coverage
python -m mini_marie.marie.probe_marie_demo
```

MQ49–61 (MOP geometry) are routed to `mop_mof/`, not chemistry Blazegraph.
