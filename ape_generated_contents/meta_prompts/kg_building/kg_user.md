You are generating an MCP iteration prompt for knowledge graph construction.

## Input

**T-Box Ontology:**
```turtle
{tbox}
```

**MCP Main Script (Available Tools):**
```python
{mcp_main_script}
```

**Step from Task Division Plan:**
```json
{step_json}
```

## Task

Generate a prompt for iteration {step_number} that instructs an MCP agent to:

1. **Goal**: {goal}

2. **Create instances** of:
{instances_summary}

3. **Establish relations**:
{relations_summary}

4. **Extract information** from the source document:
{extraction_summary}

5. **Follow constraints**:
{constraints_summary}

## Prompt Structure Requirements

The prompt MUST follow this structure:

```
Follow these generic rules for any iteration.

{PROMPT_CORE}

[IDENTIFICATION_HEADER with document identifier placeholder]

**Critical**: 
[
  - Be absolutely faithful to the provided content
  - Make sure every [entity] listed in the paper content is created
  - Fidelity requirements if this is an enrichment iteration
]

Task:
[1-2 sentence imperative: Using the extracted information, create instances of [classes from T-Box], link via [property], set [properties]]

[BRIEF HIGH-LEVEL SECTIONS - Focus on WHAT to build and WHERE to link]

For major concepts, create SHORT sections (2-5 bullets each) with HIGH-LEVEL guidance:

[InputEntityType] (e.g., inputs, materials, resources):
- [How inputs are modeled as instances of which class]
- [How to link them: "For X steps, attach via [property]"]
- [Cardinality: "One X per Y"]

[Entity Type] steps/operations:
- [Cardinality rule: "One X per Y. Do not combine multiple Y into one X."]
- [Linking: "call [mcp_tool] with [parameter_iri]"]
- [Critical requirement from T-Box if any]

Auxiliary context entities (examples blurred):
- [Brief: create if mentioned, link to step]
 
Ordering:
- [High-level: "Maintain contiguous [property] values"]
- [High-level: "Strictly assign orders according to content"]

Error handling:
- [Brief: Respect JSON stop conditions]

Termination:
- [Brief: When all entities created, emit <"run_status":"done">]

==============

[Entity focus section if step > 1, otherwise source document content section]
```

## Important Guidelines

**NOTE**: Generic MCP rules (JSON parsing, stop conditions, IRI management, error handling) are **ALREADY HARDCODED** in the template. You should focus ONLY on **domain-specific** and **iteration-specific** rules.

**CRITICAL**: KG building prompts should be **CONCISE** and focus on:
- **HOW to construct the KG** using MCP tools (linking, ordering, property setting)
- **NOT on WHAT to extract** (entity definitions, trigger verbs, extraction rules - those are in EXTRACTION prompts)
- The generated prompt must be operational, not explanatory. It should read like an execution checklist for graph construction, not like ontology documentation.

1. **For ITER 2+ (when entity_uri is provided)**:
   - **CRITICAL**: Explicitly state to use the existing top entity IRI provided, do NOT create a new one
   - **CRITICAL**: Build the graph around that existing entity (link new entities to it)
   - **CRITICAL**: Treat the provided scoped top-level entity as authoritative even if similarly named entities are discovered elsewhere; do NOT create or switch to a second top-level entity for the same scope
   - **CRITICAL**: Before export, verify that every entity created or reused in this iteration is attached to that scoped top-level entity through the required ontology relations
   - Example instruction: "Link each [entity] to its parent [TopEntity] via [property]"

2. **Keep it brief and focused**:
   - This is KG BUILDING, not extraction - assume information is already extracted
- Focus on: linking entities, setting properties, ordering rules, continuity of reused auxiliary entities, MCP tool usage
   - Do NOT include: entity type definitions, trigger verbs, extraction criteria, detailed step type explanations
   - Do NOT include: detailed procedural instructions like "For each X, create one instance per Y in the order in which they appear. Use the tool Z with values..."
   - Each section should be 2-5 bullet points maximum with HIGH-LEVEL guidance only

2. **Structure with clear sections**:
   - Task statement (1-2 sentences: create X, link via Y, set properties Z)
   - Brief sections for major concepts (e.g., "[InputType]:", "[OperationType] steps:", "Ordering:")
   - Each section: SHORT bullet list of CONSTRUCTION rules (how to link, not what to extract)

3. **Use T-Box for CONSTRUCTION rules only**:
   - Cardinality rules (e.g., "One [OperationType] per [InputType]")
   - Linking rules (e.g., "Link via [property_name]")
   - Property requirements (e.g., "Set [property_name]")
   - Ordering/continuity rules
   - Exact canonical property names when the ontology distinguishes them from fallback or generic fields
   - Mutual exclusivity / final-state rules when sibling properties cannot all remain true together
   - Do NOT extract: entity definitions, trigger verbs, extraction gates

4. **Reference MCP tools for linking**:
- Focus on tool usage: "call add_xxx_to_yyy with the relevant subject/object IRI arguments"
   - Focus on parameters: what IRIs/values to pass
   - Focus on sequencing: what to check first, what to create next

5. **Be imperative and specific**: "Create an X for each Y" not "You should create X"

6. **Use property names** from T-Box exactly as written (e.g., [namespace]:[property_name])
   - If the upstream extracted hints already contain an exact canonical property, preserve that canonical property in the KG prompt instead of downgrading it to a weaker fallback field.
   - If the ontology implies a canonical-marker-versus-free-text fallback distinction, tell the KG agent to keep the canonical marker whenever it is explicitly confirmed and use the fallback only for unmatched explicit content.

7. **CRITICAL**: Do NOT include:
   - Generic MCP rules (IRI management, stop conditions, error handling) - already hardcoded
   - Extraction rules - assume extraction is done
   - Tool-specific instructions like "call check_orphan_entities" or other debugging/validation tools
   - Detailed procedural steps - keep it high-level
   - Instructions about MCP tool internals (e.g., "handled automatically by step creation tools if global context is configured")

8. **CRITICAL**: Keep instructions at a HIGH LEVEL:
   - "One X per Y" not "For each X, create one instance per Y in the order in which they appear"
   - "Link via property" not "Use the tool_name with parameter_name with extracted values"
   - "Set ordering" not "Set property explicitly on every entity; start from 1 and increment by 1 with no gaps"
   - Still include high-level precedence rules when needed, such as "preserve only the final resolved marker among mutually exclusive siblings" or "do not attach provisional results as final authoritative outcomes"

9. **Integrity over termination**:
   - The generated prompt MUST remind the agent not to terminate just because creation calls succeeded; it must also ensure required links/attachments are complete
   - If the iteration is scoped to an existing top-level entity, the prompt MUST say that exporting memory before those links are attached is invalid

## Output Format

Output ONLY the prompt text. Do NOT include:
- Markdown code fences
- Explanations
- JSON
- Any preamble or postamble

Start directly with: "Follow these generic rules for any iteration."

Generate the prompt now:
