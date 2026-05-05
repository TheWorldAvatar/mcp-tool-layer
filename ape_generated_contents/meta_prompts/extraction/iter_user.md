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
- Be operational and machine-consumable, not explanatory. Tell the extractor exactly what structured output to emit, with exact omission behavior and exact token formats where relevant.
- Explicitly forbid narrative filler such as introductions, conclusions, reminders, or explanations like "not provided", "cannot be determined", "not applicable", or similar prose.
- Instruct the extractor to omit unsupported fields entirely unless the T-Box explicitly requires a positive/negative marker token for that field.
- Keep ontology-derived descriptions concise and action-oriented. Preserve critical constraints from rdfs:comment, but do not turn the prompt into a tutorial or long conceptual exposition.
- If any rdfs:comment implies evidence precedence, you MUST include an explicit "Evidence priority / precedence rules" section in the generated prompt. This includes cases such as:
  - explicit summary/header evidence outranking weaker downstream narrative mentions
  - final resolved state outranking initial setup wording
  - authoritative final evidence outranking provisional or weaker findings
- If the T-Box distinguishes canonical marker properties from fallback free-text properties, you MUST include an explicit "Canonical vs fallback" rule stating that canonical properties win whenever their criteria are met, and fallback free-text is only for explicit unmatched content.
- If sibling properties are mutually exclusive or require final-state resolution, you MUST state that exclusivity explicitly rather than leaving it implicit in the property descriptions.
- If a property rdfs:comment defines inheritance, defaulting, or override behavior across ordered members, you MUST make that rule operational in both field requirements and output discipline. Require the extractor to emit the inherited/defaulted property value explicitly on the affected ordered member whenever the T-Box says it applies, unless a source-supported override applies.
- If a free-text fallback property requires only the shortest necessary phrase, you MUST say so explicitly in both the field description and the output-format instructions.
- For each relevant class, extract and include ALL rules from its rdfs:comment (numbered rules, additional rules, critical notes, examples, trigger phrases).
- For each relevant property, include its type, default value (if specified), and usage rules from rdfs:comment.
- If a relevant object property's rdfs:comment instructs the extractor to populate a property on the linked range object, include that linked-object property explicitly in the field requirements and output discipline. Use the exact property name from the T-Box and make clear whether it belongs on the step/source instance or on the linked target instance.
- **Encoding is mandatory**: If a property's rdfs:comment prescribes an encoding such as:
  - enter `"1"` when the condition holds and otherwise omit the field, or
  - enter `"j"`/`"n"` (and otherwise omit),
  then your generated prompt MUST contain an explicit "Encoding rules" section and MUST instruct the extractor to output exactly those tokens (never descriptive phrases).
  Additionally, you MUST explicitly enumerate each such property by name under "Encoding rules" (use the actual property names from the T-Box, e.g. "PropertyA → output 1 if present, otherwise omit", "PropertyB → output j/n, otherwise omit") so the extractor cannot confuse marker columns with free-text columns.
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
[For each relevant entity type, list ALL properties mentioned in rdfs:comment. Every property that has its own rdfs:comment MUST have its own dedicated entry — do NOT group multiple properties into a single bullet (e.g., do NOT write "all PropertyGroupX: enter marker_token if present"). For each property entry include:
 - Property name (exact from T-Box)
 - Type (as specified in T-Box rdfs:range or rdfs:comment)
 - Default value if specified
 - FULL definition and all rules from its rdfs:comment: what the property covers, what it explicitly does NOT cover (exclusion criteria), any calculation rules, gating dependencies, special cases, and encoding instructions — reproduce these verbatim or very closely paraphrased, do NOT abbreviate or omit any part]

Deduplication and linking:
[Rules for deduplicating instances and linking with correct properties; mention ordering rules where applicable]

Evidence priority / precedence rules:
[When comments indicate that some evidence sources outrank others, list those rules explicitly and make them operational for the extractor]

Canonical vs fallback rules:
[For each relevant canonical marker vs free-text fallback pair, state when the canonical field must be preferred and when the fallback field may be used]

Output discipline:
[State the exact output contract for the extractor. Require the exact top-level structured shape requested by the iteration metadata; do not allow alternate wrapper names, prose headings, or ad hoc section labels when a canonical list/key is specified. For ordered members, require each item to carry the canonical order property, the concrete rdf:type, and every source-supported or T-Box-derived inherited/defaulted property that applies to that exact item. Forbid explanatory prose, "missing value" commentary, and filler text. State that unsupported fields are omitted entirely unless a T-Box encoding rule explicitly requires a marker token.]

Termination:
[Conditions under which extraction can be considered done]
{extra_guidance}

Note: The script will automatically append the appropriate input variables section (entity_label, paper_content, context, base_hints) based on the iteration metadata.
