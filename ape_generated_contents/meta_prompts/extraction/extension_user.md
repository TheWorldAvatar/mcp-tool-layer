Generate a comprehensive EXTRACTION prompt for an extension ontology.

T-Box (analyze to understand the ontology structure and what information to extract):
```turtle
{tbox}
```

Your prompt MUST:
1. **Use the extension focus class** - Derive its extraction scope from the supplied T-Box without redefining the inherited main-ontology root
2. **Instruct comprehensive extraction** - Ask the extractor to extract ALL information needed to populate the A-Box according to the T-Box
3. **Emphasize completeness** - The extraction must include all properties, relationships, and characteristics defined in the T-Box
4. **Scope through the inherited entity** - Extract extension-focus instances and facts
   relevant to the inherited upstream entity
5. **Require original text** - Must provide original text from the paper and indicate source locations
6. **Forbid fabrication** - Strictly forbid making up information; only extract what's explicitly stated
7. **Warn about entity focus** - Be extremely careful about extracting for the correct entity

Structure your output as a simple, focused prompt:
```
Given the top-level entity and the T-Box, extract all the information you need from the paper 
to populate the [ontology name] A-Box according to the T-Box. Consider carefully about the comments.

Only extract information that is directly related to the top-level entity.

[Add specific critical instructions based on T-Box comments]

Here is the inherited scoped entity:

{{entity_label}}, {{entity_uri}}

```

**CRITICAL**: Keep the prompt simple and focused. The key is "extract all the information you need to populate the A-Box according to the T-Box". Don't over-specify; let the T-Box rdfs:comment fields guide the extraction.
The runtime wrapper injects paper content and the T-Box separately. The generated
prompt must therefore use only `{{entity_label}}` and `{{entity_uri}}`; it must not
contain `{{paper_content}}`, `{{iteration_hints}}`, or any T-Box placeholder.
These two placeholders identify the inherited main-ontology scope, not the extension
focus instance. The generated prompt must keep those roles distinct and allow multiple
extension-focus instances within one inherited scope.

Generate the prompt now:

