#!/usr/bin/env python3
"""
TTL Parser for Ontology Schema Extraction

Parses OWL/RDF ontology files and extracts a structured mapping of:
- Classes and their hierarchies
- Properties (datatype and object) with domains and ranges
- For each class, all applicable properties

Output is a clear, structured representation that can be consumed by code generation agents.
"""

from rdflib import BNode, Graph, RDF, RDFS, OWL
from rdflib.collection import Collection
from typing import Dict, List, Any
import json


def _normalize_literal_text(value: Any) -> str:
    """Normalize RDF literal newlines for deterministic cross-platform output."""
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def extract_ontology_integrity_profile(ttl_path: str) -> Dict[str, Any]:
    """
    Build a generic structural profile from standard OWL/RDFS declarations.

    The stable profile keys are retained for downstream consumers, but private
    pipeline annotation predicates and ontology-local rule tokens are deliberately
    not parsed. Operational policies belong in comments, meta-prompts, or generic
    runtime configuration rather than the T-Box.
    """
    parsed = parse_ontology_ttl(ttl_path)
    classes = parsed.get("classes", {}) or {}
    properties = parsed.get("properties", {}) or {}
    class_constraints = {
        class_name: {
            "parent_classes": classes.get(class_name, {}).get(
                "parent_classes", []
            )
            or [],
        }
        for class_name in sorted(classes)
    }
    property_constraints = {
        prop_name: {
            "kind": prop_data.get("kind", ""),
            "domains": prop_data.get("domains", []) or [],
            "range": prop_data.get("range", ""),
        }
        for prop_name in sorted(properties)
        for prop_data in [properties.get(prop_name, {}) or {}]
    }

    most_specific_subclass_targets = {
        class_name: children
        for class_name in sorted(classes)
        for children in [
            sorted(
                child_name
                for child_name, child_data in classes.items()
                if class_name in (child_data.get("parent_classes", []) or [])
            )
        ]
        if children
    }

    integer_ranges = {"integer", "int", "long", "nonNegativeInteger", "positiveInteger"}
    ordering_properties = {
        prop_name
        for prop_name, prop_data in properties.items()
        if prop_data.get("kind") == "datatype"
        and str(prop_data.get("range") or "") in integer_ranges
        and "order" in str(prop_data.get("comment") or "").lower()
        and any(
            token in str(prop_data.get("comment") or "").lower()
            for token in ("index", "sequence", "position", "contiguous", "no gap")
        )
    }
    ordered_member_classes = {
        str(domain)
        for prop_name in ordering_properties
        for domain in (properties.get(prop_name, {}).get("domains") or [])
        if str(domain) in classes
    }
    changed = True
    while changed:
        changed = False
        for class_name, class_data in classes.items():
            if class_name in ordered_member_classes:
                continue
            if set(class_data.get("parent_classes") or []) & ordered_member_classes:
                ordered_member_classes.add(class_name)
                changed = True
    collection_properties = {
        prop_name
        for prop_name, prop_data in properties.items()
        if prop_data.get("kind") == "object"
        and str(prop_data.get("range") or "") in ordered_member_classes
        and (
            "order" in str(prop_data.get("comment") or "").lower()
            or any(
                order_prop.lower()
                in str(prop_data.get("comment") or "").lower()
                for order_prop in ordering_properties
            )
        )
    }
    parent_type_preserving = {
        class_name
        for class_name in ordered_member_classes
        if set(classes.get(class_name, {}).get("parent_classes") or [])
        & ordered_member_classes
    }

    return {
        "class_constraints": class_constraints,
        "property_constraints": property_constraints,
        "ordered_member_classes": sorted(ordered_member_classes),
        "non_reusable_classes": [],
        "parent_type_preserving_classes": sorted(parent_type_preserving),
        "most_specific_subclass_targets": most_specific_subclass_targets,
        "individually_linked_object_properties": sorted(collection_properties),
        "single_valued_ordering_properties": sorted(ordering_properties),
    }


def format_ontology_integrity_guidance(
    profile: Dict[str, Any],
    *,
    include_machine_readable: bool = True,
) -> str:
    """Render ontology-derived integrity constraints as generic prompt guidance."""
    if not isinstance(profile, dict) or not profile:
        return ""

    bullets: List[str] = []
    if profile.get("ordered_member_classes"):
        bullets.append(
            "- For ontology-marked ordered members, create one individual per semantic member. "
            "Never collapse multiple members into a single node."
        )
    if profile.get("single_valued_ordering_properties"):
        bullets.append(
            "- When an ontology-marked ordering property carries sequence position semantics, "
            "store exactly one scalar order slot per individual."
        )
    if profile.get("individually_linked_object_properties"):
        bullets.append(
            "- When an ontology-marked parent-to-member property requires individual links, attach one edge per member "
            "instead of pointing a parent at an aggregate placeholder."
        )
    if profile.get("parent_type_preserving_classes"):
        bullets.append(
            "- When a concrete subclass carries a parent's required semantics, preserve both the concrete subclass type "
            "and the compatible parent type in the emitted graph."
        )
    if profile.get("most_specific_subclass_targets"):
        bullets.append(
            "- When an extracted instance belongs to a parent class that has ontology-declared concrete subclasses, "
            "choose the most specific supported subclass as the emitted class/type label. Do not emit the parent "
            "class as the instance type when subclass evidence or subclass-specific properties are present."
        )
    if profile.get("non_reusable_classes"):
        bullets.append(
            "- For ontology-marked non-reusable members, always mint a fresh individual within the parent sequence; "
            "do not deduplicate or reuse them by label."
        )

    if not bullets:
        return ""

    lines = ["Ontology-derived integrity contract:"]
    lines.extend(bullets)
    if include_machine_readable:
        machine_profile = {
            "ordered_member_classes": profile.get("ordered_member_classes", []) or [],
            "single_valued_ordering_properties": profile.get("single_valued_ordering_properties", []) or [],
            "individually_linked_object_properties": profile.get("individually_linked_object_properties", []) or [],
            "non_reusable_classes": profile.get("non_reusable_classes", []) or [],
            "parent_type_preserving_classes": profile.get("parent_type_preserving_classes", []) or [],
            "most_specific_subclass_targets": profile.get("most_specific_subclass_targets", {}) or {},
        }
        lines.append("")
        lines.append("Machine-readable integrity profile (ontology-derived):")
        lines.append(json.dumps(machine_profile, indent=2, ensure_ascii=False))
    return "\n".join(lines)


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
        print("Warning: Error parsing as Turtle, trying as N3/Notation3...")
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
        if isinstance(cls, BNode):
            continue
        cls_local = _get_local_name(cls)
        if not cls_local:
            continue
        
        # Skip blank nodes (anonymous classes like unionOf)
        # These have weird auto-generated names like "nd71c6ac4efca4d599bd435b1605e7ccbb46"
        if cls_local.startswith("n") and len(cls_local) > 30 and all(c in "0123456789abcdef" for c in cls_local[1:]):
            continue
        
        # Get parent classes
        parents = [
            _get_local_name(p)
            for p in g.objects(cls, RDFS.subClassOf)
            if not isinstance(p, BNode) and _get_local_name(p)
        ]
        
        # Get rdfs:comment for reusability and other annotations
        comments = list(g.objects(cls, RDFS.comment))
        comment_text = _normalize_literal_text(comments[0]) if comments else ""
        
        classes[cls_local] = {
            "iri": str(cls),
            "parent_classes": parents,
            "comment": comment_text,
            "datatype_properties": {},
            "object_properties": {}
        }
    
    properties = {}

    def domain_locals(prop: Any) -> List[str]:
        """Expand named and owl:unionOf property domains into class locals."""
        result: List[str] = []
        for domain in g.objects(prop, RDFS.domain):
            members = list(g.objects(domain, OWL.unionOf)) if isinstance(domain, BNode) else []
            nodes = list(Collection(g, members[0])) if members else [domain]
            for node in nodes:
                local = _get_local_name(node)
                if local and local not in result:
                    result.append(local)
        return result

    # Extract datatype properties
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        prop_local = _get_local_name(prop)
        if not prop_local:
            continue
        
        # Get domain(s) - classes this property applies to
        domains = domain_locals(prop)
        
        # Get range - datatype
        ranges = list(g.objects(prop, RDFS.range))
        range_type = _get_local_name(ranges[0]) if ranges else "xsd:string"
        
        comment_vals = list(g.objects(prop, RDFS.comment))
        comment_text = (
            _normalize_literal_text(comment_vals[0]) if comment_vals else ""
        )
        value_kinds = []
        for pred, obj in g.predicate_objects(prop):
            if _get_local_name(pred) == "valueKind":
                text = str(obj).strip()
                if text and text not in value_kinds:
                    value_kinds.append(text)
        properties[prop_local] = {
            "iri": str(prop),
            "kind": "datatype",
            "domains": domains,
            "range": range_type,
            "comment": comment_text,
            "value_kind": value_kinds[0] if value_kinds else "",
            "value_kinds": value_kinds,
        }

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
        domains = domain_locals(prop)
        
        # Get range - object class
        ranges = list(g.objects(prop, RDFS.range))
        range_class = _get_local_name(ranges[0]) if ranges else "owl:Thing"
        
        comment_vals = list(g.objects(prop, RDFS.comment))
        comment_text = (
            _normalize_literal_text(comment_vals[0]) if comment_vals else ""
        )
        properties[prop_local] = {
            "iri": str(prop),
            "kind": "object",
            "domains": domains,
            "range": range_class,
            "comment": comment_text,
        }

        # Add to each domain class
        for domain in domains:
            if domain in classes:
                classes[domain]["object_properties"][prop_local] = range_class
    
    # Inherit properties from parent classes
    _inherit_properties(classes)
    
    return {
        "classes": classes,
        "properties": properties,
        "metadata": {
            "total_classes": len(classes),
            "source_file": ttl_path,
        }
    }


