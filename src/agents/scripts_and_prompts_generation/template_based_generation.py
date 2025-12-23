#!/usr/bin/env python3
"""
Template-based script generation - NO LLM required!

Pure script-based generation using templates and the concise ontology structure.
Faster, more reliable, and free compared to LLM-based generation.
"""

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any


def parse_function_signature(sig_text: str) -> Dict[str, Any]:
    """
    Parse a function signature from the concise markdown.
    
    Example input:
        ```python
        def create_Add(
            label: str,  # Required
            hasOrder: Optional[int] = None,
            vessel_label: Optional[str] = None,  # Auto-created Vessel
        ) -> str:
        ```
    
    Returns:
        {
            'name': 'create_Add',
            'class_name': 'Add',
            'parameters': [
                {'name': 'label', 'type': 'str', 'optional': False, 'comment': 'Required'},
                {'name': 'hasOrder', 'type': 'Optional[int]', 'optional': True, 'comment': None},
                {'name': 'vessel_label', 'type': 'Optional[str]', 'optional': True, 'comment': 'Auto-created Vessel'},
            ]
        }
    """
    # Extract function name
    func_match = re.search(r'def (create_\w+)\(', sig_text)
    if not func_match:
        return None
    
    func_name = func_match.group(1)
    class_name = func_name.replace('create_', '')
    
    # Extract parameters
    parameters = []
    param_pattern = r'(\w+):\s*([\w\[\]]+)(?:\s*=\s*None)?(?:\s*,)?\s*(?:#\s*(.+))?'
    
    for match in re.finditer(param_pattern, sig_text):
        param_name = match.group(1)
        param_type = match.group(2)
        comment = match.group(3).strip() if match.group(3) else None
        
        # Skip the function name line
        if param_name == 'def':
            continue
        
        is_optional = 'None' in sig_text[match.start():match.end() + 20]
        
        parameters.append({
            'name': param_name,
            'type': param_type,
            'optional': is_optional,
            'comment': comment,
            'is_auxiliary': comment and 'Auto-created' in comment if comment else False,
            'auxiliary_type': comment.replace('Auto-created', '').strip() if (comment and 'Auto-created' in comment) else None
        })
    
    return {
        'name': func_name,
        'class_name': class_name,
        'parameters': parameters
    }


def parse_concise_signatures(concise_md_path: Path) -> List[Dict[str, Any]]:
    """
    Parse all function signatures from the concise markdown file.
    
    Returns:
        List of parsed function signature dictionaries
    """
    with open(concise_md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find all function signature blocks
    signatures = []
    pattern = r'### `(create_\w+)` Parameters:\s*```python\s*(.*?)\s*```'
    
    for match in re.finditer(pattern, content, re.DOTALL):
        func_name = match.group(1)
        sig_block = match.group(2)
        
        parsed = parse_function_signature(sig_block)
        if parsed:
            signatures.append(parsed)
    
    return signatures


def generate_create_function(func_info: Dict[str, Any], ontology_name: str) -> str:
    """
    Generate a complete create_* function from parsed signature info.
    
    Args:
        func_info: Parsed function signature info
        ontology_name: Name of ontology (e.g., 'ontosynthesis')
    
    Returns:
        Complete Python function code as string
    """
    class_name = func_info['class_name']
    params = func_info['parameters']
    
    # Build function signature
    param_lines = []
    for p in params:
        if p['optional']:
            param_lines.append(f"    {p['name']}: {p['type']} = None,")
        else:
            param_lines.append(f"    {p['name']}: {p['type']},")
    
    # Remove trailing comma from last param
    if param_lines:
        param_lines[-1] = param_lines[-1].rstrip(',')
    
    params_str = '\n'.join(param_lines)
    
    # Determine namespace
    namespace = 'ONTOSYN'  # Default
    if 'MetalOrganicPolyhedron' in class_name or 'MolecularCage' in class_name:
        namespace = 'ONTOMOPS'
    
    # Build function body
    lines = [
        f"@_guard_noncheck",
        f"def create_{class_name}(",
        params_str,
        f") -> str:",
        f'    """',
        f'    Create {class_name} entity.',
        f'    ',
        f'    Returns JSON envelope with status, IRI, and created flag.',
        f'    """',
        f"    try:",
        f"        with locked_graph() as g:",
        f"            sanitized = _sanitize_label(label)",
        f"            existing = _find_by_type_and_label(g, {namespace}.{class_name}, sanitized)",
        f"            if existing is not None:",
        f'                return _format_error("{class_name} with label \'" + sanitized + "\' already exists", code="ALREADY_EXISTS")',
        f"            ",
        f'            iri = _mint_hash_iri("{class_name}")',
        f"            g.add((iri, RDF.type, {namespace}.{class_name}))",
        f"            _set_single_label(g, iri, sanitized)",
        f"            ",
    ]
    
    # Add datatype properties
    datatype_params = [p for p in params if not p['is_auxiliary'] and p['name'] != 'label']
    if datatype_params:
        lines.append("            # Datatype properties")
        for p in datatype_params:
            param_name = p['name']
            
            # Determine RDF literal type
            if 'int' in p['type'].lower():
                cast = 'int'
            elif 'bool' in p['type'].lower():
                cast = 'bool'
            else:
                cast = 'str'
            
            lines.append(f"            if {param_name} is not None:")
            lines.append(f"                g.add((iri, {namespace}.{param_name}, RDFLiteral({cast}({param_name}))))")
        lines.append("            ")
    
    # Add auxiliary entity auto-creation
    auxiliary_params = [p for p in params if p['is_auxiliary']]
    if auxiliary_params:
        lines.append("            # Auto-create auxiliary entities")
        for p in auxiliary_params:
            param_name = p['name']
            aux_type = p['auxiliary_type']
            
            if 'Vessel' in aux_type and 'Type' not in aux_type and 'Environment' not in aux_type:
                # Special case: Vessel takes vessel_type as parameter
                lines.append(f"            if {param_name}:")
                lines.append(f"                vessel_iri = _find_or_create_Vessel(g, {param_name}, vessel_type)")
                lines.append(f"                g.add((iri, {namespace}.hasVessel, vessel_iri))")
            elif 'VesselEnvironment' in aux_type:
                lines.append(f"            if {param_name}:")
                lines.append(f"                venv_iri = _find_or_create_VesselEnvironment(g, {param_name})")
                lines.append(f"                g.add((iri, {namespace}.hasVesselEnvironment, venv_iri))")
            elif 'Supplier' in aux_type:
                lines.append(f"            if {param_name}:")
                lines.append(f"                supplier_iri = _find_or_create_Supplier(g, {param_name})")
                lines.append(f"                g.add((iri, {namespace}.isSuppliedBy, supplier_iri))")
            elif 'Material' in aux_type:
                lines.append(f"            if {param_name}:")
                lines.append(f"                material_iri = _mint_hash_iri('Material')")
                lines.append(f"                g.add((material_iri, RDF.type, ONTOCAPE_MAT.Material))")
                lines.append(f"                _set_single_label(g, material_iri, _sanitize_label({param_name}))")
                lines.append(f"                g.add((iri, {namespace}.referencesMaterial, material_iri))")
            elif 'MetalOrganicPolyhedron' in aux_type:
                lines.append(f"            if represented_by_label:")
                lines.append(f"                mop_iri = _find_or_create_MetalOrganicPolyhedron(g, represented_by_label, represented_by_CCDC)")
                lines.append(f"                g.add((iri, {namespace}.isRepresentedBy, mop_iri))")
            elif 'DocumentContext' in aux_type:
                lines.append(f"            if {param_name}:")
                lines.append(f"                dc_iri = _mint_hash_iri('DocumentContext')")
                lines.append(f"                g.add((dc_iri, RDF.type, {namespace}.DocumentContext))")
                lines.append(f"                _set_single_label(g, dc_iri, _sanitize_label({param_name}))")
                lines.append(f"                g.add((iri, {namespace}.hasDocumentContext, dc_iri))")
            elif 'HeatChillDevice' in aux_type:
                lines.append(f"            if {param_name}:")
                lines.append(f"                hcd_iri = _mint_hash_iri('HeatChillDevice')")
                lines.append(f"                g.add((hcd_iri, RDF.type, {namespace}.HeatChillDevice))")
                lines.append(f"                _set_single_label(g, hcd_iri, _sanitize_label({param_name}))")
                lines.append(f"                g.add((iri, {namespace}.hasHeatChillDevice, hcd_iri))")
        lines.append("            ")
    
    # Close function
    lines.extend([
        "        _export_snapshot_silent()",
        '        return _format_success_json(iri, f"Created {class_name}", created=True)',
        "    except Exception as e:",
        '        return _format_error(str(e), code="INTERNAL_ERROR")',
        ""
    ])
    
    return '\n'.join(lines)


def generate_entity_script_from_template(
    concise_md_path: Path,
    ontology_name: str,
    output_path: Path,
    class_subset: Optional[List[str]] = None
) -> Path:
    """
    Generate entity creation script using pure templates (NO LLM).
    
    Args:
        concise_md_path: Path to concise ontology markdown
        ontology_name: Name of ontology
        output_path: Where to write the generated script
        class_subset: If provided, only generate these classes
    
    Returns:
        Path to generated script
    """
    print(f"   🔧 Generating entity script (template-based, no LLM)...")
    
    # Parse all signatures
    signatures = parse_concise_signatures(concise_md_path)
    
    if class_subset:
        signatures = [s for s in signatures if s['class_name'] in class_subset]
    
    print(f"   📝 Generating {len(signatures)} create_* functions...")
    
    # Build script
    lines = [
        "#!/usr/bin/env python3",
        '"""',
        f'{ontology_name}_creation_entities.py',
        '',
        f'Entity creation functions for {ontology_name} ontology.',
        'Generated by template-based script generation (no LLM).',
        '"""',
        '',
        'import json',
        'from typing import Optional',
        'from rdflib import Graph, URIRef, RDF, RDFS, Literal as RDFLiteral, Namespace',
        '',
        '# Import universal utilities',
        'from ..universal_utils import (',
        '    locked_graph, _mint_hash_iri, _sanitize_label,',
        '    _find_by_type_and_label, _set_single_label, _export_snapshot_silent',
        ')',
        '',
        '# Import from base script',
        f'from .{ontology_name}_creation_base import (',
        '    _guard_noncheck, _format_error, _format_success_json,',
        '    _find_or_create_Vessel, _find_or_create_VesselEnvironment,',
        '    _find_or_create_Supplier, _find_or_create_MetalOrganicPolyhedron',
        ')',
        '',
        '# Define all namespaces locally (to avoid import issues)',
        'ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")',
        'ONTOLAB = Namespace("https://www.theworldavatar.com/kg/OntoLab/")',
        'ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")',
        '',
        '# ============================================================================',
        '# ENTITY CREATION FUNCTIONS',
        '# ============================================================================',
        ''
    ]
    
    # Generate each function
    for sig in signatures:
        function_code = generate_create_function(sig, ontology_name)
        lines.append(function_code)
    
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"   ✅ Generated: {output_path.name} ({len(signatures)} functions)")
    return output_path


def generate_checks_script_from_template(
    concise_structure: Dict,
    ontology_name: str,
    output_path: Path
) -> Path:
    """
    Generate check_existing_* functions script using templates (NO LLM).
    
    Only generates parent-class based functions (no redundant subclass functions).
    """
    print(f"   🔧 Generating check_existing functions (template-based)...")
    
    # Analyze class hierarchy
    class_structures = concise_structure['class_structures']
    
    # Find parent-child relationships
    children_by_parent = {}
    classes_with_parents = set()
    
    for cls_name, structure in class_structures.items():
        simple_name = cls_name.split('/')[-1] if '/' in cls_name else cls_name
        parents = structure.get('parent_classes', [])
        for parent_full in parents:
            parent = parent_full.split('/')[-1] if '/' in parent_full else parent_full
            # Check if parent is in our ontology
            if any(parent in k for k in class_structures.keys()):
                if parent not in children_by_parent:
                    children_by_parent[parent] = []
                children_by_parent[parent].append(simple_name)
                classes_with_parents.add(simple_name)
    
    # Standalone classes
    all_classes = [c.split('/')[-1] if '/' in c else c for c in concise_structure['classes']]
    standalone_classes = [c for c in all_classes if c not in classes_with_parents and c not in children_by_parent]
    
    # Generate script
    lines = [
        "#!/usr/bin/env python3",
        '"""',
        f'{ontology_name}_creation_checks.py',
        '',
        f'Check existing entity functions for {ontology_name} ontology.',
        'Generated by template-based script generation (no LLM).',
        '"""',
        '',
        'from rdflib import Graph, Namespace, RDF',
        '',
        '# Import universal utilities',
        'from ..universal_utils import locked_graph, _list_instances_with_label',
        '',
        '# Import from base script',
        f'from .{ontology_name}_creation_base import _guard_check',
        '',
        '# Define all namespaces locally (to avoid import issues)',
        'ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")',
        'ONTOLAB = Namespace("https://www.theworldavatar.com/kg/OntoLab/")',
        'ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")',
        '',
        '# ============================================================================',
        '# CHECK EXISTING FUNCTIONS (Parent-Class Based)',
        '# ============================================================================',
        ''
    ]
    
    # Generate parent-class functions
    for parent in sorted(children_by_parent.keys()):
        children = children_by_parent[parent]
        children_str = ', '.join(sorted(children))
        
        # Determine namespace
        namespace = 'ONTOMOPS' if 'Polyhedron' in parent or 'Cage' in parent else 'ONTOSYN'
        
        lines.extend([
            '@_guard_check',
            f'def check_existing_{parent}() -> str:',
            f'    """',
            f'    List existing {parent} instances (includes subclasses: {children_str}).',
            f'    Returns tab-separated list: IRI\\tlabel',
            f'    """',
            f'    with locked_graph() as g:',
            f'        return "\\n".join(_list_instances_with_label(g, {namespace}.{parent}))',
            ''
        ])
    
    # Generate standalone class functions
    for cls_name in sorted(standalone_classes):
        namespace = 'ONTOMOPS' if 'Polyhedron' in cls_name or 'Cage' in cls_name else 'ONTOSYN'
        
        lines.extend([
            '@_guard_check',
            f'def check_existing_{cls_name}() -> str:',
            f'    """',
            f'    List existing {cls_name} instances.',
            f'    Returns tab-separated list: IRI\\tlabel',
            f'    """',
            f'    with locked_graph() as g:',
            f'        return "\\n".join(_list_instances_with_label(g, {namespace}.{cls_name}))',
            ''
        ])
    
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    num_funcs = len(children_by_parent) + len(standalone_classes)
    print(f"   ✅ Generated: {output_path.name} ({num_funcs} functions)")
    return output_path


def generate_relationships_script_from_template(
    concise_structure: Dict,
    ontology_name: str,
    output_path: Path
) -> Path:
    """
    Generate add_*_to_* relationship functions using templates (NO LLM).
    """
    print(f"   🔧 Generating relationship functions (template-based)...")
    
    # Extract all object properties
    object_properties = []
    class_structures = concise_structure['class_structures']
    
    for cls_name, structure in class_structures.items():
        for conn in structure.get('connects_to', []):
            prop_name = conn['property'].split('/')[-1] if '/' in conn['property'] else conn['property']
            domain_class = cls_name.split('/')[-1] if '/' in cls_name else cls_name
            
            for target in conn['target_classes']:
                range_class = target.split('/')[-1] if '/' in target else target
                
                object_properties.append({
                    'property': prop_name,
                    'domain': domain_class,
                    'range': range_class
                })
    
    # Remove duplicates
    unique_props = []
    seen = set()
    for prop in object_properties:
        key = (prop['property'], prop['domain'], prop['range'])
        if key not in seen:
            seen.add(key)
            unique_props.append(prop)
    
    # Generate script
    lines = [
        "#!/usr/bin/env python3",
        '"""',
        f'{ontology_name}_creation_relationships.py',
        '',
        f'Relationship-building functions for {ontology_name} ontology.',
        'Generated by template-based script generation (no LLM).',
        '"""',
        '',
        'from rdflib import URIRef, RDF, Namespace',
        '',
        '# Import universal utilities',
        'from ..universal_utils import (',
        '    locked_graph, _require_existing, _get_label,',
        '    _export_snapshot_silent, _format_success',
        ')',
        '',
        '# Import from base script',
        f'from .{ontology_name}_creation_base import _guard_noncheck',
        '',
        '# Define all namespaces locally (to avoid import issues)',
        'ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")',
        'ONTOLAB = Namespace("https://www.theworldavatar.com/kg/OntoLab/")',
        'ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")',
        'ONTOCAPE_MAT = Namespace("http://www.theworldavatar.com/ontology/ontocape/material/material.owl#")',
        '',
        '# ============================================================================',
        '# RELATIONSHIP FUNCTIONS (add_*_to_*)',
        '# ============================================================================',
        ''
    ]
    
    # Generate each add_* function
    for prop in sorted(unique_props, key=lambda x: (x['property'], x['domain'])):
        prop_name = prop['property']
        domain = prop['domain']
        range_cls = prop['range']
        
        # Sanitize class names (remove special characters)
        domain_safe = domain.replace('#', '_').replace('.', '_').replace('/', '_')
        range_safe = range_cls.replace('#', '_').replace('.', '_').replace('/', '_')
        
        # Handle duplicate parameter names (subject == object)
        subject_param = f'{domain_safe}_iri'
        if domain == range_cls:
            object_param = f'{range_safe}_target_iri'
        else:
            object_param = f'{range_safe}_iri'
        
        # Determine namespaces
        domain_ns = 'ONTOMOPS' if 'Polyhedron' in domain or 'Cage' in domain else 'ONTOSYN'
        range_ns = 'ONTOMOPS' if 'Polyhedron' in range_cls or 'Cage' in range_cls else 'ONTOSYN'
        
        # Handle external ranges (from other ontologies)
        if range_cls in ['Temperature', 'Duration', 'Pressure', 'Volume', 'TemperatureRate', 'AmountOfSubstanceFraction', 'Document']:
            range_ns = 'ONTOSYN'  # Use ONTOSYN namespace as fallback
        elif 'Material' in range_cls:
            range_ns = 'ONTOCAPE_MAT'
        elif 'LabEquipment' in range_cls:
            range_ns = 'ONTOLAB'
        
        # Sanitize namespace class references
        domain_ns_ref = f'{domain_ns}.{domain_safe}'
        range_ns_ref = f'{range_ns}.{range_safe}'
        
        lines.extend([
            '@_guard_noncheck',
            f'def add_{prop_name}_to_{domain_safe}(',
            f'    {subject_param}: str,',
            f'    {object_param}: str',
            f') -> str:',
            f'    """',
            f'    Attach {range_cls} to {domain} via {prop_name}.',
            f'    """',
            f'    with locked_graph() as g:',
            f'        subject, msg = _require_existing(g, {subject_param}, {domain_ns_ref}, "{subject_param}")',
            f'        if subject is None:',
            f'            raise ValueError(msg or "Subject not found")',
            f'        obj, msg2 = _require_existing(g, {object_param}, {range_ns_ref}, "{object_param}")',
            f'        if obj is None:',
            f'            raise ValueError(msg2 or "Object not found")',
            f'        ',
            f'        # Idempotency check',
            f'        if (subject, ONTOSYN.{prop_name}, obj) not in g:',
            f'            g.add((subject, ONTOSYN.{prop_name}, obj))',
            f'        ',
            f'        msg_out = f"Attached {{_get_label(g, obj)}} to {{_get_label(g, subject)}}."',
            f'    _export_snapshot_silent()',
            f'    return _format_success(subject, msg_out)',
            ''
        ])
    
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"   ✅ Generated: {output_path.name} ({len(unique_props)} functions)")
    return output_path

