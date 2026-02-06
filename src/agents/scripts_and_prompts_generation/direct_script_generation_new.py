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
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple
from openai import OpenAI
from dotenv import load_dotenv
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

def _token_limit_kwargs(model_name: str, max_tokens: int) -> dict:
    """
    OpenAI API compatibility shim:
    Some model endpoints (notably gpt-5.* / gpt-4.1.* on certain providers)
    reject `max_tokens` and require `max_completion_tokens` instead.
    """
    mn = (model_name or "").lower()
    if mn.startswith("gpt-5") or mn.startswith("gpt-4.1"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def create_openai_client() -> OpenAI:
    """
    Create and return an OpenAI client using the same pattern as LLMCreator.
    Uses REMOTE_API_KEY/REMOTE_BASE_URL primarily, with fallbacks for common repo env keys.
    """
    load_dotenv(override=True)
    
    api_key = (
        os.getenv("REMOTE_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = (
        os.getenv("REMOTE_BASE_URL")
        or os.getenv("BASE_URL")
    )
    
    if not api_key:
        raise ValueError(
            "No API key found in environment variables. "
            "Set one of: REMOTE_API_KEY, API_KEY, or OPENAI_API_KEY."
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
    
    # Parse TTL to extract domain-specific information
    tbox_info = parse_ttl_tbox(ontology_path)
    
    # Load reference snippet for patterns (domain-agnostic patterns)
    ref_script_path = project_root / "sandbox" / "code" / "mcp_creation" / "mcp_creation.py"
    ref_snippet = ""
    if ref_script_path.exists():
        with open(ref_script_path, 'r', encoding='utf-8') as f:
            # Take first 20k chars showing key patterns
            ref_snippet = f.read()[:20000]
    
    # Load ontology content
    with open(ontology_path, 'r', encoding='utf-8') as f:
        ontology_ttl = f.read()
    
    # Format entity classes
    entity_classes_str = "\n".join(f"- {cls}" for cls in tbox_info['classes'])
    
    # Format object properties
    object_props_str = "\n".join(
        f"- {prop['name']}: domain={prop['domains']}, range={prop['ranges']}" 
        for prop in tbox_info['object_properties']
    )
    
    # Format datatype properties
    datatype_props_str = "\n".join(
        f"- {prop['name']}: domain={prop['domains']}"
        for prop in tbox_info['datatype_properties']
    )
    
    # Fill in the meta-prompt template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=tbox_info['namespace_uri'],
        reference_snippet=ref_snippet,
        ontology_ttl=ontology_ttl,
        entity_classes=entity_classes_str,
        object_properties=object_props_str,
        datatype_properties=datatype_props_str
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
    underlying_script_path: str
) -> str:
    """
    Build the prompt for generating a FastMCP main script using domain-agnostic meta-prompt.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        underlying_script_path: Path to the generated underlying script
        
    Returns:
        Complete prompt string with extracted information filled into meta-prompt
    """
    # Load domain-agnostic meta-prompt
    meta_prompt_template = load_meta_prompt('direct_main_script_prompt.md')
    
    # Parse TTL to extract domain-specific information
    tbox_info = parse_ttl_tbox(ontology_path)
    
    # Load reference main.py for patterns
    ref_main_path = project_root / "sandbox" / "code" / "mcp_creation" / "main.py"
    ref_main_snippet = ""
    if ref_main_path.exists():
        with open(ref_main_path, 'r', encoding='utf-8') as f:
            # Take first 25k chars showing structure
            ref_main_snippet = f.read()[:25000]
    
    # Load ontology content
    with open(ontology_path, 'r', encoding='utf-8') as f:
        ontology_ttl = f.read()
    
    # Extract function signatures from underlying script
    functions = extract_functions_from_underlying(underlying_script_path)
    
    # Format function signatures
    function_sigs_str = "\n".join(
        f"- {func['name']}: {func['signature']}"
        for func in functions
    )
    
    # Format entity classes
    entity_classes_str = "\n".join(f"- {cls}" for cls in tbox_info['classes'])
    
    # Format relationships (object properties)
    relationships_str = "\n".join(
        f"- {prop['name']}: {prop['domains']} -> {prop['ranges']}"
        for prop in tbox_info['object_properties']
    )
    
    # Fill in the meta-prompt template
    prompt = meta_prompt_template.format(
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=tbox_info['namespace_uri'],
        reference_main_snippet=ref_main_snippet,
        ontology_ttl=ontology_ttl,
        function_signatures=function_sigs_str,
        total_functions=len(functions),
        entity_classes=entity_classes_str,
        relationships=relationships_str
    )
    
    return prompt


async def generate_underlying_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate an underlying MCP script using direct LLM calls with domain-agnostic meta-prompts.
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology (e.g., 'ontosynthesis')
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts for API calls
    
    Returns:
        Path to generated script
    """
    print(f"\n📝 Generating underlying script via direct LLM call (domain-agnostic mode)...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    
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
                **_token_limit_kwargs(model_name, 16000)
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
    Generate a FastMCP main script using direct LLM calls with domain-agnostic meta-prompts.
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        underlying_script_path: Path to the generated underlying script
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts
    
    Returns:
        Path to generated script
    """
    print(f"\n📝 Generating main.py via direct LLM call (domain-agnostic mode)...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    
    # Build prompt using domain-agnostic meta-prompt + TTL parsing
    prompt = build_main_script_prompt(ontology_path, ontology_name, underlying_script_path)
    
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
                **_token_limit_kwargs(model_name, 16000)
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

