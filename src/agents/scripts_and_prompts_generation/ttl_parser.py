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


_GENERIC_INTEGRITY_ANNOTATION_FIELDS = {
    "instanceIntegrityRule": "instance_integrity_rules",
    "edgeIntegrityRule": "edge_integrity_rules",
    "orderingSemantics": "ordering_semantics",
    "typingIntegrityRule": "typing_integrity_rules",
}


def _empty_integrity_annotations() -> Dict[str, List[str]]:
    """Return the normalized integrity-annotation buckets used across generators."""
    return {
        "instance_integrity_rules": [],
        "edge_integrity_rules": [],
        "ordering_semantics": [],
        "typing_integrity_rules": [],
    }


def _merge_unique_rules(target: Dict[str, List[str]], source: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Merge integrity buckets while preserving order and removing duplicates."""
    for key, values in (source or {}).items():
        bucket = target.setdefault(key, [])
        for value in values or []:
            text = str(value).strip()
            if text and text not in bucket:
                bucket.append(text)
    return target


def _extract_integrity_annotations(g: Graph, subject) -> Dict[str, List[str]]:
    """Extract generic machine-readable integrity annotations from a subject."""
    out = _empty_integrity_annotations()
    for predicate, obj in g.predicate_objects(subject):
        bucket = _GENERIC_INTEGRITY_ANNOTATION_FIELDS.get(_get_local_name(predicate))
        if not bucket:
            continue
        text = str(obj).strip()
        if text and text not in out[bucket]:
            out[bucket].append(text)
    return out


def _has_any_integrity_annotations(annotations: Dict[str, List[str]]) -> bool:
    """Return True when any integrity bucket carries at least one value."""
    return any(bool(values) for values in (annotations or {}).values())


def _inherit_integrity_annotations(classes: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """Compute inherited integrity annotations for each class."""
    resolved: Dict[str, Dict[str, List[str]]] = {}
    visiting: set[str] = set()

    def resolve(class_name: str) -> Dict[str, List[str]]:
        if class_name in resolved:
            return resolved[class_name]
        if class_name in visiting:
            return _empty_integrity_annotations()

        visiting.add(class_name)
        merged = _empty_integrity_annotations()
        class_data = classes.get(class_name, {}) or {}
        for parent in class_data.get("parent_classes", []) or []:
            if parent in classes:
                _merge_unique_rules(merged, resolve(parent))
        _merge_unique_rules(merged, class_data.get("integrity_annotations", {}) or {})
        visiting.remove(class_name)
        resolved[class_name] = merged
        return merged

    for name in classes.keys():
        resolve(name)
    return resolved


def _contains_any_token(values: List[str], tokens: List[str]) -> bool:
    """Case-insensitive substring check across a rule bucket."""
    lowered = [str(v).strip().lower() for v in values or []]
    return any(token in value for value in lowered for token in tokens)


def extract_ontology_integrity_profile(ttl_path: str) -> Dict[str, Any]:
    """
    Parse ontology integrity annotations into a generic machine-readable profile.

    The resulting keys stay domain-agnostic so prompt/script generation can consume
    ontology-derived structure without hard-coding ontology names or class lists.
    """
    parsed = parse_ontology_ttl(ttl_path)
    classes = parsed.get("classes", {}) or {}
    properties = parsed.get("properties", {}) or {}
    inherited = _inherit_integrity_annotations(classes)

    ordered_member_classes: List[str] = []
    non_reusable_classes: List[str] = []
    parent_type_preserving_classes: List[str] = []
    most_specific_subclass_targets: Dict[str, List[str]] = {}

    for class_name in sorted(classes.keys()):
        annotations = inherited.get(class_name, _empty_integrity_annotations())
        ordering_semantics = annotations.get("ordering_semantics", []) or []
        instance_rules = annotations.get("instance_integrity_rules", []) or []
        typing_rules = annotations.get("typing_integrity_rules", []) or []

        if _contains_any_token(ordering_semantics, ["ordered_member", "ordered member", "ordered sequence", "ordered_procedure_member"]):
            ordered_member_classes.append(class_name)
        if _contains_any_token(instance_rules, ["fresh_individual", "do_not_reuse", "non_reusable", "never_reuse", "unique_member"]):
            non_reusable_classes.append(class_name)
        if typing_rules:
            parent_type_preserving_classes.append(class_name)
        if _contains_any_token(typing_rules, ["prefer_most_specific_subclass", "most specific subclass", "specific subclass"]):
            children = sorted(
                child_name
                for child_name, child_data in classes.items()
                if class_name in (child_data.get("parent_classes", []) or [])
            )
            if children:
                most_specific_subclass_targets[class_name] = children

    individually_linked_object_properties: List[str] = []
    single_valued_ordering_properties: List[str] = []
    property_constraints: Dict[str, Dict[str, Any]] = {}
    for prop_name in sorted(properties.keys()):
        prop_data = properties.get(prop_name, {}) or {}
        annotations = prop_data.get("integrity_annotations", {}) or _empty_integrity_annotations()
        prop_entry = {
            "kind": prop_data.get("kind", ""),
            "domains": prop_data.get("domains", []) or [],
            "range": prop_data.get("range", ""),
            "instance_integrity_rules": annotations.get("instance_integrity_rules", []) or [],
            "edge_integrity_rules": annotations.get("edge_integrity_rules", []) or [],
            "ordering_semantics": annotations.get("ordering_semantics", []) or [],
            "typing_integrity_rules": annotations.get("typing_integrity_rules", []) or [],
        }
        property_constraints[prop_name] = prop_entry
        if _contains_any_token(prop_entry["edge_integrity_rules"], ["link_each_member_individually", "individual_member_links", "one_edge_per_member", "aggregate"]):
            individually_linked_object_properties.append(prop_name)
        if _contains_any_token(prop_entry["ordering_semantics"], ["single_scalar", "single_slot", "single_order", "one order"]) or _contains_any_token(
            prop_entry["instance_integrity_rules"],
            ["single_order", "one_order", "multiple_order_values", "single_slot"],
        ):
            single_valued_ordering_properties.append(prop_name)

    class_constraints = {}
    for class_name in sorted(classes.keys()):
        ann = inherited.get(class_name, _empty_integrity_annotations())
        class_constraints[class_name] = {
            "instance_integrity_rules": ann.get("instance_integrity_rules", []) or [],
            "edge_integrity_rules": ann.get("edge_integrity_rules", []) or [],
            "ordering_semantics": ann.get("ordering_semantics", []) or [],
            "typing_integrity_rules": ann.get("typing_integrity_rules", []) or [],
            "parent_classes": classes.get(class_name, {}).get("parent_classes", []) or [],
        }

    return {
        "class_constraints": class_constraints,
        "property_constraints": property_constraints,
        "ordered_member_classes": ordered_member_classes,
        "non_reusable_classes": non_reusable_classes,
        "parent_type_preserving_classes": parent_type_preserving_classes,
        "most_specific_subclass_targets": most_specific_subclass_targets,
        "individually_linked_object_properties": individually_linked_object_properties,
        "single_valued_ordering_properties": single_valued_ordering_properties,
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
        
        integrity_annotations = _extract_integrity_annotations(g, cls)
        classes[cls_local] = {
            "iri": str(cls),
            "parent_classes": parents,
            "comment": comment_text,
            "integrity_annotations": integrity_annotations,
            "datatype_properties": {},
            "object_properties": {}
        }
    
    properties = {}

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
        
        integrity_annotations = _extract_integrity_annotations(g, prop)
        comment_vals = list(g.objects(prop, RDFS.comment))
        comment_text = str(comment_vals[0]) if comment_vals else ""
        properties[prop_local] = {
            "iri": str(prop),
            "kind": "datatype",
            "domains": domains,
            "range": range_type,
            "comment": comment_text,
            "integrity_annotations": integrity_annotations,
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
        domains = [_get_local_name(d) for d in g.objects(prop, RDFS.domain) 
                   if _get_local_name(d)]
        
        # Get range - object class
        ranges = list(g.objects(prop, RDFS.range))
        range_class = _get_local_name(ranges[0]) if ranges else "owl:Thing"
        
        integrity_annotations = _extract_integrity_annotations(g, prop)
        comment_vals = list(g.objects(prop, RDFS.comment))
        comment_text = str(comment_vals[0]) if comment_vals else ""
        properties[prop_local] = {
            "iri": str(prop),
            "kind": "object",
            "domains": domains,
            "range": range_class,
            "comment": comment_text,
            "integrity_annotations": integrity_annotations,
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
            "has_integrity_annotations": any(
                _has_any_integrity_annotations((cls_data or {}).get("integrity_annotations", {}) or {})
                for cls_data in classes.values()
            ) or any(
                _has_any_integrity_annotations((prop_data or {}).get("integrity_annotations", {}) or {})
                for prop_data in properties.values()
            ),
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

