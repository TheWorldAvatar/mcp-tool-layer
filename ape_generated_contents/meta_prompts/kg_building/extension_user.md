Generate a KG building prompt for an extension ontology.

T-Box (analyze to understand the ontology structure and requirements):
```turtle
{tbox}
```

MCP Main Script (understand available tools, their parameters, and calling sequences):
```python
{mcp_main_script}
```

Your prompt MUST:
1. **State the task** - Extend the main ontology A-Box with the extension A-Box
2. **Provide a task route** - Give a recommended sequence of steps for building the KG
3. **Emphasize MCP tools** - Instruct to use the MCP server to populate the A-Box
4. **Require IRI reuse** - Emphasize reusing existing IRIs from the main ontology A-Box
5. **Extract T-Box requirements** - Include any compulsory requirements from rdfs:comment (e.g., required fields, minimum instances)
6. **Forbid fabrication** - Only use information from the paper content
7. **Include tool-specific notes** - If the T-Box or domain requires special handling (e.g., external database integration, data transformations), include those notes

Structure your output as:
```
Your task is to extend the provided A-Box of [MainOntology] with the [extension ontology] A-Box, according to the paper content.

You should use the provided MCP server to populate the [extension ontology] A-Box.

Here is the recommended route of task:

[Step-by-step guidance based on T-Box structure]

Requirements:

[List of requirements based on T-Box rdfs:comment and MCP tool constraints]

Special note:

[Any domain-specific notes based on T-Box comments]

Here is the DOI for this run (normalized and pipeline forms):

- DOI: {{doi_slash}}
- Pipeline DOI: {{doi_underscore}}

Here is the [MainOntology] A-Box:

{{main_ontology_a_box}}

Here is the paper content:

{{paper_content}}
```

**CRITICAL**: 
- Extract domain-specific requirements from the T-Box rdfs:comment fields. Do NOT invent requirements. ALL requirements must be justified by the T-Box or MCP tool constraints.
- Output EXACTLY the structure shown above. Do NOT add any additional sections after {{paper_content}}. This is the END of the prompt.

Generate the prompt now:

