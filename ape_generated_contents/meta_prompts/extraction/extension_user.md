Generate a comprehensive EXTRACTION prompt for an extension ontology.

T-Box (analyze to understand the ontology structure and what information to extract):
```turtle
{tbox}
```

Your prompt MUST:
1. **Identify the top entity class** - Determine which class is the main entity type
2. **Instruct comprehensive extraction** - Ask the extractor to extract ALL information needed to populate the A-Box according to the T-Box
3. **Emphasize completeness** - The extraction must include all properties, relationships, and characteristics defined in the T-Box
4. **Scope to entity** - Only extract information directly related to the top-level entity
5. **Require original text** - Must provide original text from the paper and indicate source locations
6. **Forbid fabrication** - Strictly forbid making up information; only extract what's explicitly stated
7. **Warn about entity focus** - Be extremely careful about extracting for the correct entity

Structure your output as a simple, focused prompt:
```
Given the top-level entity and the T-Box, extract all the information you need from the paper 
to populate the [ontology name] A-Box according to the T-Box. Consider carefully about the comments.

Only extract information that is directly related to the top-level entity.

[Add specific critical instructions based on T-Box comments]

Here is the top-level entity:

{{entity_label}}, {{entity_uri}}

Here is the T-Box of [Ontology Name]:

{{[ontology]_t_box}}

```

**CRITICAL**: Keep the prompt simple and focused. The key is "extract all the information you need to populate the A-Box according to the T-Box". Don't over-specify; let the T-Box rdfs:comment fields guide the extraction.

Generate the prompt now:

