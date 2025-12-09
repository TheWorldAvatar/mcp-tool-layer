# Extensions Extractions Pipeline Step

## Overview

This pipeline step handles extraction and knowledge graph building for extension ontologies that complement the main OntoSynthesis ontology:

- **OntoMOPs**: Describes metal-organic polyhedra (MOPs) structures, including chemical building units, topology, and CCDC information
- **OntoSpecies**: Describes chemical species characterizations, including spectroscopic data, physical properties, and analytical results

## Process

For each extension ontology and each top-level entity:

1. **Content Extraction**: Uses an LLM to extract relevant information from the paper based on the extension ontology's T-Box
2. **A-Box Building**: Runs an agent with MCP tools to build the extension A-Box and link it to the OntoSynthesis A-Box

## Configuration

### Meta Task Configuration

`configs/meta_task/meta_task_config.json` defines the extension ontologies:

```json
{
  "ontologies": {
    "extensions": [
      {
        "name": "ontomops",
        "description": "The ontology for describing of metal-organic polyhedra.",
        "ttl_file": "data/ontologies/ontomops-subgraph.ttl",
        "complex_pipeline": false
      },
      {
        "name": "ontospecies",
        "description": "The ontology for describing the species of chemical compounds.",
        "ttl_file": "data/ontologies/ontospecies-subgraph.ttl",
        "complex_pipeline": false
      }
    ]
  }
}
```

### Iteration Configuration

Each extension has its own `iterations.json` file:

- `ai_generated_contents/iterations/ontomops/iterations.json`
- `ai_generated_contents/iterations/ontospecies/iterations.json`

These define:
- Extraction and extension prompts
- Model configuration
- MCP tools and settings
- Input/output file paths

## Inputs

- **Stitched Paper**: `data/{doi_hash}/{doi_hash}_stitched.md`
- **Top Entities**: `data/{doi_hash}/mcp_run/iter1_top_entities.json`
- **OntoSynthesis TTL**: `data/{doi_hash}/output_{entity_safe}.ttl` (per entity)
- **Extension T-Box**: `data/ontologies/{ontology}-subgraph.ttl`

## Outputs

### OntoMOPs Extension

Per entity:
- Extraction: `data/{doi_hash}/mcp_run_ontomops/extraction_{entity_safe}.txt`
- Extension TTL: `data/{doi_hash}/ontomops_extension_{entity_safe}.ttl`
- Prompts: `data/{doi_hash}/mcp_run_ontomops/extraction_prompt_{entity_safe}.md`
- Prompts: `data/{doi_hash}/mcp_run_ontomops/extension_prompt_{entity_safe}.md`

### OntoSpecies Extension

Per entity:
- Extraction: `data/{doi_hash}/mcp_run_ontospecies/extraction_{entity_safe}.txt`
- Extension TTL: `data/{doi_hash}/ontospecies_extension_{entity_safe}.ttl`
- Prompts: `data/{doi_hash}/mcp_run_ontospecies/extraction_prompt_{entity_safe}.md`
- Prompts: `data/{doi_hash}/mcp_run_ontospecies/extension_prompt_{entity_safe}.md`

## MCP Tools

### OntoMOPs
- `mops_extension`: Creates MOP instances, chemical building units, topology information
- `ccdc`: Downloads CIF/RES files from CCDC database

### OntoSpecies
- `ontospecies_extension`: Creates species characterizations, spectroscopic data, physical properties
- `ccdc`: Retrieves CCDC numbers and structural data

## Usage

### As Pipeline Step

Automatically called when `extensions_extractions` is in `configs/pipeline.json`:

```json
{
  "steps": [
    "pdf_conversion",
    "section_classification",
    "stitching",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
    "main_kg_building",
    "extensions_extractions"
  ]
}
```

### Standalone

```bash
python -m src.pipelines.extensions_extractions.extract <doi_hash>
```

## Key Features

1. **Entity-wise Processing**: Each top-level entity gets its own extension A-Box
2. **Automatic Linking**: Extension A-Boxes are linked to OntoSynthesis A-Boxes via IRIs
3. **Idempotent**: Skips already-processed entities
4. **Global State Management**: Uses file-locked global state for MCP server context
5. **Flexible File Naming**: Handles various entity name conventions (hyphens, underscores, case)

## Prompts

All prompts are stored as markdown files in the `ai_generated_contents/prompts/` directory:

### OntoMOPs
- **Extraction**: `ai_generated_contents/prompts/ontomops/EXTRACTION.md`
- **Extension**: `ai_generated_contents/prompts/ontomops/EXTENSION.md`

### OntoSpecies
- **Extraction**: `ai_generated_contents/prompts/ontospecies/EXTRACTION.md`
- **Extension**: `ai_generated_contents/prompts/ontospecies/EXTENSION.md`

Prompts use placeholders for dynamic content:
- `{entity_label}`, `{entity_uri}`: Entity information
- `{ontomops_t_box}`, `{ontospecies_t_box}`: T-Box content
- `{ontosynthesis_a_box}`: OntoSynthesis A-Box
- `{paper_content}`: Extracted paper content
- `{doi_slash}`, `{doi_underscore}`: DOI in different formats
- `{hash}`: DOI hash

## SPARQL Queries

No SPARQL queries are used in the extensions extractions step. The extension agents use MCP tools to directly create and link A-Box instances.

## Dependencies

- `models.LLMCreator`: For extraction LLM calls
- `models.BaseAgent`: For extension agent execution
- `filelock`: For atomic global state writes
- `src.utils.extraction_models`: For model configuration lookup

