#!/usr/bin/env python3
"""
TTL Parser for Ontology Schema Extraction

Parses OWL/RDF ontology files and extracts a structured mapping of:
- Classes and their hierarchies
- Properties (datatype and object) with domains and ranges
- For each class, all applicable properties

Output is a clear, structured representation that can be consumed by code generation agents.
"""

from rdflib import Graph, Namespace, RDF, RDFS, OWL, XSD
from typing import Dict, List, Set, Tuple, Any
import json
from collections import defaultdict
from pathlib import Path


def parse_ontology_ttl(ttl_path: str) -> Dict[str, Any]:
    """
    Parse an ontology TTL file and extract structured class/property information.
    
    Args:
        ttl_path: Path to the TTL ontology file
    
    Returns:
        Structured dictionary with classes and their properties
    """
    g = Graph()
    try:
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        print(f"Warning: Error parsing as Turtle, trying as N3/Notation3...")
        print(f"Error: {e}")
        try:
            g.parse(ttl_path, format="n3")
        except Exception as e2:
            print(f"Error parsing as N3 as well: {e2}")
            print("Attempting to read without strict parsing...")
            # Try to load as best-effort
            import re
            with open(ttl_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Clean up potential issues
            content = re.sub(r';\s*\.', ' .', content)  # Fix "; ." patterns
            content = re.sub(r';\s*;\s*', ' ; ', content)  # Fix ";;" patterns  
            try:
                g.parse(data=content, format="turtle")
            except Exception as e3:
                print(f"Still failed: {e3}")
                print("Returning empty structure")
                return {"classes": {}, "metadata": {"error": str(e3)}}
    
    # Extract all classes
    classes = {}
    for cls in g.subjects(RDF.type, OWL.Class):
        cls_local = _get_local_name(cls)
        if not cls_local:
            continue
        
        # Skip blank nodes (anonymous classes like unionOf)
        # These have weird auto-generated names like "nd71c6ac4efca4d599bd435b1605e7ccbb46"
        if cls_local.startswith("n") and len(cls_local) > 30 and all(c in "0123456789abcdef" for c in cls_local[1:]):
            continue
        
        # Get parent classes
        parents = [_get_local_name(p) for p in g.objects(cls, RDFS.subClassOf) 
                   if _get_local_name(p)]
        
        # Get rdfs:comment for reusability and other annotations
        comments = list(g.objects(cls, RDFS.comment))
        comment_text = str(comments[0]) if comments else ""
        
        classes[cls_local] = {
            "iri": str(cls),
            "parent_classes": parents,
            "comment": comment_text,
            "datatype_properties": {},
            "object_properties": {}
        }
    
    # Extract datatype properties
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        prop_local = _get_local_name(prop)
        if not prop_local:
            continue
        
        # Get domain(s) - classes this property applies to
        domains = [_get_local_name(d) for d in g.objects(prop, RDFS.domain) 
                   if _get_local_name(d)]
        
        # Get range - datatype
        ranges = list(g.objects(prop, RDFS.range))
        range_type = _get_local_name(ranges[0]) if ranges else "xsd:string"
        
        # Add to each domain class
        for domain in domains:
            if domain in classes:
                classes[domain]["datatype_properties"][prop_local] = range_type
    
    # Extract object properties
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        prop_local = _get_local_name(prop)
        if not prop_local:
            continue
        
        # Get domain(s)
        domains = [_get_local_name(d) for d in g.objects(prop, RDFS.domain) 
                   if _get_local_name(d)]
        
        # Get range - object class
        ranges = list(g.objects(prop, RDFS.range))
        range_class = _get_local_name(ranges[0]) if ranges else "owl:Thing"
        
        # Add to each domain class
        for domain in domains:
            if domain in classes:
                classes[domain]["object_properties"][prop_local] = range_class
    
    # Inherit properties from parent classes
    _inherit_properties(classes)
    
    return {
        "classes": classes,
        "metadata": {
            "total_classes": len(classes),
            "source_file": ttl_path
        }
    }


def _get_local_name(uri) -> str:
    """Extract local name from URI."""
    if uri is None:
        return ""
    uri_str = str(uri)
    if "#" in uri_str:
        return uri_str.split("#")[-1]
    elif "/" in uri_str:
        return uri_str.split("/")[-1]
    return uri_str


def _inherit_properties(classes: Dict[str, Any]):
    """
    Inherit properties from parent classes.
    Modifies classes dict in place.
    """
    # Build inheritance graph
    for cls_name, cls_data in classes.items():
        for parent_name in cls_data["parent_classes"]:
            if parent_name in classes:
                parent = classes[parent_name]
                # Inherit datatype properties
                for prop, dtype in parent["datatype_properties"].items():
                    if prop not in cls_data["datatype_properties"]:
                        cls_data["datatype_properties"][prop] = dtype
                # Inherit object properties
                for prop, range_cls in parent["object_properties"].items():
                    if prop not in cls_data["object_properties"]:
                        cls_data["object_properties"][prop] = range_cls


def format_class_properties_markdown(parsed: Dict[str, Any]) -> str:
    """
    Format parsed ontology as markdown for agent consumption.
    
    Returns:
        Markdown string with clear property listings for each class
    """
    lines = ["# Ontology Schema - Structured Property Mapping", ""]
    
    classes = parsed["classes"]
    
    # Sort classes by name
    for cls_name in sorted(classes.keys()):
        cls_data = classes[cls_name]
        lines.append(f"## Class: `{cls_name}`")
        lines.append("")
        
        if cls_data["parent_classes"]:
            parents_str = ", ".join(f"`{p}`" for p in cls_data["parent_classes"])
            lines.append(f"**Parent Classes:** {parents_str}")
            lines.append("")
        
        if cls_data["comment"]:
            lines.append(f"**Comment:** {cls_data['comment']}")
            lines.append("")
        
        # Datatype properties
        if cls_data["datatype_properties"]:
            lines.append("### Datatype Properties")
            lines.append("")
            lines.append("| Property | Range |")
            lines.append("|----------|-------|")
            for prop, dtype in sorted(cls_data["datatype_properties"].items()):
                lines.append(f"| `{prop}` | `{dtype}` |")
            lines.append("")
        
        # Object properties
        if cls_data["object_properties"]:
            lines.append("### Object Properties")
            lines.append("")
            lines.append("| Property | Range (Target Class) |")
            lines.append("|----------|----------------------|")
            for prop, range_cls in sorted(cls_data["object_properties"].items()):
                lines.append(f"| `{prop}` | `{range_cls}` |")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    return "\n".join(lines)


def main():
    """Example usage: parse ontosynthesis.ttl and output structured mapping."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python ttl_parser.py <ontology.ttl> [output.json] [output.md]")
        print("\nExample:")
        print("  python ttl_parser.py data/ontologies/ontosynthesis.ttl")
        sys.exit(1)
    
    ttl_path = sys.argv[1]
    
    print(f"Parsing {ttl_path}...")
    parsed = parse_ontology_ttl(ttl_path)
    
    # Output JSON
    json_path = sys.argv[2] if len(sys.argv) > 2 else ttl_path.replace(".ttl", "_parsed.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)
    print(f"[OK] Saved JSON to {json_path}")
    
    # Output Markdown
    md_path = sys.argv[3] if len(sys.argv) > 3 else ttl_path.replace(".ttl", "_parsed.md")
    markdown = format_class_properties_markdown(parsed)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"[OK] Saved Markdown to {md_path}")
    
    # Summary
    total_classes = parsed.get('metadata', {}).get('total_classes', len(parsed.get('classes', {})))
    print(f"\n[OK] Parsed {total_classes} classes")
    if total_classes > 0:
        print(f"\nExample class (HeatChill):")
        if "HeatChill" in parsed.get("classes", {}):
            hc = parsed["classes"]["HeatChill"]
            print(f"  Parent: {hc['parent_classes']}")
            print(f"  Datatype properties: {list(hc['datatype_properties'].keys())}")
            print(f"  Object properties: {list(hc['object_properties'].keys())}")
        else:
            # Show first class as example
            first_class = list(parsed["classes"].keys())[0]
            fc = parsed["classes"][first_class]
            print(f"Example class ({first_class}):")
            print(f"  Parent: {fc['parent_classes']}")
            print(f"  Datatype properties: {list(fc['datatype_properties'].keys())}")
            print(f"  Object properties: {list(fc['object_properties'].keys())}")


if __name__ == "__main__":
    main()

