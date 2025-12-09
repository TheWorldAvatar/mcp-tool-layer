Generate an extraction prompt for one iteration step.

T-Box (FULL REFERENCE; mine rdfs:comment constraints rigorously):
```turtle
{tbox}
```

Iteration metadata (JSON; scope hint only):
```json
{meta}
```

**Critical**:

Your prompt MUST:
- Be domain-agnostic and derive ALL specifics from T-Box rdfs:comments.
- Include EVERY detail from rdfs:comment: goals, in-scope entities, required fields, inclusion rules, exclusion rules, cardinality, allowed values, gating triggers, ordering, linking, dedup rules, naming rules, placeholder policies, trigger verbs/phrases, property details (with types and defaults), and any special handling notes.
- For each relevant class, extract and include ALL rules from its rdfs:comment (numbered rules, additional rules, critical notes, examples, trigger phrases).
- For each relevant property, include its type, default value (if specified), and usage rules from rdfs:comment.
- Reference class/property names exactly as in the T-Box.
- Provide clear termination criteria.
- Avoid dataset-specific paths (the script handles files).
- Be completely domain-agnostic: do not add examples or terminology not present in the T-Box.
- Output plain text only (no markdown fences).

**COMPLETENESS CHECK**: Before finalizing, verify you have extracted:
1. ALL numbered/lettered rules from each relevant class's rdfs:comment
2. ALL property definitions with their types and defaults
3. ALL trigger verbs/phrases mentioned
4. ALL examples provided
5. ALL critical notes, warnings, or special cases
6. ALL linking and deduplication policies

Recommended structure:
Task:
[What to extract for this iteration and the specific entities/properties]

Scope:
[What is in scope vs out-of-scope]

**CRITICAL RULES** (Extract these from rdfs:comment and emphasize prominently):
[Identify and list ALL rules marked as **CRITICAL**, **MANDATORY**, or with **NEVER**/**ALWAYS** language in the T-Box rdfs:comments. These are non-negotiable constraints that must be highlighted at the top of the prompt. Look for:
 - Ordering/sequencing constraints between different entity types
 - Explicit language requirements for entity creation
 - Invalid entity subtypes or classifications
 - Cardinality rules (one-per-instance, minimum/maximum counts)
 - Separation of operations or attributes
List each critical rule clearly with verbatim quotes or paraphrases from the T-Box rdfs:comment.]

Ontology-derived constraints:
[For EACH relevant class, include ALL rules from its rdfs:comment - numbered rules, additional rules, critical notes, examples, trigger phrases. Do NOT summarize; include complete details.]

Class-specific rules:
[For each entity type involved, list:
 - When to use (trigger conditions)
 - When NOT to use (exclusions)
 - Required properties with types and defaults
 - Special handling rules
 - Examples from rdfs:comment
 - **RE-EMPHASIZE any CRITICAL ordering or constraint rules here**]

Inclusion rules:
[When to include an entity or assertion]

Exclusion rules:
[When to exclude]

Field requirements:
[For each relevant entity type, list ALL properties mentioned in rdfs:comment with:
 - Property name (exact from T-Box)
 - Type (as specified in T-Box rdfs:range or rdfs:comment)
 - Default value if specified
 - Usage rules]

Deduplication and linking:
[Rules for deduplicating instances and linking with correct properties; mention ordering rules where applicable]

Termination:
[Conditions under which extraction can be considered done]
{extra_guidance}

Note: The script will automatically append the appropriate input variables section (entity_label, paper_content, context, base_hints) based on the iteration metadata.
