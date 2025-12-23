#!/usr/bin/env python3
"""
Direct LLM Script Generation (No Agents/MCP)

This module provides direct LLM-based script generation functions that:
1. Load domain-agnostic meta-prompts from ape_generated_contents/meta_prompts/mcp_scripts/
2. Parse T-Box ontology TTL to extract entity classes, properties, relationships
3. Fill meta-prompt templates with extracted domain-specific information
4. Call LLM API directly (no agents, no MCP tools, no Docker)
5. Extract code from LLM response
6. Write code directly to files

This is faster and simpler than agent-based generation, suitable for when you have 
stable prompts and just need to generate code.
"""

import os
import sys
import re
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple
from openai import OpenAI
from dotenv import load_dotenv
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def create_openai_client() -> OpenAI:
    """
    Create and return an OpenAI client using the same pattern as LLMCreator.
    Uses REMOTE_API_KEY and REMOTE_BASE_URL from environment variables.
    """
    # Load environment variables (same as LLMCreator)
    load_dotenv(override=True)
    
    # Use REMOTE_API_KEY and REMOTE_BASE_URL (same as LLMCreator for remote_model=True)
    api_key = os.getenv("REMOTE_API_KEY")
    base_url = os.getenv("REMOTE_BASE_URL")
    
    if not api_key:
        raise ValueError(
            "REMOTE_API_KEY not found in environment variables. "
            "Please set REMOTE_API_KEY in your .env file. "
            "This follows the same pattern as LLMCreator for remote models."
        )
    
    # Create client with base_url if provided (same as LLMCreator pattern)
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    else:
        return OpenAI(api_key=api_key)


