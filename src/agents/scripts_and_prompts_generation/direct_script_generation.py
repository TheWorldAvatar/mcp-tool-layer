#!/usr/bin/env python3
"""
Direct LLM Script Generation (Domain-Agnostic)

This module provides direct LLM-based script generation that:
1. Loads domain-agnostic meta-prompts from ape_generated_contents/meta_prompts/mcp_scripts/
2. Parses T-Box ontology TTL to extract entity classes, properties, relationships
3. Fills meta-prompt templates with extracted domain-specific information
4. Calls LLM API directly (no agents, no MCP tools)
5. Writes generated code to files

The meta-prompts contain NO domain-specific hardcoded examples.
All domain-specific information comes from parsing the TTL T-Box.
"""

import os
import sys
import re
import asyncio
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple
from openai import OpenAI
from dotenv import load_dotenv
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def validate_python_syntax(code: str, filepath: str = "<generated>") -> tuple[bool, str]:
    """
    Validate Python code syntax by attempting to compile it.
    
    Returns:
        (is_valid, error_message)
    """
    try:
        compile(code, filepath, 'exec')
        return True, ""
    except SyntaxError as e:
        error_msg = f"Syntax error at line {e.lineno}: {e.msg}"
        if e.text:
            error_msg += f"\n  {e.text.strip()}"
            if e.offset:
                error_msg += f"\n  {' ' * (e.offset - 1)}^"
        return False, error_msg
    except Exception as e:
        return False, f"Compilation error: {str(e)}"


# Static list of available functions in universal_utils.py
# This list is maintained manually to match sandbox/code/universal_utils.py
UNIVERSAL_UTILS_FUNCTIONS = [
    'locked_graph',
    'init_memory',
    'export_memory',
    '_mint_hash_iri',
    '_iri_exists',
    '_find_by_type_and_label',
    '_get_label',
    '_set_single_label',
    '_ensure_type_with_label',
    '_require_existing',
    '_sanitize_label',
    '_format_success',
    '_list_instances_with_label',
    '_to_pos_int',
    '_export_snapshot_silent',
    'get_memory_paths',
    'inspect_memory',
]


def create_openai_client() -> OpenAI:
    """
    Create and return an OpenAI client using the same pattern as LLMCreator.
    Uses REMOTE_API_KEY and REMOTE_BASE_URL from environment variables.
    """
    load_dotenv(override=True)
    
    api_key = os.getenv("REMOTE_API_KEY")
    base_url = os.getenv("REMOTE_BASE_URL")
    
    if not api_key:
        raise ValueError(
            "REMOTE_API_KEY not found in environment variables. "
            "Please set REMOTE_API_KEY in your .env file."
        )
    
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
    
    # Find the main namespace (usually the one with most classes)
    namespaces = {str(ns): prefix for prefix, ns in g.namespaces()}
    
    ontology_ns = None
    max_classes = 0
    for ns_uri in namespaces.keys():
        if ns_uri in [str(RDF), str(RDFS), str(OWL), 'http://www.w3.org/XML/1998/namespace']:
            continue
        count = len([c for c in g.subjects(RDF.type, OWL.Class) if str(c).startswith(str(ns_uri))])
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


def extract_concise_ontology_structure(ontology_path: str) -> Dict[str, any]:
    """
    Extract a concise, focused structure from TTL ontology.
    
    Focus on:
    1. Class connections (object properties connecting classes)
    2. Class inputs (datatype properties for each class)
    
    Excludes:
    - rdfs:comment (verbose descriptions)
    - rdfs:label (human-readable labels)
    - Other metadata
    
    Returns:
        Dictionary with:
        - namespace_uri: Base namespace URI
        - classes: List of class names
        - class_structures: For each class, its connections and inputs
    """
    g = Graph()
    g.parse(ontology_path, format='turtle')
    
    # Find the main namespace
    namespaces = {str(ns): prefix for prefix, ns in g.namespaces()}
    ontology_ns = None
    max_classes = 0
    for ns_uri in namespaces.keys():
        if ns_uri in [str(RDF), str(RDFS), str(OWL), 'http://www.w3.org/XML/1998/namespace']:
            continue
        count = len([c for c in g.subjects(RDF.type, OWL.Class) if str(c).startswith(str(ns_uri))])
        if count > max_classes:
            max_classes = count
            ontology_ns = ns_uri
    
    if ontology_ns is None:
        for ns_uri in namespaces.keys():
            if ns_uri not in [str(RDF), str(RDFS), str(OWL)]:
                ontology_ns = ns_uri
                break
    
    def extract_classes_from_domain(domain_node):
        """Helper to extract classes from domain (handles union domains)."""
        classes_in_domain = []
        
        # Check if it's a direct class
        if str(domain_node).startswith(str(ontology_ns)):
            classes_in_domain.append(str(domain_node).replace(str(ontology_ns), ''))
        # Check if it's a blank node with unionOf
        elif isinstance(domain_node, URIRef) or (domain_node, RDF.type, OWL.Class) in g:
            # Check for unionOf
            for union_list in g.objects(domain_node, OWL.unionOf):
                # Iterate through the RDF collection
                current = union_list
                while current and current != RDF.nil:
                    first = g.value(current, RDF.first)
                    if first and str(first).startswith(str(ontology_ns)):
                        classes_in_domain.append(str(first).replace(str(ontology_ns), ''))
                    current = g.value(current, RDF.rest)
        
        return classes_in_domain
    
    # Extract all classes
    classes = []
    for cls in g.subjects(RDF.type, OWL.Class):
        if str(cls).startswith(str(ontology_ns)):
            local_name = str(cls).replace(str(ontology_ns), '')
            classes.append(local_name)
    
    # Build class structures
    class_structures = {}
    
    for class_name in classes:
        class_uri = URIRef(ontology_ns + class_name)
        
        # Find object properties where this class is the DOMAIN (what this class connects TO)
        connects_to = []
        for prop in g.subjects(RDF.type, OWL.ObjectProperty):
            if str(prop).startswith(str(ontology_ns)):
                for domain in g.objects(prop, RDFS.domain):
                    # Extract all classes from domain (handles unions)
                    domain_classes = extract_classes_from_domain(domain)
                    if class_name in domain_classes:
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        ranges = [str(r).replace(str(ontology_ns), '') for r in g.objects(prop, RDFS.range) 
                                 if str(r).startswith(str(ontology_ns))]
                        # Also handle external ranges (om-2, etc.)
                        external_ranges = [str(r) for r in g.objects(prop, RDFS.range) 
                                          if not str(r).startswith(str(ontology_ns)) and '/' in str(r)]
                        if ranges or external_ranges:
                            all_ranges = ranges + [r.split('/')[-1] for r in external_ranges if '/' in r]
                            connects_to.append({
                                'property': prop_name,
                                'target_classes': all_ranges
                            })
        
        # Find object properties where this class is the RANGE (what connects TO this class)
        connected_from = []
        for prop in g.subjects(RDF.type, OWL.ObjectProperty):
            if str(prop).startswith(str(ontology_ns)):
                for rng in g.objects(prop, RDFS.range):
                    if str(rng) == str(class_uri):
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        # Collect all domain classes (handling unions)
                        all_domain_classes = []
                        for domain in g.objects(prop, RDFS.domain):
                            all_domain_classes.extend(extract_classes_from_domain(domain))
                        if all_domain_classes:
                            connected_from.append({
                                'property': prop_name,
                                'source_classes': all_domain_classes
                            })
        
        # Find datatype properties where this class is the DOMAIN (what data/inputs this class has)
        datatype_inputs = []
        for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
            if str(prop).startswith(str(ontology_ns)):
                for domain in g.objects(prop, RDFS.domain):
                    # Extract all classes from domain (handles unions)
                    domain_classes = extract_classes_from_domain(domain)
                    if class_name in domain_classes:
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        datatype_inputs.append(prop_name)
        
        # Find subclass relationships
        parents = []
        for parent in g.objects(class_uri, RDFS.subClassOf):
            if str(parent).startswith(str(ontology_ns)):
                parent_name = str(parent).replace(str(ontology_ns), '')
                parents.append(parent_name)
        
        class_structures[class_name] = {
            'connects_to': connects_to,
            'connected_from': connected_from,
            'datatype_inputs': datatype_inputs,
            'parent_classes': parents
        }
    
    # Build class_hierarchy dict for parent-class grouping
    class_hierarchy = {}
    for class_name, structure in class_structures.items():
        if structure['parent_classes']:
            class_hierarchy[class_name] = structure['parent_classes']
    
    return {
        'namespace_uri': ontology_ns,
        'classes': sorted(classes),
        'class_structures': class_structures,
        'class_hierarchy': class_hierarchy
    }


def format_concise_structure_as_markdown(concise_structure: Dict, ontology_name: str) -> str:
    """
    Format the concise ontology structure as a markdown document.
    
    Args:
        concise_structure: Output from extract_concise_ontology_structure()
        ontology_name: Name of the ontology
        
    Returns:
        Markdown-formatted string
    """
    lines = [
        f"# Concise Ontology Structure: {ontology_name}",
        "",
        "**Auto-generated by direct script generation pipeline**",
        "",
        "This document contains the concise, focused structure extracted from the ontology TTL file.",
        "It includes only structural information needed for script generation:",
        "- Class definitions",
        "- Object property connections (domain → range)",
        "- Datatype property assignments (domain)",
        "- Class hierarchy (inheritance)",
        "- Required creation functions",
        "",
        "**Excluded:** verbose rdfs:comment fields and other metadata.",
        "",
        "---",
        "",
        f"## Namespace",
        "",
        f"`{concise_structure['namespace_uri']}`",
        "",
        "---",
        "",
        f"## Classes ({len(concise_structure['classes'])} total)",
        ""
    ]
    
    for cls in concise_structure['classes']:
        lines.append(f"- `{cls}`")
    
    # Get class structures for detailed signatures section later
    class_structures = concise_structure.get('class_structures', {})
    
    # Jump straight to detailed signatures - no misleading summary sections
    lines.extend([
        "",
        "---",
        "",
        "## Create Function Signatures",
        "",
        "**CRITICAL**: Each `create_*` function MUST include ALL parameters listed below.",
        "These are the AUTHORITATIVE signatures - use these EXACTLY when generating code.",
        ""
    ])
    
    # Add detailed function signature for each class
    for cls in sorted(concise_structure['classes']):
        class_name = cls.split('/')[-1] if '/' in cls else cls
        structure = class_structures.get(cls, {})  # classes list has full keys like "OntoSyn/Add"
        
        lines.append(f"### `create_{class_name}` Parameters:")
        lines.append("")
        lines.append("```python")
        lines.append(f"def create_{class_name}(")
        lines.append("    label: str,  # Required")
        
        # Datatype properties with type inference
        datatype_props = structure.get('datatype_inputs', [])
        for prop in sorted(datatype_props):
            prop_name = prop.split('/')[-1] if '/' in prop else prop
            
            # Infer type from property name
            if 'Order' in prop_name or 'Count' in prop_name:
                param_type = "Optional[int]"
            elif prop_name.startswith('is') or prop_name.startswith('has') and ('Vacuum' in prop_name or 'Sealed' in prop_name or 'Stirred' in prop_name or 'Repeated' in prop_name or 'Layered' in prop_name or 'Wait' in prop_name or 'Filtration' in prop_name or 'Evaporator' in prop_name):
                param_type = "Optional[bool]"
            elif 'Ph' in prop_name or 'Purity' in prop_name or 'Amount' in prop_name or 'Names' in prop_name or 'Formula' in prop_name or 'Description' in prop_name or 'Parameter' in prop_name or 'Number' in prop_name:
                param_type = "Optional[str]"
            else:
                param_type = "Optional[str]"
            
            lines.append(f"    {prop_name}: {param_type} = None,")
        
        # Object connections as label parameters for auto-creation
        # Group by property to avoid duplicates
        seen_params = set()
        for conn in structure.get('connects_to', []):
            prop = conn['property'].split('/')[-1] if '/' in conn['property'] else conn['property']
            
            # For common auxiliary entities, use simplified parameter names
            for target in conn['target_classes']:
                target_name = target.split('/')[-1] if '/' in target else target
                
                # Generate parameter name based on property and target
                if 'Vessel' in prop and 'Type' not in target_name and 'Environment' not in target_name:
                    param_name = "vessel_label"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created Vessel")
                        seen_params.add(param_name)
                        # Also add vessel_type parameter
                        if "vessel_type" not in seen_params:
                            lines.append(f"    vessel_type: Optional[str] = None,  # VesselType for auto-created Vessel")
                            seen_params.add("vessel_type")
                elif 'VesselEnvironment' in target_name:
                    param_name = "vessel_env_label"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created VesselEnvironment")
                        seen_params.add(param_name)
                elif 'Supplier' in target_name:
                    param_name = "supplied_by"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created Supplier")
                        seen_params.add(param_name)
                elif 'Material' in target_name:
                    param_name = "material_label"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created Material")
                        seen_params.add(param_name)
                elif 'MetalOrganicPolyhedron' in target_name:
                    if "represented_by_label" not in seen_params:
                        lines.append(f"    represented_by_label: Optional[str] = None,  # Auto-created MetalOrganicPolyhedron")
                        seen_params.add("represented_by_label")
                        lines.append(f"    represented_by_CCDC: Optional[str] = None,  # CCDC number for MOP")
                        seen_params.add("represented_by_CCDC")
                elif 'DocumentContext' in target_name:
                    param_name = "document_context_label"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created DocumentContext")
                        seen_params.add(param_name)
                elif 'HeatChillDevice' in target_name:
                    param_name = "heat_chill_device_label"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created HeatChillDevice")
                        seen_params.add(param_name)
                # For other connections, don't auto-create (they should use add_* functions)
        
        lines.append(") -> str:")
        lines.append("```")
        lines.append("")
    
    lines.extend([
        "---",
        "",
        "## Class Structures",
        "",
        "Detailed information about connections and inputs for each class.",
        ""
    ])
    
    for class_name in sorted(concise_structure['classes']):
        structure = concise_structure['class_structures'][class_name]
        
        lines.append(f"### `{class_name}`")
        lines.append("")
        
        if structure['parent_classes']:
            lines.append(f"**Inherits from:** {', '.join(f'`{p}`' for p in structure['parent_classes'])}")
            lines.append("")
        
        if structure['connects_to']:
            lines.append("**Connects to (via object properties):**")
            lines.append("")
            for conn in structure['connects_to']:
                targets = ', '.join(f'`{t}`' for t in conn['target_classes'])
                lines.append(f"- `{conn['property']}` → {targets}")
            lines.append("")
        
        if structure['connected_from']:
            lines.append("**Connected from (via object properties):**")
            lines.append("")
            for conn in structure['connected_from']:
                sources = ', '.join(f'`{s}`' for s in conn['source_classes'])
                lines.append(f"- `{conn['property']}` ← {sources}")
            lines.append("")
        
        if structure['datatype_inputs']:
            lines.append("**Datatype properties (inputs/data):**")
            lines.append("")
            for prop in structure['datatype_inputs']:
                lines.append(f"- `{prop}`")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # Add statistics at the end
    total_object_props = sum(
        len(s['connects_to']) + len(s['connected_from']) 
        for s in concise_structure['class_structures'].values()
    )
    total_datatype_props = sum(
        len(s['datatype_inputs']) 
        for s in concise_structure['class_structures'].values()
    )
    
    lines.extend([
        "## Statistics",
        "",
        f"- **Total Classes:** {len(concise_structure['classes'])}",
        f"- **Total Object Property Connections:** {total_object_props}",
        f"- **Total Datatype Property Assignments:** {total_datatype_props}",
        ""
    ])
    
    return "\n".join(lines)


