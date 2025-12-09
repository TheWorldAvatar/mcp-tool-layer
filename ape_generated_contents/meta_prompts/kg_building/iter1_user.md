Based on the ontology and available MCP tools below, generate a KG building prompt for ITERATION 1.

ITERATION 1 SCOPE:
- Create ONLY top-level entity instances (the main procedure/process class)
- Do NOT create any related entities (inputs, outputs, sub-components, steps)
- Link to source document

ONTOLOGY (T-Box):
{tbox}

MCP MAIN SCRIPT (Available Tools):
```python
{mcp_main_script}
```

The MCP Main Script shows all available tools with their descriptions, parameters, and usage guidance. Use this to understand what tools are available and how they should be called.

REQUIREMENTS:
1. Extract ALL rules from the top-level entity class rdfs:comment, especially:
   - Scope (what qualifies as a valid instance)
   - Different forms / methods / variations rules
   - Exclusions (what NOT to create)
   - Cardinality requirements
   - Linking requirements
   - Conservative behavior guidelines
   - Critical exclusions for extraction

2. Include global MCP rules:
   - Tool invocation rules (never call same tool twice with identical args)
   - IRI management (must create before passing, use check_existing_* tools)
   - Error handling (status codes, already_attached, retryable)
   - Placeholder policies
   - Termination conditions (run_status: done)

3. Include identification section:
   - Document identifier handling (treat as sole task identifier, reuse consistently)
   - Entity focus guidance (for when entity_label/entity_uri provided)

4. Be concise and actionable - this is for an MCP agent making function calls

5. Be completely domain-agnostic:
   - Do NOT mention specific compound types, materials, or chemical entities
   - Use generic terminology that applies to any domain
   - Adapt wording from the ontology to be domain-neutral where possible

Generate the prompt now (do NOT include variable placeholders - those will be added programmatically):