def load_meta_prompt(prompt_name: str) -> str:
    """
    Load a meta-prompt from ape_generated_contents/meta_prompts/mcp_scripts/.
    
    Args:
        prompt_name: Name of the prompt file (e.g., 'direct_underlying_script_prompt.md')
        
    Returns:
        Content of the meta-prompt as a string
    """
    meta_prompt_path = project_root / "ape_generated_contents" / "meta_prompts" / "mcp_scripts" / prompt_name
    
    if not meta_prompt_path.exists():
        raise FileNotFoundError(f"Meta-prompt not found: {meta_prompt_path}")
    
    with open(meta_prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


def parse_ttl_tbox(ontology_path: str) -> Dict[str, any]:
    """
    Parse T-Box ontology TTL to extract entity classes, properties, and relationships.
    
    Args:
        ontology_path: Path to the TTL file
        
    Returns:
        Dictionary with:
        - namespace_uri: Base namespace URI
        - classes: List of OWL classes (local names)
        - object_properties: List of object properties with domain/range
        - datatype_properties: List of datatype properties with domain/range
        - class_hierarchy: Parent-child relationships
    """
    g = Graph()
    g.parse(ontology_path, format='turtle')
    
    # Find the main namespace (assuming it's defined in the ontology)
    namespaces = {str(ns): prefix for prefix, ns in g.namespaces()}
    
    # Try to find the ontology namespace (usually the one with most classes)
    ontology_ns = None
    max_classes = 0
    for ns_uri in namespaces.keys():
        if ns_uri in [str(RDF), str(RDFS), str(OWL), 'http://www.w3.org/XML/1998/namespace']:
            continue
        count = len(list(g.subjects(RDF.type, OWL.Class)))
        if count > max_classes:
            max_classes = count
            ontology_ns = ns_uri
    
    if ontology_ns is None:
        # Fallback: use first non-standard namespace
        for ns_uri in namespaces.keys():
            if ns_uri not in [str(RDF), str(RDFS), str(OWL)]:
                ontology_ns = ns_uri
                break
    
    # Extract classes
    classes = []
    for cls in g.subjects(RDF.type, OWL.Class):
        if str(cls).startswith(str(ontology_ns)):
            local_name = str(cls).replace(str(ontology_ns), '')
            classes.append(local_name)
    
    # Extract object properties
    object_properties = []
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        if str(prop).startswith(str(ontology_ns)):
            local_name = str(prop).replace(str(ontology_ns), '')
            
            # Get domain and range
            domains = [str(d).replace(str(ontology_ns), '') for d in g.objects(prop, RDFS.domain)]
            ranges = [str(r).replace(str(ontology_ns), '') for r in g.objects(prop, RDFS.range)]
            
            object_properties.append({
                'name': local_name,
                'domains': domains,
                'ranges': ranges
            })
    
    # Extract datatype properties
    datatype_properties = []
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        if str(prop).startswith(str(ontology_ns)):
            local_name = str(prop).replace(str(ontology_ns), '')
            
            # Get domain
            domains = [str(d).replace(str(ontology_ns), '') for d in g.objects(prop, RDFS.domain)]
            
            datatype_properties.append({
                'name': local_name,
                'domains': domains
            })
    
    # Extract class hierarchy
    class_hierarchy = {}
    for cls in g.subjects(RDF.type, OWL.Class):
        if str(cls).startswith(str(ontology_ns)):
            local_name = str(cls).replace(str(ontology_ns), '')
            parents = []
            for parent in g.objects(cls, RDFS.subClassOf):
                if str(parent).startswith(str(ontology_ns)):
                    parent_name = str(parent).replace(str(ontology_ns), '')
                    parents.append(parent_name)
            if parents:
                class_hierarchy[local_name] = parents
    
    return {
        'namespace_uri': ontology_ns,
        'classes': sorted(classes),
        'object_properties': object_properties,
        'datatype_properties': datatype_properties,
        'class_hierarchy': class_hierarchy
    }


def load_ontology_parsed_md(ontology_path: str) -> str:
    """Load the parsed markdown version of an ontology."""
    # Try to load the _parsed.md version first
    base_path = str(Path(ontology_path).with_suffix(""))
    md_path = f"{base_path}_parsed.md"
    
    if Path(md_path).exists():
        with open(md_path, 'r', encoding='utf-8') as f:
            return f.read()
    else:
        # Fallback to raw TTL
        with open(ontology_path, 'r', encoding='utf-8') as f:
            return f.read()


def build_underlying_script_prompt(ontology_md: str, ontology_name: str) -> str:
    """Build the prompt for generating an underlying MCP script."""
    
    # Read reference implementation for examples
    ref_script_path = Path(project_root) / "sandbox" / "code" / "mcp_creation" / "mcp_creation.py"
    ref_script_snippet = ""
    if ref_script_path.exists():
        with open(ref_script_path, 'r', encoding='utf-8') as f:
            ref_code = f.read()
            # Extract guard system, check functions, and step creation examples
            ref_script_snippet = ref_code[:25000]  # First 25k chars show key patterns
    
    prompt = f"""You are a Python code generation expert. Generate a complete MCP (Model Context Protocol) creation script for the {ontology_name} ontology.

**Ontology Definition:**

{ontology_md}

**CRITICAL: Reference Implementation Patterns**

Below is a reference implementation showing key patterns you MUST follow. Study this carefully:

```python
{ref_script_snippet}
```

**Your Task:**

Generate a Python file named `{ontology_name}_creation.py` that implements functions to create instances of all classes defined in the ontology above, following the patterns shown in the reference implementation.

**CRITICAL REQUIREMENTS:**

1. **Import Structure (MUST INCLUDE universal_utils):**
   ```python
   import os
   import re
   import uuid
   import time
   import tempfile
   import unicodedata
   import html
   from contextlib import contextmanager
   from typing import Optional, Tuple, List, Dict, Literal as LiteralType, Union
   from datetime import datetime, timezone
   import hashlib
   from filelock import FileLock
   from rdflib import Graph, Namespace, URIRef, Literal
   from rdflib.namespace import RDF, RDFS, OWL, XSD
   from models.locations import DATA_DIR
   import builtins as _bi
   import json
   
   # Import utilities from universal_utils (CRITICAL - provides many helpers)
   from ai_generated_contents_candidate.scripts.universal_utils import (
       _read_global_state, get_memory_paths, locked_graph as _base_locked_graph,
       _mint_hash_iri, _is_abs_iri, _safe_parent, _iri_exists,
       _find_by_type_and_label, _get_label, _set_single_label,
       _require_existing, _slugify, _sanitize_label, _format_success,
       _list_instances_with_label, _to_pos_int
   )
   ```

2. **Namespace Definitions:**
   - Define ALL necessary RDF namespaces from the ontology
   - Include standard namespaces: RDF, RDFS, OWL, XSD
   - Define namespace for this ontology and related ontologies (OM-2, etc.)

3. **Global State & Memory Management:**
   - Use `_read_global_state()` from universal_utils (already implemented)
   - Use `get_memory_paths()` from universal_utils (already implemented)
   - Create a custom `locked_graph()` wrapper that adds domain-specific namespace bindings
   - Implement `_current_top_entity_iri()` to get active entity from global state

4. **Guard System for check_existing Functions (CRITICAL - prevents repeated calls):**
   ```python
   # Guard state management (from reference implementation)
   def _guard_paths() -> Tuple[str, str]:
       \"\"\"Return (guard_json, guard_lock) for tracking check calls.\"\"\"
       # Implementation from reference
       
   def _load_guard_state() -> Dict[str, object]:
       \"\"\"Load guard state from JSON.\"\"\"
       # Implementation from reference
       
   def _save_guard_state(state: Dict[str, object]) -> None:
       \"\"\"Save guard state to JSON.\"\"\"
       # Implementation from reference
       
   def _guard_note_check(kind: str) -> None:
       \"\"\"Record a check function call.\"\"\"
       # Implementation from reference
       
   def _guard_note_noncheck() -> None:
       \"\"\"Record a non-check function call (resets counter).\"\"\"
       # Implementation from reference
       
   def _guard_check(func):
       \"\"\"Decorator for check_existing_* functions.\"\"\"
       # Implementation from reference
       
   def _guard_noncheck(func):
       \"\"\"Decorator for creation functions.\"\"\"
       # Implementation from reference
   ```

5. **Check Existing Functions (MUST IMPLEMENT FOR ALL MAJOR TYPES):**
   ```python
   @_guard_check
   def check_existing_<EntityType>() -> str:
       \"\"\"List existing <EntityType> instances (IRI and label).
       Avoid repeated calls; results won't change unless state changed between calls.\"\"\"
       with locked_graph() as g:
           return "\\n".join(_list_instances_with_label(g, NAMESPACE.<EntityType>))
   ```
   
   MUST implement check_existing functions for:
   - All major entity types (ChemicalInput, Supplier, etc.)
   - All quantity types (Duration, Temperature, Pressure, Volume, etc.)
   - All equipment types (Vessels, Devices, etc.)
   - All step types
   
   **CRITICAL - Also implement these special check functions:**
   ```python
   @_guard_check
   def check_existing_steps() -> str:
       \"\"\"List existing steps for active synthesis with order, label, type, and IRI.\"\"\"
       with locked_graph() as g:
           cur = _current_top_entity_iri()
           if cur is None:
               return "No active synthesis in global state"
           syn = URIRef(str(cur))
           steps = [o for o in g.objects(syn, ONTOSYN.hasSynthesisStep)]
           lines: List[str] = []
           for st in steps:
               # Extract order
               order_vals = []
               for o in g.objects(st, ONTOSYN.hasOrder):
                   try:
                       order_vals.append(int(str(o)))
                   except Exception:
                       pass
               order_txt = str(order_vals[0]) if order_vals else "N/A"
               # Extract label
               label_txt = _get_label(g, st) or "(no label)"
               # Extract type
               type_txt = None
               for tp in g.objects(st, RDF.type):
                   type_txt = str(tp)
                   break
               type_txt = type_txt or "(no type)"
               lines.append(f"order={{order_txt}} | label={{label_txt}} | type={{type_txt}} | iri={{st}}")
       return "\\n".join(lines)
   
   @_guard_noncheck
   def check_and_report_order_consistency() -> str:
       \"\"\"Check for duplicate and missing hasOrder values for active synthesis.\"\"\"
       with locked_graph() as g:
           cur = _current_top_entity_iri()
           if cur is None:
               return "No active synthesis"
           syn = URIRef(str(cur))
           steps = [o for o in g.objects(syn, ONTOSYN.hasSynthesisStep)]
           orders: List[int] = []
           index: Dict[int, List[str]] = {{}}
           for st in steps:
               for o in g.objects(st, ONTOSYN.hasOrder):
                   try:
                       val = int(str(o))
                       orders.append(val)
                       index.setdefault(val, []).append(str(st))
                   except Exception:
                       pass
           if not orders:
               return "No hasOrder values found."
           max_order = max(orders)
           expected = set(range(1, max_order + 1))
           present = set(orders)
           missing = sorted(list(expected - present))
           duplicates = {{val: ids for val, ids in index.items() if len(ids) > 1}}
           msg = [f"Checked {{len(steps)}} steps; max order={{max_order}}"]
           if missing:
               msg.append(f"Missing orders: {{missing}}")
           else:
               msg.append("No missing orders.")
           if duplicates:
               msg.append("Duplicate order assignments:")
               for val, ids in duplicates.items():
                   msg.append(f"  order {{val}}: {{ids}}")
           else:
               msg.append("No duplicate orders.")
       return "\\n".join(msg)
   ```

6. **Relationship-Building Functions (CRITICAL - MUST IMPLEMENT):**
   
   These `add_*` functions link entities together. **DO NOT SKIP THESE!**
   
   **Supplier Attachment (3 variants required):**
   ```python
   @_guard_noncheck
   def add_supplier_to_chemical_input(
       chemical_input_iri: str,
       supplier_iri: str,
       is_existing: bool = False,
       supplier_name: str = None
   ) -> str:
       \"\"\"Link Supplier to ChemicalInput via ontosyn:isSuppliedBy. Returns JSON envelope.\"\"\"
       def _json_line(payload: Dict[str, object]) -> str:
           return json.dumps(payload, separators=(",", ":")).replace("\\n", " ")
       
       try:
           with locked_graph() as g:
               # Validate chemical input exists
               ci, msg = _require_existing(g, chemical_input_iri, ONTOSYN.ChemicalInput, "chemical_input_iri")
               if ci is None:
                   return _json_line({{"status": "error", "iri": None, "created": False, "already_attached": False, "retryable": True, "code": "NOT_FOUND", "message": "chemical_input_iri not found", "supplier_iri": None}})
               
               # Resolve or mint Supplier
               created_supplier_entity = False
               unknown_supplier = False
               sup = None
               if is_existing:
                   sup, msg2 = _require_existing(g, supplier_iri, ONTOSYN.Supplier, "supplier_iri")
                   if sup is None:
                       return _json_line({{"status": "error", "iri": str(ci), "created": False, "already_attached": False, "retryable": True, "code": "NOT_FOUND", "message": "supplier_iri not found", "supplier_iri": None}})
               else:
                   if not supplier_name or not str(supplier_name).strip():
                       supplier_name = "N/A"
                   sanitized = _sanitize_label(supplier_name)
                   if sanitized.strip().upper() == "N/A":
                       unknown_supplier = True
                   existing_sup = _find_by_type_and_label(g, ONTOSYN.Supplier, sanitized)
                   if existing_sup is not None:
                       sup = existing_sup
                   else:
                       sup = _mint_hash_iri("Supplier")
                       g.add((sup, RDF.type, ONTOSYN.Supplier))
                       _set_single_label(g, sup, sanitized)
                       created_supplier_entity = True
               
               # Idempotency check
               if (ci, ONTOSYN.isSuppliedBy, sup) in g:
                   return _json_line({{"status": "ok", "iri": str(ci), "created": False, "already_attached": True, "retryable": False, "code": None, "supplier_iri": str(sup), "message": "Supplier already attached."}})
               
               # Attach relation
               g.add((ci, ONTOSYN.isSuppliedBy, sup))
           
           _export_snapshot_silent()
           return _json_line({{"status": "ok", "iri": str(ci), "created": True, "already_attached": False, "retryable": not unknown_supplier, "code": None, "supplier_iri": str(sup), "message": "Supplier attached."}})
       except Exception as e:
           return _json_line({{"status": "error", "iri": None, "created": False, "already_attached": False, "retryable": True, "code": "INTERNAL_ERROR", "message": str(e), "supplier_iri": None}})
   
   @_guard_noncheck
   def add_known_supplier_to_chemical_input(chemical_input_iri: str, supplier_iri: str) -> str:
       \"\"\"Attach existing Supplier (by IRI) to ChemicalInput.\"\"\"
       with locked_graph() as g:
           ci, _ = _require_existing(g, chemical_input_iri, ONTOSYN.ChemicalInput, "chemical_input_iri")
           if ci is None:
               return _format_error("chemical_input_iri not found", code="NOT_FOUND", retryable=False)
           sup, _ = _require_existing(g, supplier_iri, ONTOSYN.Supplier, "supplier_iri")
           if sup is None:
               return _format_error("supplier_iri not found", code="NOT_FOUND", retryable=False)
           
           is_unknown = (_get_label(g, sup) or "").strip().upper() == "N/A"
           
           if (ci, ONTOSYN.isSuppliedBy, sup) in g:
               return _format_success_json(ci, "Supplier already attached.", created=False, already_attached=True, retryable=False, supplier_iri=str(sup))
           g.add((ci, ONTOSYN.isSuppliedBy, sup))
       _export_snapshot_silent()
       return _format_success_json(ci, "Supplier attached.", created=True, already_attached=False, retryable=not is_unknown, supplier_iri=str(sup))
   
   @_guard_noncheck
   def add_new_supplier_to_chemical_input(chemical_input_iri: str, supplier_name: str) -> str:
       \"\"\"Create (or reuse) Supplier by name, then attach to ChemicalInput.\"\"\"
       name = (supplier_name or "").strip()
       if not name:
           return _format_error("Supplier name is empty", code="VALIDATION_FAILED", retryable=False)
       
       with locked_graph() as g:
           ci, _ = _require_existing(g, chemical_input_iri, ONTOSYN.ChemicalInput, "chemical_input_iri")
           if ci is None:
               return _format_error("chemical_input_iri not found", code="NOT_FOUND", retryable=False)
           
           sanitized = _sanitize_label(name)
           is_unknown = sanitized.upper() == "N/A"
           
           sup = _find_by_type_and_label(g, ONTOSYN.Supplier, sanitized)
           if sup is None:
               sup = _mint_hash_iri("Supplier")
               g.add((sup, RDF.type, ONTOSYN.Supplier))
               _set_single_label(g, sup, sanitized)
               if is_unknown:
                   g.add((sup, ONTOSYN.isUnknown, Literal(True)))
           
           if (ci, ONTOSYN.isSuppliedBy, sup) in g:
               return _format_success_json(ci, "Supplier already attached.", created=False, already_attached=True, retryable=False, supplier_iri=str(sup))
           g.add((ci, ONTOSYN.isSuppliedBy, sup))
       _export_snapshot_silent()
       return _format_success_json(ci, "Supplier attached.", created=True, already_attached=False, retryable=not is_unknown, supplier_iri=str(sup))
   ```
   
   **Amount Addition:**
   ```python
   @_guard_noncheck
   def add_amount_to_chemical_input(chemical_input_iri: str, amount: str) -> str:
       \"\"\"Add additional amount to existing ChemicalInput. Allows multiple amounts.\"\"\"
       def _json_line(payload: Dict[str, object]) -> str:
           return json.dumps(payload, separators=(",", ":")).replace("\\n", " ")
       
       try:
           if not amount or str(amount).strip().upper() == "N/A":
               return _json_line({{"status": "error", "iri": chemical_input_iri, "created": False, "already_attached": False, "retryable": False, "code": "INVALID_AMOUNT", "message": "Amount cannot be empty or 'N/A'."}})
           
           with locked_graph() as g:
               ci_iri = URIRef(chemical_input_iri)
               if not _iri_exists(g, ci_iri):
                   return _json_line({{"status": "error", "iri": chemical_input_iri, "created": False, "already_attached": False, "retryable": False, "code": "NOT_FOUND", "message": "ChemicalInput IRI not found."}})
               
               if (ci_iri, RDF.type, ONTOSYN.ChemicalInput) not in g:
                   return _json_line({{"status": "error", "iri": chemical_input_iri, "created": False, "already_attached": False, "retryable": False, "code": "WRONG_TYPE", "message": "IRI is not a ChemicalInput."}})
               
               amount_normalized = str(amount).strip()
               existing_amounts = [str(amt).strip() for amt in g.objects(ci_iri, ONTOSYN.hasAmount)]
               if amount_normalized in existing_amounts:
                   return _json_line({{"status": "ok", "iri": chemical_input_iri, "created": False, "already_attached": True, "retryable": False, "code": "ALREADY_EXISTS", "message": f"Amount '{{amount_normalized}}' already exists."}})
               
               g.add((ci_iri, ONTOSYN.hasAmount, Literal(amount_normalized)))
           
           _export_snapshot_silent()
           return _json_line({{"status": "ok", "iri": chemical_input_iri, "created": True, "already_attached": False, "retryable": False, "code": None, "message": f"Added amount '{{amount_normalized}}'."}})
       except Exception as e:
           return _json_line({{"status": "error", "iri": chemical_input_iri if 'chemical_input_iri' in locals() else None, "created": False, "already_attached": False, "retryable": True, "code": "INTERNAL_ERROR", "message": str(e)}})
   ```
   
   **Step Attachments:**
   ```python
   @_guard_noncheck
   def add_chemical_to_add_step(add_step_iri: str, chemical_input_iri: str) -> str:
       \"\"\"Attach ChemicalInput to Add step via hasAddedChemicalInput.\"\"\"
       with locked_graph() as g:
           step, msg = _require_existing(g, add_step_iri, ONTOSYN.Add, "add_step_iri")
           if step is None:
               raise ValueError(msg or "add_step_iri not found")
           ci, msgci = _require_existing(g, chemical_input_iri, ONTOSYN.ChemicalInput, "chemical_input_iri")
           if ci is None:
               raise ValueError(msgci or "chemical_input_iri not found")
           
           if (step, ONTOSYN.hasAddedChemicalInput, ci) not in g:
               g.add((step, ONTOSYN.hasAddedChemicalInput, ci))
           
           if (step, ONTOSYN.hasAddedChemicalInput, ci) not in g:
               raise ValueError(f"Failed to attach ChemicalInput")
           msg_out = f"Attached ChemicalInput '{{_get_label(g, ci)}}' to Add step '{{_get_label(g, step)}}'."
       _export_snapshot_silent()
       return _format_success(step, msg_out)
   
   @_guard_noncheck
   def add_vessel_to_step(step_iri: str, vessel_iri: str) -> str:
       \"\"\"Attach Vessel to step via hasVessel.\"\"\"
       with locked_graph() as g:
           st, msgst = _require_existing(g, step_iri, ONTOSYN.SynthesisStep, "step_iri")
           if st is None:
               raise ValueError(msgst or "step_iri not found")
           v, msgv = _require_existing(g, vessel_iri, ONTOSYN.Vessel, "vessel_iri")
           if v is None:
               raise ValueError(msgv or "vessel_iri not found")
           if (st, ONTOSYN.hasVessel, v) not in g:
               g.add((st, ONTOSYN.hasVessel, v))
           msg = f"Attached Vessel '{{_get_label(g, v)}}' to step '{{_get_label(g, st)}}'."
       _export_snapshot_silent()
       return _format_success(st, msg)
   
   @_guard_noncheck
   def add_vessel_environment_to_step(step_iri: str, vessel_env_iri: str) -> str:
       \"\"\"Attach VesselEnvironment to step via hasVesselEnvironment.\"\"\"
       with locked_graph() as g:
           st, msgst = _require_existing(g, step_iri, ONTOSYN.SynthesisStep, "step_iri")
           if st is None:
               raise ValueError(msgst or "step_iri not found")
           ve, msgve = _require_existing(g, vessel_env_iri, ONTOSYN.VesselEnvironment, "vessel_env_iri")
           if ve is None:
               raise ValueError(msgve or "vessel_env_iri not found")
           if (st, ONTOSYN.hasVesselEnvironment, ve) not in g:
               g.add((st, ONTOSYN.hasVesselEnvironment, ve))
           msg = f"Attached VesselEnvironment '{{_get_label(g, ve)}}' to step '{{_get_label(g, st)}}'."
       _export_snapshot_silent()
       return _format_success(st, msg)
   
   @_guard_noncheck
   def add_heat_chill_device_to_step(step_iri: str, device_iri: str) -> str:
       \"\"\"Attach HeatChillDevice to HeatChill step via hasHeatChillDevice.\"\"\"
       with locked_graph() as g:
           st, msgst = _require_existing(g, step_iri, ONTOSYN.HeatChill, "step_iri")
           if st is None:
               raise ValueError(msgst or "step_iri not found or not a HeatChill step")
           d, msgd = _require_existing(g, device_iri, ONTOSYN.HeatChillDevice, "device_iri")
           if d is None:
               raise ValueError(msgd or "device_iri not found")
           if (st, ONTOSYN.hasHeatChillDevice, d) not in g:
               g.add((st, ONTOSYN.hasHeatChillDevice, d))
           msg = f"Attached HeatChillDevice '{{_get_label(g, d)}}' to HeatChill step '{{_get_label(g, st)}}'."
       _export_snapshot_silent()
       return _format_success(st, msg)
   ```
   
   **Helper functions for JSON responses:**
   ```python
   def _json_line(d: dict) -> str:
       return json.dumps(d, ensure_ascii=False, separators=(',', ':'))
   
   def _format_error(message: str, *, code: str="VALIDATION_FAILED", retryable: bool=False, iri: str|None=None, **extra) -> str:
       payload = {{
           "status": "error",
           "iri": iri,
           "created": False,
           "already_attached": False,
           "retryable": bool(retryable),
           "code": code,
           "message": message,
       }}
       if extra:
           payload.update(extra)
       return _json_line(payload)
   
   def _format_success_json(iri: URIRef|str|None, message: str, *, created: bool, already_attached: bool, retryable: bool, code: str|None=None, supplier_iri: str|None=None, **extra) -> str:
       payload = {{
           "status": "ok",
           "iri": (str(iri) if iri is not None else None),
           "created": bool(created),
           "already_attached": bool(already_attached),
           "retryable": bool(retryable),
           "code": code,
           "message": message,
       }}
       if supplier_iri is not None:
           payload["supplier_iri"] = supplier_iri
       if extra:
           payload.update(extra)
       return _json_line(payload)
   ```

7. **Entity Creation Functions with JSON Responses:**
   For critical entities (like ChemicalInput, Supplier), use JSON envelope responses:
   ```python
   def create_<EntityName>(...) -> str:
       \"\"\"Create <EntityName> with JSON response envelope.\"\"\"
       def _json_line(payload: Dict[str, object]) -> str:
           return json.dumps(payload, separators=(",", ":")).replace("\\n", " ")
       
       try:
           with locked_graph() as g:
               # Validation checks
               if <validation_fails>:
                   return _json_line({{
                       "status": "error",
                       "iri": None,
                       "created": False,
                       "already_attached": False,
                       "retryable": False,
                       "code": "VALIDATION_ERROR",
                       "message": "Error message"
                   }})
               
               # Check for duplicates
               existing = _find_by_type_and_label(g, NAMESPACE.<EntityName>, sanitized_label)
               if existing is not None:
                   return _json_line({{
                       "status": "error",
                       "iri": None,
                       "created": False,
                       "already_attached": False,
                       "retryable": False,
                       "code": "ALREADY_EXISTS",
                       "message": "Entity already exists."
                   }})
               
               # Create entity
               iri = _mint_hash_iri("<EntityName>")
               g.add((iri, RDF.type, NAMESPACE.<EntityName>))
               _set_single_label(g, iri, sanitized_label)
               # Add properties...
           
           _export_snapshot_silent()
           return _json_line({{
               "status": "ok",
               "iri": str(iri),
               "created": True,
               "already_attached": False,
               "retryable": True,
               "code": None,
               "message": "Created successfully."
           }})
       except Exception as e:
           return _json_line({{
               "status": "error",
               "iri": None,
               "created": False,
               "already_attached": False,
               "retryable": True,
               "code": "INTERNAL_ERROR",
               "message": str(e)
           }})
   ```

8. **Order Validation for Steps (CRITICAL - enforce sequential order):**
   ```python
   def _existing_orders_for_active_synthesis(g: Graph) -> List[int]:
       \"\"\"Get all existing step orders for active synthesis.\"\"\"
       orders: List[int] = []
       top_iri = _current_top_entity_iri()
       if top_iri is None:
           return orders
       for st in g.objects(top_iri, ONTOSYN.hasSynthesisStep):
           for o in g.objects(st, ONTOSYN.hasOrder):
               try:
                   orders.append(int(str(o)))
               except Exception:
                   pass
       return sorted(set(orders))
   
   def _order_validation_message(existing_orders: List[int], new_order: int) -> Tuple[bool, str]:
       \"\"\"Validate step order with strict sequential enforcement.
       Rules: First order must be 1, subsequent orders must be contiguous.\"\"\"
       if new_order in existing_orders:
           return False, f"order {{new_order}} already exists"
       if new_order < 1:
           return False, "order must be >= 1"
       if not existing_orders:
           if new_order == 1:
               return True, ""
           else:
               return False, f"First step order must be 1, but got {{new_order}}"
       expected_next = max(existing_orders) + 1
       if new_order == expected_next:
           return True, ""
       elif new_order > expected_next:
           return False, f"Order skipping detected. Expected {{expected_next}}, got {{new_order}}"
       else:
           return False, f"Cannot add order {{new_order}}. Next expected is {{expected_next}}"
   ```

9. **Auto-Create Helpers for Quantities and Equipment:**
   ```python
   def _find_or_create_quantity(g: Graph, class_uri: URIRef, value: float, unit_label: str) -> Optional[URIRef]:
       \"\"\"Find existing quantity by type, value, and unit, or create new one.\"\"\"
       # Implementation from reference (reuse existing quantities)
       
   def _find_or_create_vessel(g: Graph, vessel_name: str, vessel_type_name: str) -> Optional[URIRef]:
       \"\"\"Find existing vessel by name and type, or create new one.\"\"\"
       # Implementation from reference (reuse or create vessels)
       
   def _find_or_create_vessel_environment(g: Graph, vessel_env_name: str) -> Optional[URIRef]:
       \"\"\"Find existing vessel environment by name, or create new one.\"\"\"
       # Implementation from reference
   ```

10. **Step Creation with Pre-validation (CRITICAL - no side effects until all checks pass):**
   ```python
   def create_<StepType>_step(
       name: str,
       comment: str,
       order: int,
       # ... other parameters
       vessel_name: Optional[str] = None,
       vessel_type_name: Optional[str] = None,
       vessel_env_name: Optional[str] = None,
       duration_value: Optional[float] = None,
       duration_unit: Optional[str] = None,
       # ... specific parameters
   ) -> str:
       \"\"\"Create <StepType> step with comprehensive validation.\"\"\"
       with locked_graph() as g:
           # PRE-VALIDATION (before creating anything)
           top_iri = _current_top_entity_iri()
           if top_iri is None:
               raise ValueError("No active synthesis in global state")
           
           # Validate order FIRST
           existing_orders = _existing_orders_for_active_synthesis(g)
           order_int = _to_pos_int(order, name="order")
           ok, msg = _order_validation_message(existing_orders, order_int)
           if not ok:
               raise ValueError(msg)
           
           # Validate all referenced IRIs
           if some_iri_param:
               entity, msg = _require_existing(g, some_iri_param, EXPECTED_TYPE, "param_name")
               if entity is None:
                   raise ValueError(msg or "Entity not found")
           
           # AUTO-CREATE quantities and equipment (reuse existing if possible)
           duration_iri = None
           if duration_value is not None and duration_unit is not None:
               d = _find_or_create_quantity(g, OM2.Duration, duration_value, duration_unit)
               duration_iri = str(d)
           
           vessel_iri = None
           if vessel_name is not None and vessel_type_name is not None:
               v = _find_or_create_vessel(g, vessel_name, vessel_type_name)
               vessel_iri = str(v)
           
           vessel_env_iri = None
           if vessel_env_name is not None:
               ve = _find_or_create_vessel_environment(g, vessel_env_name)
               vessel_env_iri = str(ve)
           
           # ALL CHECKS PASSED -> Now create the step
           sanitized_name = _sanitize_label(name)
           iri = _mint_hash_iri("<StepType>")
           g.add((iri, RDF.type, ONTOSYN.<StepType>))
           g.add((iri, RDF.type, ONTOSYN.SynthesisStep))
           g.add((iri, RDFS.comment, Literal(str(comment))))
           _set_single_label(g, iri, sanitized_name)
           
           # Add order
           g.add((iri, ONTOSYN.hasOrder, Literal(order_int, datatype=XSD.integer)))
           
           # Add duration, vessel, vessel_env if present
           if duration_iri:
               g.add((iri, ONTOSYN.hasStepDuration, URIRef(duration_iri)))
           if vessel_iri:
               g.add((iri, ONTOSYN.hasVessel, URIRef(vessel_iri)))
           if vessel_env_iri:
               g.add((iri, ONTOSYN.hasVesselEnvironment, URIRef(vessel_env_iri)))
           
           # Add step-specific properties...
           
           # Link to synthesis
           g.add((top_iri, ONTOSYN.hasSynthesisStep, iri))
       
       _export_snapshot_silent()
       return _format_success(iri, f"Created <StepType> step '{{sanitized_name}}' order={{order_int}}")
   ```

11. **Export Functions:**
    ```python
    @_guard_noncheck
    def init_memory(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:
        \"\"\"Initialize or resume memory graph.\"\"\"
        # Implementation
        
    @_guard_noncheck
    def export_memory() -> str:
        \"\"\"Export entire graph to TTL file.\"\"\"
        # Implementation
        
    def _export_snapshot_silent() -> str:
        \"\"\"Export snapshot silently (for internal use after each modification).\"\"\"
        _guard_note_noncheck()
        try:
            return export_memory()
        except Exception:
            return ""
    ```

12. **Allowed Value Sets (use Literal types for validation):**
    ```python
    # Runtime validation sets
    ALLOWED_VESSEL_TYPES = {{
        "type1", "type2", "type3", ...
    }}
    
    ALLOWED_VESSEL_ENVIRONMENTS = {{
        "env1", "env2", ...
    }}
    
    # Validate in functions:
    if vessel_type_name not in ALLOWED_VESSEL_TYPES:
        allowed_list = ", ".join(f"'{{vt}}'" for vt in sorted(ALLOWED_VESSEL_TYPES))
        raise ValueError(f"Invalid vessel_type_name: '{{vessel_type_name}}'. Must be one of: {{allowed_list}}")
    ```

**Output Format:**

Return ONLY the complete Python code for `{ontology_name}_creation.py`. 
Do NOT include any explanations, markdown formatting, or triple backticks.
Start directly with the imports and end with the last function.

The code should be production-ready, well-commented, comprehensive (including ALL check_existing functions, ALL entity creation functions, and ALL step creation functions with proper validation), and follow the patterns shown in the reference implementation exactly.
"""
    
    return prompt


def build_main_script_prompt(ontology_md: str, ontology_name: str, underlying_script_path: str) -> str:
    """Build the prompt for generating a FastMCP main interface script."""
    
    # Read reference main.py for structure examples
    ref_main_path = Path(project_root) / "sandbox" / "code" / "mcp_creation" / "main.py"
    ref_main_snippet = ""
    if ref_main_path.exists():
        with open(ref_main_path, 'r', encoding='utf-8') as f:
            ref_main_code = f.read()
            ref_main_snippet = ref_main_code[:30000]  # First 30k chars for structure
    
    # Read the underlying script to understand available functions
    with open(underlying_script_path, 'r', encoding='utf-8') as f:
        underlying_code = f.read()
    
    # Extract complete function definitions (signature + docstring)
    # This regex captures multi-line function signatures
    function_pattern = r'(^def (create_\w+|check_\w+|add_\w+|init_\w+|export_\w+|set_\w+|inspect_\w+)\([^)]*(?:\n[^)]*)*\)\s*->\s*\w+:(?:\n\s+"""[^"]*""")?)'
    function_defs = re.findall(function_pattern, underlying_code, re.MULTILINE)
    
    # Format function definitions for the prompt (keep first element of tuple)
    function_signatures = [match[0] for match in function_defs]
    
    # Also provide a substantial portion of the underlying code
    code_preview_length = min(len(underlying_code), 20000)  # Show up to 20k chars
    
    prompt = f"""You are a Python code generation expert. Generate a FastMCP server interface script for the {ontology_name} ontology.

**CRITICAL: Reference Implementation**

Below is a reference main.py implementation showing the EXACT patterns you MUST follow:

```python
{ref_main_snippet}
```

**CRITICAL REQUIREMENT:** You MUST read and analyze the underlying script code below to create tool wrappers for the ACTUAL functions that exist in it.

**Underlying Script Code Preview (First {code_preview_length} characters):**

```python
{underlying_code[:code_preview_length]}
```

**Extracted Function Definitions from Underlying Script ({len(function_signatures)} functions found):**

```python
{chr(10).join(function_signatures)}
```

**Your Task:**

Generate a Python file named `main.py` that creates a FastMCP server exposing **ALL THE ACTUAL FUNCTIONS** from the underlying script shown above, following the EXACT patterns shown in the reference implementation.

**CRITICAL INSTRUCTIONS:**

1. **Study the reference implementation** to understand the exact structure and patterns
2. **Analyze the underlying script code** to identify all functions
3. **DO NOT create generic/placeholder functions** - only wrap functions that exist
4. **Match the exact function signatures** from the underlying script (parameters, types, defaults)
5. **Import ALL functions** starting with: `create_`, `init_`, `export_`, `check_existing_`, `check_and_`, `add_`, `set_`, `inspect_`
   - **CRITICAL**: The `add_*` functions are for relationship-building (e.g., `add_supplier_to_chemical_input`, `add_chemical_to_add_step`, `add_vessel_to_step`). These MUST be included!
6. **Copy the instruction prompt style** from the reference implementation (detailed domain-specific rules)
7. **Copy the description patterns** from the reference implementation (comprehensive tool descriptions)

**Requirements:**

1. **Import Structure - CRITICAL: Import EVERY function from underlying script:**
   
   **YOU MUST:**
   - Extract ALL function names from the underlying script code provided above
   - Import EVERY function that starts with: `init_`, `export_`, `check_existing_`, `check_and_`, `create_`, `add_`, `set_`
   - **CRITICAL for `add_*` functions**: These are relationship-building functions (e.g., `add_supplier_to_chemical_input`, `add_chemical_to_add_step`, `add_amount_to_chemical_input`, `add_vessel_to_step`, `add_vessel_environment_to_step`, `add_heat_chill_device_to_step`). YOU MUST IMPORT AND WRAP ALL OF THEM!
   - Use the pattern: `function_name as _function_name`
   
   **Example (adapt based on ACTUAL functions in underlying script):**
   ```python
   from fastmcp import FastMCP
   from typing import List, Literal, Optional, Union, Dict
   from ai_generated_contents_candidate.scripts.{ontology_name}.{ontology_name}_creation import (
       # Memory API (import if they exist in underlying)
       init_memory as _init_memory,
       export_memory as _export_memory,
       
       # Check functions (import EVERY check_existing_* from underlying)
       check_existing_ChemicalInput as _check_existing_ChemicalInput,
       check_existing_Supplier as _check_existing_Supplier,
       check_existing_Duration as _check_existing_Duration,
       check_existing_Temperature as _check_existing_Temperature,
       check_existing_Pressure as _check_existing_Pressure,
       check_existing_Volume as _check_existing_Volume,
       # ... (continue for ALL check_existing_* in underlying)
       
       # Creation functions (import EVERY create_* from underlying)
       create_Add as _create_Add,
       create_ChemicalInput as _create_ChemicalInput,
       create_Temperature as _create_Temperature,
       # ... (continue for ALL create_* in underlying)
       
       # Add/Set functions (import if they exist in underlying)
       add_supplier_to_chemical_input as _add_supplier_to_chemical_input,
       add_chemical_to_add_step as _add_chemical_to_add_step,
       # ... (continue for ALL add_* in underlying)
   )
   from src.utils.global_logger import get_logger, mcp_tool_logger
   import re
   from typing import Union
   ```
   
   **CRITICAL:** The {len(function_signatures)} functions extracted above are YOUR SOURCE OF TRUTH. Import ALL of them.

2. **FastMCP Server Setup:**
   ```python
   mcp = FastMCP(name="{ontology_name}_creation")
   ```

3. **Instruction Prompt (MUST be comprehensive like reference):**
   ```python
   @mcp.prompt(name="instruction")
   def instruction_prompt():
       return (
           "{{Ontology}} MCP server for building ... knowledge graphs.\\n"
           "Domain-specific rules and guidance from the ontology.\\n\\n"
           "CRITICAL RULES:\\n"
           "- Rule 1 from ontology\\n"
           "- Rule 2 from ontology\\n"
           "- ...\\n\\n"
           "ORDERING:\\n"
           "- Ordering rules if applicable\\n"
           "...\\n"
       )
   ```

4. **Tool Wrappers - CRITICAL: Create ONE wrapper FOR EACH of the {len(function_signatures)} functions:**
   
   **YOU MUST create wrappers for:**
   - ALL init_* and export_* functions (memory management)
   - ALL check_existing_* functions (discovery)
   - ALL create_* functions (entity creation)
   - ALL add_* and set_* functions (relationship building)
   
   **Memory Management Functions:**
   ```python
   @mcp.tool(
       name="init_memory",
       description="Initialize or resume the persistent graph. Returns a success message."
   )
   @mcp_tool_logger
   def init_memory() -> str:
       return _init_memory()
   
   @mcp.tool(
       name="export_memory",
       description="Export the entire graph to a Turtle file. Returns file path."
   )
   @mcp_tool_logger
   def export_memory() -> str:
       return _export_memory()
   ```
   
   **Check Functions (with guard reminder in docstring):**
   ```python
   @mcp.tool(
       name="check_existing_<EntityType>",
       description="List existing <EntityType> (IRI and label). Use before supplying IRIs. Avoid repeated calls; results won't change unless state changed between calls."
   )
   @mcp_tool_logger
   def check_existing_<EntityType>() -> str:
       \"\"\"Avoid repeated calls; results won't change unless state changed between calls.\"\"\"
       return _check_existing_<EntityType>()
   ```
   
   **Creation Functions (with detailed parameter descriptions):**
   ```python
   @mcp.tool(
       name="create_<ClassName>",
       description="\"\"\"Create <ClassName>. 
       REQUIRED parameters: param1 (type): description
       OPTIONAL parameters: param2 (type): description
       
       Domain-specific rules:
       - Rule 1
       - Rule 2
       
       Allowed values for restricted params:
       - param: ['value1', 'value2', ...]
       
       Next: describe typical next steps
       \"\"\"
   )
   @mcp_tool_logger
   def create_<ClassName>(
       required_param: Type,
       optional_param: Optional[Type] = None,
       literal_param: Literal["val1", "val2", ...] = None,
       # ... match ALL parameters with EXACT types from underlying
   ) -> str:
       return _create_<ClassName>(
           required_param=required_param,
           optional_param=optional_param,
           literal_param=literal_param,
           # ... pass ALL parameters by name
       )
   ```
   
   **Step Creation Functions (with extensive domain rules):**
   ```python
   @mcp.tool(
       name="create_<StepType>_step",
       description="\"\"\"Create <StepType> step.
       REQUIRED: comment (string) stored as rdfs:comment
       REQUIRED: order (int) - CRITICAL: Must be sequential (1, 2, 3...)
       
       CRITICAL ORDERING RULE:
       - Steps MUST be added one by one in sequential order
       - Cannot skip orders
       - Cannot add retroactively
       
       CRITICAL VESSEL TRACKING:
       - vessel_name/vessel_type_name: REQUIRED
       - Vessels auto-created/reused by name and type
       - Use 'vessel 1' for all steps in same container
       
       Other parameters:
       - duration_value/duration_unit: optional duration
       - param1: description
       - param2: description
       
       Allowed units: 'unit1', 'unit2', ...
       Allowed vessel types: 'type1', 'type2', ...
       
       Next: describe next steps
       \"\"\"
   )
   @mcp_tool_logger
   def create_<StepType>_step(
       name: str,
       comment: str,
       order: int,
       # ... all parameters with Literal types for restricted values
       vessel_name: Optional[str] = None,
       vessel_type_name: Optional[Literal["type1", "type2", ...]] = None,
       # ...
   ) -> str:
       return _create_<StepType>_step(
           name=name,
           comment=comment,
           order=order,
           vessel_name=vessel_name,
           vessel_type_name=vessel_type_name,
           # ... pass ALL
       )
   ```

5. **Preserve Exact Signatures (CRITICAL):**
   - Match **every parameter name** exactly
   - Match **every type hint** exactly (including Literal, Optional, Union)
   - Match **every default value** exactly
   - Include **comprehensive docstrings** from reference pattern
   - Do NOT simplify or change anything

6. **Main Entry Point:**
   ```python
   if __name__ == "__main__":
       mcp.run(transport="stdio")
   ```

**VALIDATION CHECKLIST (verify before outputting):**

☐ **Imports:** Did I import ALL {len(function_signatures)} functions from the underlying script?
☐ **Wrappers:** Did I create a @mcp.tool wrapper for EACH of the {len(function_signatures)} functions?
☐ **Memory:** Did I expose init_memory and export_memory if they exist?
☐ **Check functions:** Did I expose ALL check_existing_* AND check_and_* functions (~10-20 typically)?
☐ **Create functions:** Did I expose ALL create_* functions?
☐ **Add functions (CRITICAL):** Did I expose ALL add_* relationship functions? These include:
   - add_supplier_to_chemical_input, add_known_supplier_to_chemical_input, add_new_supplier_to_chemical_input
   - add_amount_to_chemical_input
   - add_chemical_to_add_step
   - add_vessel_to_step, add_vessel_environment_to_step
   - add_heat_chill_device_to_step
   - (any other add_* functions in the underlying script)
☐ **Descriptions:** Did I make descriptions comprehensive (not just "Args: ... Returns: ...")?
☐ **Instruction:** Did I make the instruction prompt detailed with domain rules (not generic)?

**If any checklist item is ☐ (not checked), GO BACK and fix it before outputting.**

**Output Format:**

Return ONLY the complete Python code for `main.py`.
Do NOT include any explanations, markdown formatting, or triple backticks.
Start directly with the imports and end with the main entry point.

The code should be production-ready, well-commented, and follow FastMCP best practices.

**FINAL REMINDER:** You extracted {len(function_signatures)} functions from the underlying script. Your main.py MUST have {len(function_signatures)} corresponding @mcp.tool wrappers. Count them before outputting.
"""
    
    return prompt


def extract_code_from_response(response: str) -> str:
    """Extract Python code from LLM response, removing markdown formatting if present."""
    
    # Try to extract code from markdown code blocks
    code_block_pattern = r'```(?:python)?\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, response, re.DOTALL)
    
    if matches:
        # Use the largest code block (likely the main code)
        return max(matches, key=len).strip()
    
    # If no code blocks found, assume the entire response is code
    return response.strip()


async def generate_underlying_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate an underlying MCP script using direct LLM calls (no agents).
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology (e.g., 'ontosynthesis')
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts for API calls
    
    Returns:
        Path to generated script
    """
    print(f"\n📝 Generating underlying script via direct LLM call...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    
    # Load ontology
    ontology_md = load_ontology_parsed_md(ontology_path)
    
    # Build prompt
    prompt = build_underlying_script_prompt(ontology_md, ontology_name)
    
    # Create OpenAI client
    client = create_openai_client()
    
    # Call LLM API with retries
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry attempt {attempt}/{max_retries}...")
            
            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0,
                max_tokens=16000
            )
            
            # Extract code from response
            code = extract_code_from_response(response.choices[0].message.content or "")
            
            if not code:
                raise ValueError("LLM returned empty response")
            
            # Write to file
            output_path = Path(output_dir) / f"{ontology_name}_creation.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✅ Generated: {output_path}")
            print(f"   📊 Size: {len(code)} characters")
            
            return str(output_path)
            
        except Exception as e:
            last_exception = e
            print(f"   ⚠️  Attempt {attempt} failed: {e}")
            
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
    
    # All retries failed
    raise Exception(f"Failed to generate script after {max_retries} attempts: {last_exception}")


async def generate_main_script_direct(
    ontology_path: str,
    ontology_name: str,
    underlying_script_path: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate a FastMCP main script using direct LLM calls (no agents).
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        underlying_script_path: Path to underlying creation script
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts
    
    Returns:
        Path to generated script
    """
    print(f"\n📝 Generating main script via direct LLM call...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    
    # Load ontology
    ontology_md = load_ontology_parsed_md(ontology_path)
    
    # Build prompt
    prompt = build_main_script_prompt(ontology_md, ontology_name, underlying_script_path)
    
    # Create OpenAI client
    client = create_openai_client()
    
    # Call LLM API with retries
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry attempt {attempt}/{max_retries}...")
            
            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert Python developer specializing in FastMCP server development."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0,
                max_tokens=16000
            )
            
            # Extract code from response
            code = extract_code_from_response(response.choices[0].message.content or "")
            
            if not code:
                raise ValueError("LLM returned empty response")
            
            # Write to file
            output_path = Path(output_dir) / "main.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✅ Generated: {output_path}")
            print(f"   📊 Size: {len(code)} characters")
            
            return str(output_path)
            
        except Exception as e:
            last_exception = e
            print(f"   ⚠️  Attempt {attempt} failed: {e}")
            
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
    
    # All retries failed
    raise Exception(f"Failed to generate script after {max_retries} attempts: {last_exception}")


if __name__ == "__main__":
    import asyncio
    import argparse
    
    parser = argparse.ArgumentParser(description="Direct LLM-based script generation")
    parser.add_argument("--ontology", required=True, help="Path to ontology TTL file")
    parser.add_argument("--name", required=True, help="Short name of ontology")
    parser.add_argument("--model", default="gpt-4o", help="LLM model to use")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--type", choices=["underlying", "main"], required=True, help="Script type to generate")
    parser.add_argument("--underlying-script", help="Path to underlying script (required for main)")
    
    args = parser.parse_args()
    
    if args.type == "main" and not args.underlying_script:
        parser.error("--underlying-script is required for --type=main")
    
    try:
        if args.type == "underlying":
            result = asyncio.run(generate_underlying_script_direct(
                ontology_path=args.ontology,
                ontology_name=args.name,
                output_dir=args.output_dir,
                model_name=args.model
            ))
        else:  # main
            result = asyncio.run(generate_main_script_direct(
                ontology_path=args.ontology,
                ontology_name=args.name,
                underlying_script_path=args.underlying_script,
                output_dir=args.output_dir,
                model_name=args.model
            ))
        
        print(f"\n✅ Success: {result}")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