def save_concise_structure(
    ontology_path: str, 
    ontology_name: str, 
    output_base_dir: Optional[Path] = None
) -> Path:
    """
    Extract and save the concise ontology structure as a markdown file.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology
        output_base_dir: Base output directory (defaults to ai_generated_contents_candidate)
        
    Returns:
        Path to the saved markdown file
    """
    if output_base_dir is None:
        output_base_dir = project_root / "ai_generated_contents_candidate"
    
    # Create ontology_structures subfolder
    structures_dir = output_base_dir / "ontology_structures"
    structures_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract concise structure
    concise_structure = extract_concise_ontology_structure(ontology_path)
    
    # Format as markdown
    markdown_content = format_concise_structure_as_markdown(concise_structure, ontology_name)
    
    # Save to file
    output_path = structures_dir / f"{ontology_name}_concise.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    
    return output_path


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


def build_underlying_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating an underlying MCP script using domain-agnostic meta-prompt.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        
    Returns:
        Complete prompt string with TTL-extracted information filled into meta-prompt
    """
    # Load domain-agnostic meta-prompt
    meta_prompt_template = load_meta_prompt('direct_underlying_script_prompt.md')
    
    # Extract CONCISE ontology structure (focused on connections and inputs, no verbose comments)
    concise_structure = extract_concise_ontology_structure(ontology_path)
    
    # Parse TTL for additional metadata if needed
    tbox_info = parse_ttl_tbox(ontology_path)
    
    # Load reference snippet for patterns (domain-agnostic patterns)
    ref_script_path = project_root / "sandbox" / "code" / "mcp_creation" / "mcp_creation.py"
    ref_snippet = ""
    if ref_script_path.exists():
        with open(ref_script_path, 'r', encoding='utf-8') as f:
            # Take first 20k chars showing key patterns
            ref_snippet = f.read()[:20000]
    
    # Format concise ontology structure
    ontology_structure_lines = [
        f"Namespace: {concise_structure['namespace_uri']}",
        "",
        "# Classes",
        *[f"- {cls}" for cls in concise_structure['classes']],
        "",
        "# Class Structures (Connections and Inputs)",
        ""
    ]
    
    for class_name, structure in sorted(concise_structure['class_structures'].items()):
        ontology_structure_lines.append(f"## {class_name}")
        
        if structure['parent_classes']:
            ontology_structure_lines.append(f"  Inherits from: {', '.join(structure['parent_classes'])}")
        
        if structure['connects_to']:
            ontology_structure_lines.append("  Connects to (via object properties):")
            for conn in structure['connects_to']:
                targets = ', '.join(conn['target_classes'])
                ontology_structure_lines.append(f"    - {conn['property']} → {targets}")
        
        if structure['connected_from']:
            ontology_structure_lines.append("  Connected from (via object properties):")
            for conn in structure['connected_from']:
                sources = ', '.join(conn['source_classes'])
                ontology_structure_lines.append(f"    - {conn['property']} ← {sources}")
        
        if structure['datatype_inputs']:
            ontology_structure_lines.append("  Datatype properties (inputs/data):")
            for prop in structure['datatype_inputs']:
                ontology_structure_lines.append(f"    - {prop}")
        
        ontology_structure_lines.append("")
    
    concise_ontology_str = "\n".join(ontology_structure_lines)
    
    # Format entity classes (for backward compatibility)
    entity_classes_str = "\n".join(f"- {cls}" for cls in concise_structure['classes'])
    
    # Format object properties (simplified, from concise structure)
    object_props_list = []
    for class_name, structure in concise_structure['class_structures'].items():
        for conn in structure['connects_to']:
            targets = ', '.join(conn['target_classes'])
            object_props_list.append(f"- {conn['property']}: {class_name} → {targets}")
    object_props_str = "\n".join(sorted(set(object_props_list)))
    
    # Format datatype properties (simplified, from concise structure)
    datatype_props_list = []
    for class_name, structure in concise_structure['class_structures'].items():
        for prop in structure['datatype_inputs']:
            datatype_props_list.append(f"- {prop}: domain={class_name}")
    datatype_props_str = "\n".join(sorted(set(datatype_props_list)))
    
    # Format universal_utils functions list
    universal_utils_str = "\n".join(f"- {func}" for func in UNIVERSAL_UTILS_FUNCTIONS)
    
    # Fill in the meta-prompt template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=concise_structure['namespace_uri'],
        reference_snippet=ref_snippet,
        ontology_ttl=concise_ontology_str,  # Use concise structure instead of full TTL
        entity_classes=entity_classes_str,
        object_properties=object_props_str,
        datatype_properties=datatype_props_str,
        universal_utils_functions=universal_utils_str
    )
    
    return prompt


def extract_functions_from_underlying(underlying_script_path: str) -> List[Dict[str, str]]:
    """
    Extract all function signatures from the underlying script.
    
    Returns:
        List of dictionaries with 'name' and 'signature' keys
    """
    with open(underlying_script_path, 'r', encoding='utf-8') as f:
        code = f.read()
    
    # Extract function definitions
    function_pattern = r'^def\s+(\w+)\s*\((.*?)\)\s*->\s*(\w+):'
    functions = []
    
    for match in re.finditer(function_pattern, code, re.MULTILINE):
        func_name = match.group(1)
        # Skip private functions (starting with _)
        if not func_name.startswith('_'):
            functions.append({
                'name': func_name,
                'signature': match.group(0)
            })
    
    return functions


def build_main_script_prompt(
    ontology_path: str, 
    ontology_name: str, 
    underlying_script_path: Optional[str] = None,
    base_script_path: Optional[str] = None,
    entity_script_paths: Optional[list] = None
) -> str:
    """
    Build the prompt for generating a FastMCP main script using domain-agnostic meta-prompt.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        underlying_script_path: Path to single underlying script (legacy, optional)
        base_script_path: Path to base script (for multi-script architecture)
        entity_script_paths: List of paths to entity group scripts (for multi-script architecture)
        
    Returns:
        Complete prompt string with extracted information filled into meta-prompt
    """
    # Load domain-agnostic meta-prompt
    meta_prompt_template = load_meta_prompt('direct_main_script_prompt.md')
    
    # Determine architecture
    is_multi_script = base_script_path is not None and entity_script_paths is not None and len(entity_script_paths) > 0
    
    # Extract CONCISE ontology structure (focused on connections and inputs, no verbose comments)
    concise_structure = extract_concise_ontology_structure(ontology_path)
    
    # Parse TTL for additional metadata if needed
    tbox_info = parse_ttl_tbox(ontology_path)
    
    # Load reference main.py for patterns
    ref_main_path = project_root / "sandbox" / "code" / "mcp_creation" / "main.py"
    ref_main_snippet = ""
    if ref_main_path.exists():
        with open(ref_main_path, 'r', encoding='utf-8') as f:
            # Take first 25k chars showing structure
            ref_main_snippet = f.read()[:25000]
    
    # Format concise ontology structure (simplified version for main.py)
    ontology_structure_lines = [
        f"Namespace: {concise_structure['namespace_uri']}",
        "",
        "# Entity Classes",
        *[f"- {cls}" for cls in concise_structure['classes']],
        "",
        "# Key Relationships (Object Properties)",
        ""
    ]
    
    # Collect all unique object property relationships
    relationships = set()
    for class_name, structure in concise_structure['class_structures'].items():
        for conn in structure['connects_to']:
            targets = ', '.join(conn['target_classes'])
            relationships.add(f"- {conn['property']}: {class_name} → {targets}")
    
    ontology_structure_lines.extend(sorted(relationships))
    concise_ontology_str = "\n".join(ontology_structure_lines)
    
    # Extract function signatures from underlying script(s)
    functions = []
    if is_multi_script:
        # Multi-script architecture: extract from base + all entity group scripts
        base_functions = extract_functions_from_underlying(base_script_path)
        functions.extend(base_functions)
        
        for entity_script_path in entity_script_paths:
            entity_functions = extract_functions_from_underlying(entity_script_path)
            functions.extend(entity_functions)
    elif underlying_script_path:
        # Single file architecture (legacy)
        functions = extract_functions_from_underlying(underlying_script_path)
    else:
        raise ValueError("Either (base_script_path + entity_script_paths) or underlying_script_path must be provided")
    
    # Format function signatures
    function_sigs_str = "\n".join(
        f"- {func['name']}: {func['signature']}"
        for func in functions
    )
    
    # Format entity classes
    entity_classes_str = "\n".join(f"- {cls}" for cls in concise_structure['classes'])
    
    # Format relationships (simplified)
    relationships_str = "\n".join(sorted(relationships))
    
    # Add architecture-specific info
    if is_multi_script:
        entity_script_list = "\n".join([
            f"- `{Path(path).name}`: {Path(path).stem.replace(f'{ontology_name}_creation_', '')} entities"
            for path in entity_script_paths
        ])
        
        architecture_note = f"""
**ARCHITECTURE: MULTI-SCRIPT (BASE + {len(entity_script_paths)} ENTITY GROUPS)**

Base script (`{Path(base_script_path).name}`):
- check_existing_* functions
- add_*_to_* relationship functions  
- _find_or_create_* helper functions
- Memory management wrappers (init_memory, export_memory)

Entity group scripts ({len(entity_script_paths)} files):
{entity_script_list}

**IMPORTANT**: Import functions from ALL scripts in main.py:
```python
from .{Path(base_script_path).stem} import (
    # check_existing, add_*, memory functions
)

# Import create_* functions from each entity group
{chr(10).join([f'from .{Path(path).stem} import (...)' for path in entity_script_paths])}
```
"""
    elif underlying_script_path:
        architecture_note = f"**ARCHITECTURE: SINGLE SCRIPT** (`{Path(underlying_script_path).name}`)"
    else:
        architecture_note = "**ARCHITECTURE: UNKNOWN** (No scripts provided)"
    
    # Fill in the meta-prompt template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=concise_structure['namespace_uri'],
        reference_main_snippet=ref_main_snippet,
        ontology_ttl=concise_ontology_str,  # Use concise structure instead of full TTL
        function_signatures=function_sigs_str,
        total_functions=len(functions),
        entity_classes=entity_classes_str,
        relationships=relationships_str,
        architecture_note=architecture_note
    )
    
    return prompt


def build_base_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating the BASE/INFRASTRUCTURE script (guard system, namespaces, helpers ONLY).
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        
    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_base_script_prompt.md')
    
    # Extract concise ontology structure (minimal - just namespace and classes for _find_or_create helpers)
    concise_structure = extract_concise_ontology_structure(ontology_path)
    
    # Identify common auxiliary entities that need _find_or_create helpers
    # These are typically entities that are often created as side-effects of main entity creation
    class_structures = concise_structure.get('class_structures', {})
    auxiliary_entities = []
    
    # Heuristic: entities that are range of many properties but don't have many properties themselves
    for cls_name, structure in class_structures.items():
        simple_name = cls_name.split('/')[-1]
        # Check if this entity is frequently referenced (connected_from count)
        connected_from_count = len(structure.get('connected_from', []))
        connects_to_count = len(structure.get('connects_to', []))
        
        # If it's frequently referenced but doesn't have many outgoing connections, it's likely auxiliary
        if connected_from_count >= 2 and connects_to_count <= 2:
            auxiliary_entities.append(simple_name)
    
    # Add common patterns (e.g., Vessel, Equipment, Supplier)
    common_auxiliary = ['Vessel', 'VesselType', 'VesselEnvironment', 'Supplier', 'Equipment', 
                       'HeatChillDevice', 'ChemicalInput', 'ChemicalOutput', 'MetalOrganicPolyhedron']
    all_classes = [c.split('/')[-1] for c in concise_structure['classes']]
    auxiliary_entities.extend([a for a in common_auxiliary if a in all_classes and a not in auxiliary_entities])
    
    auxiliary_entities_str = "\n".join([f"- {entity}" for entity in sorted(set(auxiliary_entities))])
    
    # Fill template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=concise_structure['namespace_uri'],
        ontology_structure=f"Auxiliary entities (need _find_or_create_ helpers):\n{auxiliary_entities_str}",
        universal_utils_functions=", ".join(UNIVERSAL_UTILS_FUNCTIONS)
    )
    
    return prompt


def build_entity_group_prompt(
    ontology_path: str, 
    ontology_name: str, 
    group_info: dict,
    available_helpers: list = None,
    available_check_functions: list = None,
    available_add_functions: list = None
) -> str:
    """
    Build the prompt for generating a single entity group script (subset of entities).
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology
        group_info: Dictionary with 'name', 'entities', 'description'
        available_helpers: List of _find_or_create_* helper function names from base script
        available_check_functions: List of check_existing_* function names from base script
        available_add_functions: List of add_* function names from base script
        
    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_entities_script_prompt.md')
    
    # Default to empty lists if not provided
    if available_helpers is None:
        available_helpers = []
    if available_check_functions is None:
        available_check_functions = []
    if available_add_functions is None:
        available_add_functions = []
    
    # Extract concise ontology structure
    full_concise_structure = extract_concise_ontology_structure(ontology_path)
    
    # Filter to only include entities in this group
    entity_names = set(group_info['entities'])
    
    # Build filtered structure
    ontology_structure_lines = []
    ontology_structure_lines.append(f"Namespace: {full_concise_structure['namespace_uri']}")
    ontology_structure_lines.append("")
    ontology_structure_lines.append(f"Entity Group: {group_info['name']}")
    ontology_structure_lines.append(f"Description: {group_info['description']}")
    ontology_structure_lines.append(f"Entities in this group: {len(entity_names)}")
    ontology_structure_lines.append("")
    
    class_structures = full_concise_structure.get('class_structures', {})
    for class_name, structure in sorted(class_structures.items()):
        # Only include classes in this group
        if class_name not in entity_names:
            continue
            
        ontology_structure_lines.append(f"## {class_name}")
        
        if structure['parent_classes']:
            ontology_structure_lines.append(f"  Inherits from: {', '.join(structure['parent_classes'])}")
        
        if structure['datatype_inputs']:
            ontology_structure_lines.append(f"  Datatype properties:")
            for prop in structure['datatype_inputs']:
                ontology_structure_lines.append(f"    - {prop}")
        
        if structure['object_connections']:
            ontology_structure_lines.append(f"  Object property connections:")
            for prop, range_cls in structure['object_connections']:
                ontology_structure_lines.append(f"    - {prop} → {range_cls}")
        
        ontology_structure_lines.append("")
    
    ontology_structure = "\n".join(ontology_structure_lines)
    
    # Format available functions from base script
    available_helpers_str = "\n".join([f"- {name}" for name in sorted(available_helpers)]) if available_helpers else "(none available)"
    available_checks_str = "\n".join([f"- {name}" for name in sorted(available_check_functions)]) if available_check_functions else "(none available)"
    available_adds_str = "\n".join([f"- {name}" for name in sorted(available_add_functions)]) if available_add_functions else "(none available)"
    
    # Fill in template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=full_concise_structure['namespace_uri'],
        ontology_structure=ontology_structure,
        universal_utils_functions=", ".join(UNIVERSAL_UTILS_FUNCTIONS),
        group_name=group_info['name'],
        group_description=group_info['description'],
        entity_count=len(entity_names),
        entity_classes_list=", ".join(sorted(entity_names)),
        available_helpers=available_helpers_str,
        available_check_functions=available_checks_str,
        available_add_functions=available_adds_str
    )
    
    return prompt


