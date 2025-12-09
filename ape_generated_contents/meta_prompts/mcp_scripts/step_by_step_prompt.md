# MCP Underlying Script Generation (Step-by-Step Mode) Meta-Prompt

You are an expert in creating domain-specific MCP functions from ontologies.

## Task

Generate MCP functions for **Step {step_number}: {step_name}** of the task division plan.

## Inputs

**Step Number**: {step_number}
**Step Name**: {step_name}
**Script Name**: {script_name}
**Output Directory**: {output_dir}
**Ontology Name**: {ontology_name}

**Goal**: {goal}

**Classes to Create**:
{classes_info}

**Relations to Establish**:
{relations_info}

**Information to Extract**:
{extraction_info}

**Constraints**:
{constraints_info}

**Universal Design Principles**:
```
{design_principles}
```

**T-Box Ontology**:
```turtle
{ontology_ttl}
```

## Requirements

### 1. Function Generation for This Step

Generate functions ONLY for the classes and properties relevant to this step.

For each class in "Classes to Create":

1. **Creation function**: `create_<class_name>(...) -> str`
   - Parameters derived from T-Box datatype properties
   - Implements constraints from T-Box rdfs:comment
   - Returns IRI of created instance
   - Validates inputs
   - Generates unique IRI
   - Creates RDF triples

2. **Check function**: `check_existing_<class_name>(...) -> Optional[str]`
   - Checks for existing instances
   - Implements deduplication logic
   - Returns IRI if found, None otherwise

For each relation in "Relations to Establish":

1. **Link function**: `link_<property_name>(subject_iri: str, object_iri: str) -> bool`
   - Validates domain and range from T-Box
   - Creates object property triple
   - Returns success status

### 2. Extract T-Box Constraints

For each relevant class, extract from its `rdfs:comment`:
- Cardinality rules (exactly one, zero or more, etc.)
- Required vs optional properties
- Allowed value enumerations
- Naming conventions
- Deduplication rules
- Validation rules

### 3. Implement Step-Specific Logic

Based on the "Information to Extract" and "Constraints":
- Add validation specific to this step
- Implement any step-specific business rules
- Handle step-specific error cases

### 4. Documentation

- Clear docstrings for all functions
- Reference the step number and goal
- Document how this step fits into the overall pipeline
- Include examples

### 5. Code Structure

```python
#!/usr/bin/env python3
"""
Step {step_number}: {step_name}

{goal}

Generated for {ontology_name} ontology.
"""

from rdflib import Graph, Namespace, URIRef, Literal
from typing import Dict, List, Optional, Any
import hashlib
from datetime import datetime

# Namespaces
ONTOLOGY_NS = Namespace("...")

# Step {step_number} Functions

def create_class1(...) -> str:
    """Create Class1 instance."""
    pass

def check_existing_class1(...) -> Optional[str]:
    """Check for existing Class1 instance."""
    pass

def link_property1(subject_iri: str, object_iri: str) -> bool:
    """Link entities via property1."""
    pass

# ... more functions for this step
```

## Output Format

Generate ONLY the complete Python code for this step. Do NOT include:
- Markdown code fences
- Explanations or commentary
- File paths or directory structures

Start directly with the Python shebang and imports.

## Critical Notes

1. **Step Scope**: Only generate functions for THIS step, not the entire ontology
2. **T-Box Driven**: All logic must derive from the T-Box ontology
3. **Constraint Enforcement**: Implement ALL validation rules from rdfs:comment
4. **Composability**: Functions should work with functions from other steps

Generate the step-specific functions now.
