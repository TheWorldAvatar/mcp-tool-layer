# APE Generated Contents

This folder contains **meta-prompts** and **meta-configuration** used to generate AI-driven content for the MOPs extraction pipeline.

## Purpose

- **NOT for AI-generated content** (those go in `ai_generated_contents/` or `ai_generated_contents_candidate/`)
- **ONLY for meta-prompts** that instruct LLMs how to generate prompts, scripts, and configurations
- **ONLY for meta-configuration** that defines the ontology pipeline structure

## Structure

```
ape_generated_contents/
├── README.md                    # This file
├── meta_task_config.json        # Main configuration for ontologies and pipeline
└── meta_prompts/                # Meta-prompts for generating content
    ├── extraction/              # Meta-prompts for extraction prompt generation
    │   ├── iter1_system.md
    │   ├── iter1_user.md
    │   ├── iter_system.md
    │   ├── iter_user.md
    │   ├── pre_extraction_system.md
    │   └── pre_extraction_user.md
    ├── kg_building/             # Meta-prompts for KG building prompt generation
    │   ├── iter1_system.md
    │   ├── iter1_user.md
    │   ├── kg_system.md
    │   └── kg_user.md
    └── mcp_scripts/             # Meta-prompts for MCP script generation
        ├── underlying_system.md
        ├── underlying_user.md
        ├── main_system.md
        └── main_user.md
```

## Usage

The prompt generation scripts in `src/agents/scripts_and_prompts_generation/` load meta-prompts from this folder to generate:
1. **Extraction prompts** → `ai_generated_contents/prompts/{ontology}/EXTRACTION_ITER_*.md`
2. **KG building prompts** → `ai_generated_contents/prompts/{ontology}/KG_BUILDING_ITER_*.md`
3. **MCP scripts** → `ai_generated_contents/scripts/{ontology}/`

## Meta-Prompt Guidelines

Meta-prompts should be:
- **Domain-agnostic**: Work for any ontology (MOPs, proteins, reactions, etc.)
- **Complete**: Include all necessary instructions for the LLM
- **Structured**: Follow a clear template format
- **Maintainable**: Easy to update without modifying Python code

## Note

Previously, this folder contained documentation files (e.g., `mcp_script_creation/`, `specific_rules/`). These have been removed as they were not meta-prompts. Meta-prompts are now being migrated from hardcoded strings in Python files to separate `.md` files in this folder for better maintainability.

