# Main KG Building Module

## Overview
This module handles knowledge graph (KG) building for iterations 2, 3, and 4 of the ontology extraction pipeline. It uses `BaseAgent` with MCP tools to convert extraction hints into TTL (Turtle) knowledge graph files.

## Configuration
The module reads its configuration from `ai_generated_contents/iterations/ontosynthesis/iterations.json`:

- **`kg_building_prompt`**: Path to the KG building prompt template (Markdown file)
- **`mcp_set_name`**: MCP configuration file to use (e.g., `run_created_mcp.json`)
- **`mcp_tools`**: List of MCP tools to enable (e.g., `["llm_created_mcp"]`)

### Example Configuration (Iteration 3)
```json
{
  "iteration_number": 3,
  "kg_building_prompt": "ai_generated_contents/prompts/ontosynthesis/KG_BUILDING_ITER_3.md",
  "mcp_set_name": "run_created_mcp.json",
  "mcp_tools": ["llm_created_mcp"]
}
```

## Workflow

### For Each Iteration (2, 3, 4):
1. **Load Configuration**: Read iteration config from `iterations.json`
2. **Load Top Entities**: Read `iter1_top_entities.json` to get all entities
3. **For Each Entity**:
   - Check if KG building already completed (skip if exists)
   - Load extraction hints from `mcp_run/iter{N}_hints_{entity_safe}.txt`
   - Load KG building prompt template
   - Replace placeholders: `{doi}`, `{entity_label}`, `{entity_uri}`, `{paper_content}`
   - Write global state for MCP server
   - Run `BaseAgent` with specified MCP tools
   - Save response to `responses/iter{N}_kg_building/{entity_safe}.md`
   - Copy `output.ttl` to `intermediate_ttl_files/iteration_{N}_{entity_safe}.ttl`

## Input Files
- **Top Entities**: `data/{doi_hash}/mcp_run/iter1_top_entities.json`
- **Extraction Hints**: `data/{doi_hash}/mcp_run/iter{N}_hints_{entity_safe}.txt`
- **KG Building Prompts**: `ai_generated_contents/prompts/ontosynthesis/KG_BUILDING_ITER_{N}.md`
- **Iterations Config**: `ai_generated_contents/iterations/ontosynthesis/iterations.json`

## Output Files
- **Prompts**: `data/{doi_hash}/prompts/iter{N}_kg_building/{entity_safe}.md`
- **Responses**: `data/{doi_hash}/responses/iter{N}_kg_building/{entity_safe}.md`
- **TTL Files**: `data/{doi_hash}/intermediate_ttl_files/iteration_{N}_{entity_safe}.ttl`
- **Global State**: `data/global_state.json` (for MCP server)

## Prompt Placeholders
KG building prompts support the following placeholders:
- `{doi}`: DOI hash of the paper
- `{entity_label}`: Entity label (e.g., "UMC-1")
- `{entity_uri}`: Entity URI from the knowledge graph
- `{paper_content}`: Extraction hints content

## Agent Configuration
- **Model**: `gpt-4o` (default for KG building)
- **Temperature**: 0.1
- **Top_p**: 0.1
- **Recursion Limit**: 600
- **Max Retries**: 3 (with progressive backoff: 5s, 10s, 15s)

## MCP Tools
The module uses MCP (Model Context Protocol) tools for knowledge graph operations:
- **`llm_created_mcp`**: Main KG building tool (creates entities, relationships, exports TTL)
- Configuration loaded from `mcp_set_name` (e.g., `run_created_mcp.json`)

## Global State Management
The module writes global state to `data/global_state.json` before each agent run:
```json
{
  "doi": "0c57bac8",
  "top_level_entity_name": "UMC-1",
  "top_level_entity_iri": "https://www.theworldavatar.com/kg/OntoSyn/..."
}
```
This allows the MCP server to access paper and entity context.

## Idempotency
The module is idempotent:
- Skips entities where both response file and TTL file already exist
- Safe to re-run without duplicating work

## Error Handling
- **Retry Logic**: Up to 3 attempts with progressive backoff
- **Orphan Entity Check**: Automatically added to all prompts
- **Graceful Failure**: Logs errors and continues with next entity

## Usage

### As Part of Pipeline
```python
from src.pipelines.main_kg_building import run_step

run_step(doi_hash="0c57bac8", data_dir="data", project_root=".")
```

### Standalone
```bash
python -m src.pipelines.main_kg_building.build 0c57bac8
```

## Dependencies
- `models.BaseAgent`: LLM agent with MCP tool support
- `models.ModelConfig`: Model configuration
- `filelock`: For atomic global state writes
- `asyncio`: For async agent execution

## Notes
- **Iteration 1 KG Building**: Handled by `top_entity_kg_building` module, not this one
- **Iteration 2**: Uses `chemistry.json` MCP set for extraction, `run_created_mcp.json` for KG building
- **Iterations 3 & 4**: Use `run_created_mcp.json` for both extraction and KG building
- **TTL Output**: Each entity gets its own TTL file in `intermediate_ttl_files/`

