# MCP Underlying Script Generation (Full Mode) Meta-Prompt

You are an expert in creating domain-specific MCP underlying scripts from ontologies.

## Task

Generate a complete MCP underlying script that implements all necessary functions for knowledge graph construction based on the provided T-Box ontology.

## Inputs

**Ontology Name**: `{ontology_name}`
**Script Name**: `{script_name}`
**Output Directory**: `{output_dir}`

**Universal Design Principles**:
```
{design_principles}
```

**T-Box Ontology**:
```turtle
{ontology_ttl}
```

## Requirements

### 1. Script Structure

```python
#!/usr/bin/env python3
"""
{ontology_name}_creation.py

Domain-specific MCP functions for {ontology_name} ontology.
Generated from T-Box ontology.
"""

# Imports
from rdflib import Graph, Namespace, URIRef, Literal
from typing import Dict, List, Optional, Any
import hashlib
from datetime import datetime

# Constants and namespaces
ONTOLOGY_NS = Namespace("...")  # From T-Box
# ... other namespaces

# Utility functions
# ... (reusable helpers)

# Entity creation functions
# ... (one per class in T-Box)

# Relationship functions
# ... (one per object property)

# Query/check functions
# ... (for validation and deduplication)
```

### 2. Function Generation Rules

For **each OWL Class** in the T-Box, generate:

1. **Creation function**: `create_<class_name>(...) -> str`
   - Parameters: All datatype properties + required object properties
   - Returns: IRI of created instance
   - Validates inputs against T-Box constraints
   - Generates unique IRI (hash-based or timestamp-based)
   - Creates RDF triples
   - Adds to graph

2. **Check function**: `check_existing_<class_name>(...) -> Optional[str]`
   - Parameters: Key identifying properties
   - Returns: IRI if exists, None otherwise
   - Queries graph for existing instances
   - Implements deduplication logic from rdfs:comment

For **each Object Property**, generate:

1. **Link function**: `link_<property_name>(subject_iri: str, object_iri: str) -> bool`
   - Validates domain and range
   - Creates triple
   - Returns success status

### 3. T-Box Constraint Implementation

Extract from `rdfs:comment` annotations:
- **Cardinality rules**: Enforce "exactly one", "zero or more", etc.
- **Allowed values**: Validate against enumerations
- **Naming rules**: Apply naming conventions
- **Deduplication rules**: Implement check_existing logic
- **Validation rules**: Check required fields, formats, types

### 4. IRI Generation

- Use hash-based IRIs for deterministic entities
- Use timestamp-based IRIs for unique instances
- Follow pattern: `<namespace><class_name>/<identifier>`
- Ensure uniqueness within graph

### 5. Error Handling

- Validate all inputs before processing
- Return informative error messages
- Handle missing required fields
- Check type compatibility
- Validate cardinality constraints

### 6. Documentation

- Include comprehensive docstrings for all functions
- Document parameters with types
- Document return values
- Include examples where helpful
- Reference T-Box classes/properties

### 7. Graph Management

- Use RDFLib Graph for in-memory storage
- Implement proper namespace management
- Support serialization to TTL format
- Include graph initialization function

## Output Format

Generate ONLY the complete Python code. Do NOT include:
- Markdown code fences
- Explanations or commentary
- File paths or directory structures

Start directly with the Python shebang and imports.

## Critical Notes

1. **Domain-Agnostic Core**: Use the universal design principles for graph operations
2. **Domain-Specific Logic**: Derive all entity/property logic from the T-Box
3. **Complete Coverage**: Generate functions for ALL classes and properties in the T-Box
4. **Constraint Enforcement**: Implement ALL validation rules from rdfs:comment annotations
5. **Reusability**: Design functions to be reusable and composable

Generate the complete underlying script now.