def build_entities_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating the ENTITIES script (all create_* functions).
    
    DEPRECATED: Use build_entity_group_prompt for multi-script generation.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        
    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_entities_script_prompt.md')
    
    # Extract concise ontology structure
    concise_structure = extract_concise_ontology_structure(ontology_path)
    
    # Format class structures with full details
    ontology_structure_lines = []
    ontology_structure_lines.append(f"Namespace: {concise_structure['namespace_uri']}")
    ontology_structure_lines.append("")
    ontology_structure_lines.append(f"Total Classes: {len(concise_structure['classes'])}")
    ontology_structure_lines.append("")
    
    class_structures = concise_structure.get('class_structures', {})
    for class_name, structure in sorted(class_structures.items()):
        ontology_structure_lines.append(f"## {class_name}")
        
        if structure['parent_classes']:
            ontology_structure_lines.append(f"  Inherits from: {', '.join(structure['parent_classes'])}")
        
        if structure['datatype_inputs']:
            ontology_structure_lines.append("  Datatype properties:")
            for prop in structure['datatype_inputs']:
                ontology_structure_lines.append(f"    - {prop}")
        
        if structure['connects_to']:
            ontology_structure_lines.append("  Object properties:")
            for conn in structure['connects_to']:
                targets = ', '.join(conn['target_classes'])
                ontology_structure_lines.append(f"    - {conn['property']} → {targets}")
        
        ontology_structure_lines.append("")
    
    ontology_structure = "\n".join(ontology_structure_lines)
    
    # Create explicit list of all classes for verification
    entity_classes_list = "\n".join([f"- {cls.split('/')[-1]}" for cls in sorted(concise_structure['classes'])])
    
    # Fill template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        ontology_structure=ontology_structure,
        entity_classes_list=entity_classes_list
    )
    
    return prompt