def analyze_ontology_shape(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Derive lightweight topology signals from parsed ontology structure.

    A "super-flat" ontology is defined here as:
    - exactly one ontology class
    - zero object properties across all classes
    - at least one datatype property on that class
    """
    classes = parsed.get("classes", {}) or {}
    class_names = sorted(classes.keys())

    total_object_properties = 0
    total_datatype_properties = 0
    top_level_class = class_names[0] if len(class_names) == 1 else None

    for cls_data in classes.values():
        total_object_properties += len(cls_data.get("object_properties", {}) or {})
        total_datatype_properties += len(cls_data.get("datatype_properties", {}) or {})

    is_super_flat = (
        len(class_names) == 1
        and total_object_properties == 0
        and total_datatype_properties > 0
    )

    return {
        "class_names": class_names,
        "top_level_class": top_level_class,
        "total_classes": len(class_names),
        "total_object_properties": total_object_properties,
        "total_datatype_properties": total_datatype_properties,
        "is_super_flat": is_super_flat,
    }


def detect_super_flat_ontology(ttl_path: str) -> Dict[str, Any]:
    """
    Parse a TTL file and return topology analysis for generator/runtime branching.
    """
    parsed = parse_ontology_ttl(ttl_path)
    analysis = analyze_ontology_shape(parsed)
    analysis["source_file"] = ttl_path
    return analysis


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
    """Example usage: parse an ontology TTL and output a structured mapping."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python ttl_parser.py <ontology.ttl> [output.json] [output.md]")
        print("\nExample:")
        print("  python ttl_parser.py path/to/ontology.ttl")
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
        first_class = list(parsed["classes"].keys())[0]
        fc = parsed["classes"][first_class]
        print(f"\nExample class ({first_class}):")
        print(f"  Parent: {fc['parent_classes']}")
        print(f"  Datatype properties: {list(fc['datatype_properties'].keys())}")
        print(f"  Object properties: {list(fc['object_properties'].keys())}")


if __name__ == "__main__":
    main()

