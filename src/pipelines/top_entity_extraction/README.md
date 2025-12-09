# Top Entity Extraction Pipeline Step

This module extracts top-level entities (e.g., `ChemicalSynthesis`) from scientific papers using LLM-based extraction.

## Overview

The top entity extraction step:
1. Reads the stitched markdown file (`<doi_hash>_stitched.md`)
2. Loads the extraction prompt from the main ontology configuration
3. Uses an LLM to extract top-level entities
4. Saves the results to `top_entities.txt`

## Configuration

### Meta Configuration

The main ontology is specified in `configs/meta_task_config.json/meta_task_config.json`:

```json
{
  "ontologies": {
    "main": {
      "name": "ontosynthesis",
      "description": "The ontology for the synthesis of chemical compounds.",
      "ttl_file": "data/ontologies/ontosynthesis.ttl",
      "complex_pipeline": true
    }
  }
}
```

### Extraction Prompt

The extraction prompt is stored in:
```
ai_generated_contents/prompts/<ontology_name>/EXTRACTION_ITER_1.md
```

For example: `ai_generated_contents/prompts/ontosynthesis/EXTRACTION_ITER_1.md`

### Model Configuration

The LLM model is specified in `configs/extraction_models.json`:

```json
{
  "iter1_hints": "gpt-4.1"
}
```

## Usage

### As Part of Pipeline

Add to `configs/pipeline.json`:

```json
{
  "steps": [
    "pdf_conversion",
    "section_classification",
    "stitching",
    "top_entity_extraction"
  ]
}
```

### Standalone

```bash
python -m src.pipelines.top_entity_extraction.extract <doi_hash>
```

## Input/Output

### Input
- `data/<doi_hash>/<doi_hash>_stitched.md` - Stitched markdown from previous step

### Output
- `data/<doi_hash>/top_entities.txt` - Extracted top-level entities

Example output format:
```
ChemicalSynthesis — UMC-1
ChemicalSynthesis — VMOP-18
ChemicalSynthesis — MOF-5
```

## Implementation Details

- **Model**: Loaded from `extraction_models.json` using key `iter1_hints`
- **Temperature**: 0 (deterministic)
- **Top_p**: 1.0
- **Retries**: Up to 3 attempts with exponential backoff
- **Idempotency**: Skips if `top_entities.txt` already exists

## Adding New Ontologies

1. Add ontology to `configs/meta_task_config.json/meta_task_config.json`
2. Create prompt file: `ai_generated_contents/prompts/<ontology_name>/EXTRACTION_ITER_1.md`
3. The module will automatically use the new ontology

## Error Handling

The module handles:
- Missing stitched markdown file
- Missing extraction prompt
- LLM API failures (with retries)
- Invalid responses

All errors are logged with appropriate emoji indicators for easy identification.

