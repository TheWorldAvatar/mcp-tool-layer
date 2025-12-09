# MOP Derivation Module

## Overview

The MOP Derivation module derives Chemical Building Units (CBUs) from CCDC crystallographic files and paper content, then integrates them to derive complete Metal-Organic Polyhedra (MOP) formulas.

## Workflow

This module executes three sequential steps:

### 1. Metal CBU Derivation
- Derives metal-based CBUs from CCDC RES and CIF files
- Uses LLM to analyze crystallographic structure and identify minimal metal clusters
- Grounds results against existing metal CBU database
- **Inputs:**
  - CCDC RES files (`data/ccdc/res/<ccdc_number>.res`)
  - CCDC CIF files (`data/ccdc/cif/<ccdc_number>.cif`)
  - OntoMOPs extension TTL files (`data/<hash>/ontomops_output/ontomops_extension_<entity>.ttl`)
  - OntoSpecies extraction content (`data/<hash>/mcp_run_ontospecies/extraction_<entity>.txt`)
  - Existing CBU database (`data/ontologies/full_cbus_with_canonical_smiles_updated.csv`)
- **Outputs:**
  - `data/<hash>/cbu_derivation/metal/structured/<entity>.json` - Metal CBU formula and IRI
  - `data/<hash>/cbu_derivation/metal/structured/<entity>.txt` - Metal CBU formula (text)
  - `data/<hash>/cbu_derivation/metal/structured/<entity>_iri.txt` - Metal CBU IRI (text)
  - `data/<hash>/cbu_derivation/metal/prompts/<entity>.md` - Full prompt sent to LLM
  - `data/<hash>/cbu_derivation/metal/responses/<entity>.md` - LLM response

### 2. Organic CBU Derivation
- Derives organic ligand CBUs using MCP agent with chemistry tools
- Uses enhanced web search, PubChem, and SMILES canonicalization
- Grounds results against existing organic CBU database
- **Inputs:**
  - OntoSpecies extraction content (`data/<hash>/mcp_run_ontospecies/extraction_<entity>.txt`)
  - CCDC RES files (for disambiguation)
  - Existing CBU database
- **Outputs:**
  - `data/<hash>/cbu_derivation/organic/<entity>.md` - Individual derivation results
  - `data/<hash>/cbu_derivation/organic/summary.md` - Summary of all organic CBUs
  - `data/<hash>/cbu_derivation/organic/instructions.md` - Derivation instructions

### 3. Integration
- Combines metal and organic CBUs
- Derives complete MOP formula
- Creates per-entity JSON files with all CBU information
- **Inputs:**
  - Metal CBU results from step 1
  - Organic CBU results from step 2
- **Outputs:**
  - `data/<hash>/cbu_derivation/full/<entity>.json` - Complete CBU information including MOP formula

## Configuration

No additional configuration required. The module uses:
- Model: `gpt-5-mini` for metal CBU derivation
- Model: `gpt-4.1` for organic CBU derivation (via MCP agent)
- MCP tools: `["pubchem", "enhanced_websearch", "ccdc", "chemistry"]`

## Prerequisites

- `extensions_kg_building` step must be completed
- CCDC files must be available in `data/ccdc/` directory
- CBU database must be present at `data/ontologies/full_cbus_with_canonical_smiles_updated.csv`

## Skipping Behavior

The module will skip if:
- Completion marker exists (`.mop_derivation_done`)
- `ontomops_output` directory doesn't exist
- No `ontomops_extension_*.ttl` files found

## Prompts and SPARQL

All prompts and SPARQL queries are externalized:

### Prompts
- `ai_generated_contents/prompts/cbu_derivation/METAL_CBU_DOI_FOUND.md` - Metal CBU prompt when DOI is in database
- `ai_generated_contents/prompts/cbu_derivation/METAL_CBU_DOI_NOT_FOUND.md` - Metal CBU prompt when DOI is not in database
- `ai_generated_contents/prompts/cbu_derivation/ORGANIC_CBU_DOI_FOUND.md` - Organic CBU prompt when DOI is in database
- `ai_generated_contents/prompts/cbu_derivation/ORGANIC_CBU_DOI_NOT_FOUND.md` - Organic CBU prompt when DOI is not in database

### SPARQL Queries
- `ai_generated_contents/sparqls/cbu_derivation/extract_ccdc_number.sparql` - Extract CCDC number from OntoMOPs TTL

## Usage

### As Part of Pipeline
```python
from src.pipelines.mop_derivation import run_step

config = {
    "data_dir": "data",
    "project_root": "."
}

success = run_step(doi_hash="a1b2c3d4", config=config)
```

### Standalone
```bash
python -m src.pipelines.mop_derivation.derive <doi_hash>
```

## Error Handling

- Returns `False` if any step fails
- Logs detailed error messages
- Does not create completion marker on failure
- Safe to re-run after fixing issues

## Notes

- Metal and organic CBU derivation run sequentially (not in parallel)
- Integration only runs after both CBU derivations complete successfully
- The module reuses existing agent implementations for stability
- All prompts are sent to LLMs and saved for debugging

