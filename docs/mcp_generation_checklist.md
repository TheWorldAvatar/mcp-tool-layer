The following requirements for the final mcp servers and underlying scripts must be met:

1. The prompts should be domain agnostic, no specific domain entity name, class should be mentioned 
2. The domain-specific knowledge should only come from the ontologies, the allowed list is 

- om2_mock.ttl
- ontomops.ttl 
- ontosynthesis.ttl
- ontospecies.ttl
- vessel_type.ttl

3. The generate scripts and scripts should support the following operations:

- ttl file-based persistent storage (take reference from ai_generated_contents_reference\scripts\ontosynthesis\mcp_creation.py)
- complete ontology representation, where all classes and properties are taken care of, the script can construct them. 
- Specific unit constraints are applied, using OM-2 mapping mechanism and script provides feedbacks. 
- The top entity check is applied 
- The step order consistency check is applied (no repeating step number, no missing step number, contiguous step number)

4. Consistency between mcp server (main.py) and underlying script (mcp_creation.py) should be maintained.

- The inputs and outputs of the mcp server and underlying script should be consistent.
- MCP server expose all the core functions for building underlying scripts. 