async def generate_base_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate the BASE script (checks, relationships, helpers) using direct LLM calls.
    
    Returns:
        Path to generated base script
    """
    print(f"\n📝 [1/2] Generating BASE script (checks, relationships, helpers)...")
    print(f"   Model: {model_name}")
    
    # Build prompt
    prompt = build_base_script_prompt(ontology_path, ontology_name)
    
    # Create OpenAI client
    client = create_openai_client()
    
    # Call LLM
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}...")
            
            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=16000
            )
            
            # Extract code
            content = response.choices[0].message.content
            code = extract_code_from_response(content)
            
            # Write to file
            output_path = Path(output_dir) / f"{ontology_name}_creation_base.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✓ Generated: {output_path.name}")
            return str(output_path)
            
        except Exception as e:
            last_exception = e
            print(f"   ✗ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
    
    raise Exception(f"Failed to generate base script after {max_retries} attempts: {last_exception}")


async def generate_entity_group_script_direct(
    ontology_path: str,
    ontology_name: str,
    group_info: dict,
    output_dir: str,
    base_script_path: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate a single entity group script (subset of all entities).
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        group_info: Dictionary with 'name', 'entities', 'script_name', 'description'
        output_dir: Directory to write the generated script
        base_script_path: Path to the base script (to extract available functions)
        model_name: LLM model to use
        max_retries: Number of retry attempts
        
    Returns:
        Path to generated script
    """
    print(f"\n📝 Generating entity group script: {group_info['name']}")
    print(f"   Entities: {', '.join(group_info['entities'])}")
    print(f"   Output: {group_info['script_name']}")
    
    # Extract functions from base script to know what's available
    base_functions = extract_functions_from_underlying(base_script_path)
    available_helpers = [f['name'] for f in base_functions if f['name'].startswith('_find_or_create_')]
    available_check_functions = [f['name'] for f in base_functions if f['name'].startswith('check_existing_')]
    available_add_functions = [f['name'] for f in base_functions if f['name'].startswith('add_')]
    
    print(f"   Available helpers: {len(available_helpers)} _find_or_create_* functions")
    print(f"   Available checks: {len(available_check_functions)} check_existing_* functions")
    
    # Build prompt for this specific group
    prompt = build_entity_group_prompt(
        ontology_path, 
        ontology_name, 
        group_info,
        available_helpers=available_helpers,
        available_check_functions=available_check_functions,
        available_add_functions=available_add_functions
    )
    
    # Create OpenAI client
    client = create_openai_client()
    
    # Call LLM with retries
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            print(f"   🔄 Attempt {attempt}/{max_retries}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in ontology-based code generation."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=16000
            )
            
            code = response.choices[0].message.content.strip()
            
            # Clean code fences if present
            if code.startswith("```"):
                lines = code.split("\n")
                code = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            
            # Write to file
            output_path = Path(output_dir) / group_info['script_name']
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✓ Generated: {output_path.name} ({len(group_info['entities'])} entities)")
            return str(output_path)
            
        except Exception as e:
            last_exception = e
            print(f"   ✗ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
    
    raise Exception(f"Failed to generate {group_info['name']} script after {max_retries} attempts: {last_exception}")


async def generate_entities_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    base_script_path: str,
    checks_script_path: str,
    relationships_script_path: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> list:
    """
    Generate 2 entity creation scripts (create_* functions split in half).
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        output_dir: Directory to write scripts
        base_script_path: Path to base utilities script
        checks_script_path: Path to checks script
        relationships_script_path: Path to relationships script
        model_name: LLM model to use
        max_retries: Number of retry attempts
    
    Returns:
        List of paths to 2 generated entity scripts
    """
    print(f"   Generating entity creation scripts (2 parts)...")
    print(f"   Model: {model_name}")
    
    # Extract concise ontology to get all classes
    concise_structure = extract_concise_ontology_structure(ontology_path)
    all_classes = sorted(concise_structure['classes'])
    
    # Split classes into 2 equal groups
    mid_point = len(all_classes) // 2
    group_1_classes = all_classes[:mid_point]
    group_2_classes = all_classes[mid_point:]
    
    print(f"   Part 1: {len(group_1_classes)} classes")
    print(f"   Part 2: {len(group_2_classes)} classes")
    
    # Generate both scripts
    generated_scripts = []
    
    for part_num, classes in [(1, group_1_classes), (2, group_2_classes)]:
        print(f"\n   [{part_num}/2] Generating entities part {part_num}...")
        script_path = await generate_entity_part_script(
            ontology_path=ontology_path,
            ontology_name=ontology_name,
            part_number=part_num,
            classes_to_generate=classes,
            output_dir=output_dir,
            base_script_path=base_script_path,
            checks_script_path=checks_script_path,
            relationships_script_path=relationships_script_path,
            model_name=model_name,
            max_retries=max_retries
        )
        generated_scripts.append(script_path)
    
    print(f"\n   ✅ Generated 2 entity creation scripts")
    return generated_scripts


async def generate_entities_script_direct_legacy(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    LEGACY: Generate the ENTITIES script (all create_* functions) using direct LLM calls.
    
    DEPRECATED: Use generate_entities_script_direct for multi-group generation.
    
    Returns:
        Path to generated entities script
    """
    print(f"\n📝 [2/2] Generating ENTITIES script (all create_* functions)...")
    print(f"   Model: {model_name}")
    
    # Build prompt
    prompt = build_entities_script_prompt(ontology_path, ontology_name)
    
    # Create OpenAI client
    client = create_openai_client()
    
    # Call LLM
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}...")
            
            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development. Generate ALL create functions - no shortcuts, no placeholders."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=16000
            )
            
            # Extract code
            content = response.choices[0].message.content
            code = extract_code_from_response(content)
            
            # Write to file
            output_path = Path(output_dir) / f"{ontology_name}_creation_entities.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✓ Generated: {output_path.name}")
            return str(output_path)
            
        except Exception as e:
            last_exception = e
            print(f"   ✗ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
    
    raise Exception(f"Failed to generate entities script after {max_retries} attempts: {last_exception}")


def create_entity_breakdown_plan(ontology_path: str, ontology_name: str, output_dir: str) -> dict:
    """
    Analyze ontology and create a structured plan for breaking down entity generation.
    
    Groups entities by semantic category to keep each generated script manageable (~300-500 lines).
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Name of ontology
        output_dir: Output directory for plan file
        
    Returns:
        Dictionary containing the breakdown plan
    """
    import json
    from pathlib import Path
    
    # Parse ontology to get class list
    concise_structure = extract_concise_ontology_structure(ontology_path)
    classes = concise_structure["classes"]
    class_structures = concise_structure["class_structures"]
    
    # Categorize entities by type
    synthesis_steps = []
    chemical_entities = []
    equipment_entities = []
    support_entities = []
    
    for cls_full in classes:
        # Extract class name (already just the name, not full URI in our structure)
        cls_name = cls_full.split("/")[-1] if "/" in cls_full else cls_full
        structure = class_structures.get(cls_name, {})
        parents = structure.get("parent_classes", [])
        
        # Categorize based on parent class and name patterns
        if any("SynthesisStep" in parent for parent in parents):
            synthesis_steps.append(cls_name)
        elif "Chemical" in cls_name or "Synthesis" in cls_name:
            chemical_entities.append(cls_name)
        elif any(keyword in cls_name for keyword in ["Equipment", "Vessel", "Device"]):
            equipment_entities.append(cls_name)
        else:
            support_entities.append(cls_name)
    
    # Further split synthesis steps if too many
    max_per_group = 6
    steps_groups = []
    for i in range(0, len(synthesis_steps), max_per_group):
        steps_groups.append(synthesis_steps[i:i+max_per_group])
    
    # Build plan
    plan = {
        "ontology": ontology_name,
        "total_entities": len(classes),
        "groups": []
    }
    
    # Add synthesis step groups
    for idx, group in enumerate(steps_groups, 1):
        plan["groups"].append({
            "name": f"synthesis_steps_{idx}",
            "description": f"Synthesis step entities (group {idx})",
            "entities": group,
            "script_name": f"{ontology_name}_creation_steps_{idx}.py"
        })
    
    # Add other groups
    if chemical_entities:
        plan["groups"].append({
            "name": "chemical_entities",
            "description": "Chemical inputs, outputs, and synthesis",
            "entities": chemical_entities,
            "script_name": f"{ontology_name}_creation_chemical.py"
        })
    
    if equipment_entities:
        plan["groups"].append({
            "name": "equipment",
            "description": "Equipment, vessels, and devices",
            "entities": equipment_entities,
            "script_name": f"{ontology_name}_creation_equipment.py"
        })
    
    if support_entities:
        plan["groups"].append({
            "name": "support",
            "description": "Supporting entities",
            "entities": support_entities,
            "script_name": f"{ontology_name}_creation_support.py"
        })
    
    # Save plan to JSON
    plan_path = Path(output_dir) / f"{ontology_name}_entity_breakdown.json"
    with open(plan_path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, indent=2)
    
    print(f"   📋 Created entity breakdown plan: {plan_path.name}")
    print(f"      Total entities: {plan['total_entities']}")
    print(f"      Number of groups: {len(plan['groups'])}")
    for group in plan["groups"]:
        print(f"      - {group['name']}: {len(group['entities'])} entities → {group['script_name']}")
    
    return plan


async def generate_underlying_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate an underlying MCP script using direct LLM calls with domain-agnostic meta-prompts.
    
    NOTE: This function is now used for generating the BASE script only.
    Entity creation functions are split across multiple scripts via generate_entity_group_script_direct().
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology (e.g., 'ontosynthesis')
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts for API calls
    
    Returns:
        Path to generated base script
    """
    print(f"\n📝 Generating underlying script via direct LLM call (domain-agnostic mode)...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    
    # Save concise ontology structure as markdown
    output_base_dir = Path(output_dir).parent.parent  # Go up to ai_generated_contents_candidate
    concise_md_path = save_concise_structure(ontology_path, ontology_name, output_base_dir)
    print(f"   📄 Saved concise ontology structure: {concise_md_path.name}")
    
    # Build prompt using domain-agnostic meta-prompt + TTL parsing
    prompt = build_underlying_script_prompt(ontology_path, ontology_name)
    
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
                        "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development. Generate code based on T-Box ontology structure."
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
    checks_script_path: str,
    relationships_script_path: str,
    base_script_path: str,
    entity_script_paths: list,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate a FastMCP main script using direct LLM calls with domain-agnostic meta-prompts.
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        checks_script_path: Path to checks script
        relationships_script_path: Path to relationships script
        base_script_path: Path to base script
        entity_script_paths: List of paths to entity group scripts
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts
    
    Returns:
        Path to generated script
    """
    print(f"\n📝 [FINAL] Generating main.py via direct LLM call...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    print(f"   Architecture: MULTI-SCRIPT")
    print(f"      - Checks: {Path(checks_script_path).name}")
    print(f"      - Relationships: {Path(relationships_script_path).name}")
    print(f"      - Base: {Path(base_script_path).name}")
    print(f"      - Entity scripts: {len(entity_script_paths)}")
    for idx, path in enumerate(entity_script_paths, 1):
        print(f"         {idx}. {Path(path).name}")
    
    # Combine all foundational scripts for prompt
    all_script_paths = [checks_script_path, relationships_script_path, base_script_path] + entity_script_paths
    
    # Build prompt using domain-agnostic meta-prompt + TTL parsing
    prompt = build_main_script_prompt(
        ontology_path, 
        ontology_name, 
        underlying_script_path=None,  # Not used in new architecture
        base_script_path=base_script_path,
        entity_script_paths=all_script_paths  # Pass all scripts
    )
    
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
                        "content": "You are an expert in FastMCP server development. Generate complete, production-ready FastMCP wrappers based on extracted function signatures."
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





async def generate_checks_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """Generate check_existing_* functions with syntax validation."""
    print(f"   Generating check_existing functions...")
    
    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    with open(concise_md_path, 'r', encoding='utf-8') as f:
        concise_content = f.read()
    
    prompt = f"""Generate {ontology_name}_creation_checks.py

CRITICAL: Code MUST compile without syntax errors.

Use EXACTLY these imports:
```python
from rdflib import Graph, Namespace, URIRef, RDF, RDFS
from ..universal_utils import locked_graph, _list_instances_with_label
from .{ontology_name}_creation_base import _guard_check
```

REQUIRED FUNCTIONS (from concise ontology):
{concise_content[90:600]}

Generate WORKING Python code with ALL check_existing functions listed above."""
    
    client = create_openai_client()
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}... (Error: {last_error})")
            
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "Generate ONLY valid, compilable Python code. No explanations."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=8000
            )
            
            code = extract_code_from_response(response.choices[0].message.content or "")
            if not code:
                raise ValueError("Empty response")
            
            # VALIDATE SYNTAX
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_checks.py")
            if not is_valid:
                last_error = f"Syntax: {syntax_error}"
                print(f"   ❌ Syntax error: {syntax_error}")
                if attempt < max_retries:
                    prompt += f"\n\n⚠️ FIX THIS SYNTAX ERROR:\n{syntax_error}"
                    continue
                raise ValueError(f"Syntax errors after {max_retries} attempts: {syntax_error}")
            
            output_path = Path(output_dir) / f"{ontology_name}_creation_checks.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✅ Generated: {output_path.name} - Syntax OK")
            return str(output_path)
            
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                raise


async def generate_relationships_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """Generate add_xxx_to_yyy functions with syntax validation."""
    print(f"   Generating relationship functions...")
    
    concise_structure = extract_concise_ontology_structure(ontology_path)
    object_properties = set()
    for struct in concise_structure.get('class_structures', {}).values():
        for conn in struct.get('connects_to', []):
            object_properties.add(conn['property'])
    
    props_list = "\n".join([f"- {prop}" for prop in sorted(object_properties)])
    
    prompt = f"""Generate {ontology_name}_creation_relationships.py

CRITICAL: Code MUST compile without syntax errors.

Use EXACTLY these imports:
```python
from rdflib import URIRef, RDF
from ..universal_utils import locked_graph, _require_existing, _export_snapshot_silent, _format_success
from .{ontology_name}_creation_base import _guard_noncheck, ONTOSYN, ONTOLAB, ONTOMOPS, ONTOCAPE_MAT
```

OBJECT PROPERTIES (generate add_* function for each):
{props_list}

FUNCTION TEMPLATE:
```python
@_guard_noncheck
def add_{{PropertyName}}_to_{{DomainClass}}(subject_iri: str, object_iri: str) -> str:
    with locked_graph() as g:
        subject, msg = _require_existing(g, subject_iri, ONTOSYN.{{DomainClass}}, "subject_iri")
        if subject is None: raise ValueError(msg or "Subject not found")
        obj, msg2 = _require_existing(g, object_iri, ONTOSYN.{{ObjectClass}}, "object_iri")
        if obj is None: raise ValueError(msg2 or "Object not found")
        if (subject, ONTOSYN.{{propertyName}}, obj) not in g:
            g.add((subject, ONTOSYN.{{propertyName}}, obj))
        msg_out = f"Linked {{ObjectClass}} to {{DomainClass}}."
    _export_snapshot_silent()
    return _format_success(subject, msg_out)
```

Generate WORKING Python code with ALL add_* functions."""
    
    client = create_openai_client()
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}... (Error: {last_error})")
            
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "Generate ONLY valid, compilable Python code. No explanations."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=16000
            )
            
            code = extract_code_from_response(response.choices[0].message.content or "")
            if not code:
                raise ValueError("Empty response")
            
            # VALIDATE SYNTAX
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_relationships.py")
            if not is_valid:
                last_error = f"Syntax: {syntax_error}"
                print(f"   ❌ Syntax error: {syntax_error}")
                if attempt < max_retries:
                    prompt += f"\n\n⚠️ FIX THIS SYNTAX ERROR:\n{syntax_error}"
                    continue
                raise ValueError(f"Syntax errors after {max_retries} attempts: {syntax_error}")
            
            output_path = Path(output_dir) / f"{ontology_name}_creation_relationships.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✅ Generated: {output_path.name} - Syntax OK")
            return str(output_path)
            
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                raise



async def generate_entity_part_script(
    ontology_path: str,
    ontology_name: str,
    part_number: int,
    classes_to_generate: list,
    output_dir: str,
    base_script_path: str,
    checks_script_path: str,
    relationships_script_path: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """Generate one part of the entity creation scripts with syntax validation."""
    from pathlib import Path
    
    # Load concise ontology for signatures
    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    with open(concise_md_path, 'r', encoding='utf-8') as f:
        concise_content = f.read()
    
    class_names = [cls.split('/')[-1] for cls in classes_to_generate]
    classes_list = "\n".join([f"- {name}" for name in class_names])
    
    # Build strong prompt with explicit requirements
    prompt = f"""Generate {ontology_name}_creation_entities_{part_number}.py

CRITICAL REQUIREMENTS:
1. The code MUST compile without syntax errors
2. Import ONLY existing functions - do NOT make up function names
3. Return JSON STRING (use json.dumps()), NOT dict objects
4. Use proper relative imports

CLASSES TO GENERATE (Part {part_number}):
{classes_list}

REQUIRED IMPORTS (use EXACTLY these):
```python
import json
from rdflib import Graph, URIRef, RDF, RDFS, Literal as RDFLiteral
from ..universal_utils import (
    locked_graph, _mint_hash_iri, _sanitize_label,
    _find_by_type_and_label, _set_single_label, _export_snapshot_silent
)
from .{ontology_name}_creation_base import (
    _guard_noncheck, ONTOSYN, ONTOLAB, ONTOMOPS, ONTOCAPE_MAT
)
```

FUNCTION TEMPLATE (COPY THIS):
```python
@_guard_noncheck
def create_{{{{ClassName}}}}(label: str) -> str:
    try:
        with locked_graph() as g:
            sanitized = _sanitize_label(label)
            existing = _find_by_type_and_label(g, ONTOSYN.{{{{ClassName}}}}, sanitized)
            if existing is not None:
                return json.dumps({{"status": "ok", "iri": str(existing), "created": False, "code": "ALREADY_EXISTS"}})
            
            iri = _mint_hash_iri("{{{{ClassName}}}}")
            g.add((iri, RDF.type, ONTOSYN.{{{{ClassName}}}}))
            _set_single_label(g, iri, sanitized)
            
        _export_snapshot_silent()
        return json.dumps({{"status": "ok", "iri": str(iri), "created": True}})
    except Exception as e:
        return json.dumps({{"status": "error", "iri": None, "created": False, "message": str(e)}})
```

Generate WORKING, COMPILABLE Python code with ALL {len(class_names)} create functions."""

    client = create_openai_client()
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}... (Error: {last_error})")
            
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer. Generate ONLY valid, compilable Python code with correct imports. Return ONLY the code, no explanations."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=16000
            )
            
            code = extract_code_from_response(response.choices[0].message.content or "")
            if not code:
                raise ValueError("Empty response from LLM")
            
            # VALIDATE SYNTAX
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_entities_{part_number}.py")
            if not is_valid:
                last_error = f"Syntax: {syntax_error}"
                print(f"   ❌ Syntax validation failed: {syntax_error}")
                if attempt < max_retries:
                    print(f"   🔄 Retrying with syntax error feedback...")
                    prompt += f"\n\n⚠️ PREVIOUS ATTEMPT HAD SYNTAX ERROR:\n{syntax_error}\n\nFix this and generate valid Python code."
                    continue
                raise ValueError(f"Generated code has syntax errors after {max_retries} attempts: {syntax_error}")
            
            # Write validated code
            output_path = Path(output_dir) / f"{ontology_name}_creation_entities_{part_number}.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✅ Generated: {output_path.name} ({len(code)} chars) - Syntax OK")
            return str(output_path)
            
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                raise Exception(f"Failed after {max_retries} attempts. Last error: {last_error}")

