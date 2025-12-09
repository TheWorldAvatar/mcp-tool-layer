Generate a focused ITERATION 1 extraction prompt for identifying top-level entity instances.

T-Box (analyze to determine top entity type and extraction rules):
```turtle
{tbox}
```

Your prompt MUST:
1. **Identify the top entity class** - Determine which class is the main entity type
2. **Extract identification rules** - Mine the rdfs:comment for that class to extract:
   - Scope gate (what qualifies as this entity type)
   - Inclusion criteria (when to include)
   - Exclusion criteria (when to exclude)
   - Composite/adduct rejection rules
   - Evidence requirements
   - Deduplication rules
   - Normalization rules

3. **Create a tight extraction task** that instructs the extractor to:
   - Identify ALL instances of the top entity in the document
   - Output DESCRIPTIVE identifiers (NEVER allow bare numbers like "1", "2", "3" - require descriptive labels like "Entity-1 [identifier]", "Instance-A [name]")
   - Apply strict gating rules
   - Exclude when uncertain

Structure your output as:
```
Task: [Clear statement: extract all X instances, output descriptive identifier]

Ontology-anchored constraints:
[Rules from rdfs:comment about what qualifies as this entity]

Inclusion:
[Bullet points: when to include]

Hard rules:
[Numbered strict rules from rdfs:comment]

Polymorph and duplicate control: [if applicable]
[Rules about deduplication]

Decision tests (fail fast, in order):
[Step-by-step decision tree from rdfs:comment rules]

Output format (plain text, one line per entity):
[Format specification - emphasize descriptive identifiers with entity names/codes/formulas, NOT just bare numbers]

**CRITICAL OUTPUT REQUIREMENT**:
Identifiers MUST be descriptive and self-explanatory. NEVER output bare numbers like "1", "2", "3" alone.

Examples of GOOD vs BAD output format:
✅ GOOD: "Entity-1 [descriptive formula or name]"
✅ GOOD: "Code-A"
✅ GOOD: "Instance-1 - descriptive identifier"
✅ GOOD: "Named-Instance"
❌ BAD: "1" (bare number - FORBIDDEN)
❌ BAD: "2" (bare number - FORBIDDEN)
❌ BAD: "3" (bare number - FORBIDDEN)

If the paper uses bare numbers, prefix them with a descriptive label (e.g., "Entity 1", "Instance 1", "Item 1").
```

Generate the prompt now: