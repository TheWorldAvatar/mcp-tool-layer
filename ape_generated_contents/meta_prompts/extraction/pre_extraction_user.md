Generate a PRE-EXTRACTION prompt for extracting RAW TEXT ONLY from papers.

Iteration metadata (READ THIS CAREFULLY - it tells you what task the extracted text will be used for):
```json
{meta}
```

**CRITICAL DISTINCTION**: This is a PRE-EXTRACTION prompt, NOT a structured extraction prompt.

**TASK AWARENESS**: The iteration metadata above describes what task will be performed on the extracted text. Your pre-extraction prompt MUST ensure the extracted text contains ALL information needed for that task. Pay special attention to:
- The "name" field - this tells you what the subsequent extraction will focus on
- The "description" field - this describes what will be extracted from the pre-extracted text
- The extracted text must be SELF-CONTAINED for the entity, meaning someone reading only the extracted text should have all information needed to complete the subsequent task

**CRITICAL - ONTOLOGY-DRIVEN EXCLUSIONS/INCLUSIONS**: You MUST review the ontology T-Box (provided below) and look for:
- Any EXCLUSION rules in the rdfs:comment sections (e.g., "Do NOT extract", "EXCLUDED", "should be excluded", "CRITICAL EXCLUSIONS")
- Any INCLUSION rules or scope constraints (e.g., "ONLY extract", "focus on", "strictly include")
- These rules define what content should or should not be extracted during pre-extraction
- Incorporate these exclusion/inclusion rules into the pre-extraction prompt using generic, domain-agnostic language
- Do NOT copy domain-specific examples, but DO capture the intent of the rules (e.g., if TTL says "exclude activation procedures", translate to "exclude post-production treatment or preparation procedures not part of the main synthesis")

**REQUIRED STRUCTURE**: The prompt you generate MUST include these exact placeholder variables:
- {{entity_label}} - will be replaced with the specific entity name
- {{paper_content}} - will be replaced with the full paper text

Your PRE-EXTRACTION prompt MUST:
- Ask ONLY for verbatim text extraction from the paper
- Extract ALL relevant text spans for the entity that are needed for the subsequent task described in the metadata
- Ensure the extracted text is SELF-CONTAINED: include all context, definitions, conditions, and referenced procedures
- Preserve original wording without summarization or interpretation
- Handle cross-references (e.g., "above-mentioned procedure", "same method as") by explicitly resolving them:
  * First include the original sentence with the reference
  * Then add a separator: 'The "[referenced phrase]" refers to the following content:'
  * Then include the FULL referenced text (trace back completely)
- Include global experimental conditions if mentioned (these may appear in other sections)
- Include any preparatory steps or context mentioned elsewhere that are needed to understand the entity's procedure
- Output plain text only (no JSON, no structure, no markdown fences in the actual extraction)
- Be completely domain-agnostic (no specific compound types, no domain-specific terminology)

DO NOT include in a pre-extraction prompt:
- Ontology constraints or class names
- Structured output requirements
- Entity instantiation instructions
- Step type classification
- Field requirements
- Any domain-specific terminology

**EXAMPLE REFERENCE STRUCTURE** (use this pattern, but adapt wording to be generic and task-aware):
```
Extract the ORIGINAL TEXT spans from the paper that describe the procedure for the specified entity only.
- Strictly include only content relevant to the entity_label's procedure; exclude other entities or unrelated sections.
- Aggregate all relevant snippets even if scattered across the paper; preserve original wording verbatim.
- SELF-CONTAINMENT REQUIREMENT: The extracted text must be self-contained and include ALL information needed for the subsequent task. Someone reading only the extracted text should have complete context.
- Include global experimental conditions if mentioned (e.g., "The experiments were performed under xxx"). Note that 
this information might appear in other sections of the paper, you must include it in the pre-extraction. Read all contents carefully to find the global experimental conditions.
- Include any preparatory steps, reagent preparations, or contextual information mentioned elsewhere in the paper that are necessary to understand the entity's procedure.
- CRITICAL - TRACE BACK COMPLETELY: If the text contains vague references (e.g., 'above-mentioned procedure', 'same method as', 'similar to', 'prepared as described for', 'following the procedure', 'as described earlier'), you MUST:
  1) First include the original sentence with the reference
  2) Then add a clear separator line: 'The "[referenced phrase]" refers to the following content:'
  3) Then include the FULL referenced text, tracing back to the original source
  4) If the referenced text itself contains references, continue tracing back until you reach the complete original procedure
  Example: If text says 'using the above-mentioned procedure', output:
    [Original sentence with reference]
    The "above-mentioned procedure" refers to the following content:
    [Full text of the referenced procedure, including any sub-references resolved]
- Preserve order as they appear in the paper; separate non-contiguous snippets with a blank line.
- Do NOT summarize or paraphrase; return only the raw text excerpts with clear reference resolutions.
- Output ONLY the text (no commentary, no code fences, no JSON).


entity_label: {{entity_label}}
paper: 
<<<
   
{{paper_content}}

>>>
```

Generate a pre-extraction prompt following this pattern (use the exact placeholder format with double curly braces).

**IMPORTANT**: 
1. Review the iteration metadata to understand what task will be performed on the extracted text
2. Adapt the prompt to emphasize extracting information relevant to that specific task based on the "name" and "description" fields
3. Make the prompt task-aware while keeping it completely domain-agnostic (no specific examples or domain terminology)

Output the prompt as plain text (no markdown code fences around your output).
