# Docker — mini_marie MCP + cached workflows

One image runs **mof-twa** and **twa-city** MCP servers (stdio) and CLI benchmarks/E2E tests. SQLite caches persist in the `mini_marie_data` volume.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- Outbound HTTPS to SPARQL endpoints (OntoMOFs, Bremen/Kaiserslautern Ontop)

## Build

```bash
docker compose build
```

## Persistent cache

| Path in container | Content |
|-------------------|---------|
| `/app/data/mini_marie_cache/mof_competency/` | MOF competency SQLite |
| `/app/data/mini_marie_cache/twa_city/` | City workflow SQLite |

Volume name: `mini_marie_data` (survives `docker compose down`).

Copy cache out:

```bash
docker run --rm -v mini_marie_data:/data -v "%cd%/data:/out" alpine cp -r /data/mini_marie_cache /out/
```

## MCP in Cursor

StrReplace `REPO_ROOT` with the **project root** (directory containing `mini_marie/`, `docker-compose.yml`, and `configs/`).

See [mini_marie/docs/DEPLOYMENT.md](../mini_marie/docs/DEPLOYMENT.md) and [mini_marie/docs/CACHE_STARTUP.md](../mini_marie/docs/CACHE_STARTUP.md).

Reload MCP after `docker compose build`.

## CLI — arbitrary commands

```bash
docker compose --profile cli run --rm workflow-cli python -m mini_marie.zaha.twa_city.run_workflow --workflow top10_buildings_locations_kl

docker compose --profile cli run --rm workflow-cli python -m mini_marie.zaha.twa_city.replay_workflow --workflow top10_buildings_locations_kl

docker compose --profile cli run --rm workflow-cli python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ06_TOPOLOGY_ZIF8
```

## Benchmarks (profile `bench`)

```bash
# Complex multi-step replay (cold vs warm)
docker compose --profile bench run --rm bench-city-complex
docker compose --profile bench run --rm bench-mof-complex

# Full E2E suites (slow; need network)
docker compose --profile bench run --rm bench-city-e2e
docker compose --profile bench run --rm bench-mof-e2e
```

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `MINI_MARIE_DATA_DIR` | `/app/data` | Cache + persistent data root |
| `PYTHONPATH` | `/app` | Package imports |

## Architecture

```text
┌─────────────────────────────────────────┐
│  image: mini-marie:latest               │
│  ├─ mof-twa      (stdio MCP)            │
│  ├─ twa-city     (stdio MCP)            │
│  └─ workflow-cli / bench-* (one-shot)   │
│         │                               │
│         ▼                               │
│  volume: mini_marie_data → SQLite cache │
└─────────────────────────────────────────┘
         │ HTTPS
         ▼
   Remote SPARQL endpoints
```

## Competency Test GUI (profile `gui`)

```bash
docker compose --profile gui up competency-gui
# http://localhost:8501
```

## KGQA ReAct Agent GUI (profile `kgqa`)

Requires `.env` with `REMOTE_BASE_URL` and `REMOTE_API_KEY` for the LLM.

```bash
docker compose --profile kgqa up kgqa-gui
# http://localhost:8502
```

Warm caches before expecting offline replay to return full results:

```bash
docker compose --profile cli run --rm workflow-cli python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive
docker compose --profile cli run --rm workflow-cli python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive
```

Incremental warm (recommended): see [mini_marie/docs/CACHE_STARTUP.md](../mini_marie/docs/CACHE_STARTUP.md).

| Variable | Purpose |
|----------|---------|
| `REMOTE_BASE_URL` | OpenAI-compatible API base for ReAct agent |
| `REMOTE_API_KEY` | API key for LLM calls |
| `MINI_MARIE_DATA_DIR` | Cache root (default `/app/data`) |

## Dev: live-mount code

```bash
docker compose --profile cli run --rm \
  -v ./mini_marie:/app/mini_marie:ro \
  workflow-cli python -m mini_marie.zaha.twa_city.test_city_cache
```
