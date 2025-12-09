# Top Entity KG Building Pipeline Step

This module builds a knowledge graph from extracted top-level entities using an LLM agent with MCP (Model Context Protocol) tools.

## Overview

The top entity KG building step:
1. Loads the meta task configuration to determine ontology and MCP settings
2. Loads the extraction hints from the `top_entity_extraction` step
3. Loads the KG building prompt for the ontology
4. Runs an LLM agent with MCP tools to build the knowledge graph
5. Saves the output TTL as `iteration_1.ttl`

## Configuration

### Meta Configuration

The main ontology and MCP settings are specified in `configs/meta_task_config.json/meta_task_config.json`:

```json
{
  "ontologies": {
    "main": {
      "name": "ontosynthesis",
      "description": "The ontology for the synthesis of chemical compounds.",
      "ttl_file": "data/ontologies/ontosynthesis.ttl",
      "complex_pipeline": true,
      "mcp_set_name": "run_created_mcp.json",
      "mcp_list": ["llm_created_mcp"]
    }
  }
}
```

Key fields:
- `mcp_set_name`: Name of the MCP configuration file
- `mcp_list`: List of MCP tool names to use with the agent

### KG Building Prompt

The KG building prompt is stored in:
```
ai_generated_contents/prompts/<ontology_name>/KG_BUILDING_ITER_1.md
```

For example: `ai_generated_contents/prompts/ontosynthesis/KG_BUILDING_ITER_1.md`

This prompt contains:
- Global rules for MCP tool usage
- Task-specific instructions for creating ontology instances
- Termination conditions

### SPARQL Query

The SPARQL query for parsing top entities from the TTL output is stored in:
```
ai_generated_contents/sparqls/<ontology_name>/top_entity_parsing.sparql
```

For example: `ai_generated_contents/sparqls/ontosynthesis/top_entity_parsing.sparql`

## Usage

### As Part of Pipeline

Add to `configs/pipeline.json`:

```json
{
  "steps": [
    "pdf_conversion",
    "section_classification",
    "stitching",
    "top_entity_extraction",
    "top_entity_kg_building"
  ]
}
```

### Standalone

```bash
python -m src.pipelines.top_entity_kg_building.build <doi_hash>
```

## Input/Output

### Input
- `data/<doi_hash>/top_entities.txt` - Extracted top-level entities from previous step
- `configs/meta_task_config.json/meta_task_config.json` - Meta configuration
- `ai_generated_contents/prompts/<ontology>/KG_BUILDING_ITER_1.md` - KG building prompt

### Output
- `data/<doi_hash>/iteration_1.ttl` - Knowledge graph in Turtle format
- `data/<doi_hash>/kg_building/iter1_response.md` - Agent response log

## Implementation Details

### Agent Configuration
- **Model**: gpt-4o
- **Temperature**: 0.1
- **Top_p**: 0.1
- **Recursion Limit**: 600
- **MCP Tools**: Loaded from meta configuration (e.g., `["llm_created_mcp"]`)
- **MCP Set**: Loaded from meta configuration (e.g., `run_created_mcp.json`)

### MCP Tools

The agent uses MCP (Model Context Protocol) tools to interact with the knowledge graph:
- Create ontology instances (e.g., `ChemicalSynthesis`)
- Check for existing entities
- Link entities with relationships
- Export the final knowledge graph to TTL format

### Workflow

1. **Load Configuration**: Read meta task config to get ontology name and MCP settings
2. **Load Hints**: Read `top_entities.txt` from the previous extraction step
3. **Load Prompt**: Read the KG building prompt template
4. **Format Prompt**: Replace `{paper_content}` with hints and `{doi}` with DOI hash
5. **Create Agent**: Initialize `BaseAgent` with MCP tools and configuration
6. **Run Agent**: Execute the agent with the formatted prompt
7. **Save Response**: Write agent response to `kg_building/iter1_response.md`
8. **Copy TTL**: Copy `output.ttl` or `output_top.ttl` to `iteration_1.ttl`

### Skipping Logic

The step is skipped if `iteration_1.ttl` already exists in the DOI folder.

## Error Handling

- Missing extraction hints → Fail with error
- Missing KG building prompt → Fail with error
- Agent execution failure → Fail with exception
- No output TTL produced → Warning (but step continues)

## Example

For DOI hash `0c57bac8`:

**Input** (`data/0c57bac8/top_entities.txt`):
```
ChemicalSynthesis — UMC-1
ChemicalSynthesis — VMOP-18
ChemicalSynthesis — MOF-5
```

**Output** (`data/0c57bac8/iteration_1.ttl`):
```turtle
@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<http://example.org/synthesis/abc123> a ontosyn:ChemicalSynthesis ;
    rdfs:label "UMC-1" ;
    ontosyn:retrievedFrom <http://dx.doi.org/10.1021/...> .
...
```

## Dependencies

- `models.BaseAgent`: LLM agent with MCP tool support
- `models.ModelConfig`: Model configuration
- `src.utils.global_logger`: Logging utilities

