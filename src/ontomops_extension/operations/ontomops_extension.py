# ontomops_extension.py
# OntoMOPs A-Box population utilities
# - File-locked, persistent memory (memory/memory.ttl)
# - Create-then-connect discipline, with stub creation for referenced nodes
# - All inputs/outputs use simple Python types (str, int, float, bool)
# - Coverage of classes and properties from the OntoMOPs subgraph

import os
import re
import uuid
import time
import tempfile
import unicodedata
from contextlib import contextmanager
from typing import Optional, Tuple, List, Dict

from filelock import FileLock
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL, XSD

# ----------------------------------------------------------------------------------------------------------------------
# Namespaces (from ontology)
# ----------------------------------------------------------------------------------------------------------------------
KG = Namespace("https://www.theworldavatar.com/kg/")
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")

DC = Namespace("http://purl.org/dc/elements/1.1/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")

# ----------------------------------------------------------------------------------------------------------------------
# Memory management & locking
# ----------------------------------------------------------------------------------------------------------------------
MEM_DIR = "memory"
MEM_TTL = os.path.join(MEM_DIR, "memory_ontomops.ttl")
MEM_LOCK = os.path.join(MEM_DIR, "memory_ontomops.lock")
os.makedirs(MEM_DIR, exist_ok=True)

SESSION_NODE = URIRef("https://www.theworldavatar.com/kg/OntoMOPs/instance/Session")

@contextmanager
def locked_graph(timeout: float = 30.0):
    """Lock, load, yield, then atomically write-back the memory graph."""
    lock = FileLock(MEM_LOCK)
    lock.acquire(timeout=timeout)
    g = Graph()
    # Bind prefixes for nicer serialization and readability
    g.bind("kg", KG)
    g.bind("ontomops", ONTOMOPS)
    g.bind("dc", DC)
    g.bind("owl", OWL)
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)
    g.bind("xsd", XSD)
    if os.path.exists(MEM_TTL):
        g.parse(MEM_TTL, format="turtle")
    try:
        yield g
        fd, tmp = tempfile.mkstemp(dir=MEM_DIR, suffix=".ttl.tmp")
        os.close(fd)
        g.serialize(destination=tmp, format="turtle")
        os.replace(tmp, MEM_TTL)
    finally:
        lock.release()

# ----------------------------------------------------------------------------------------------------------------------
# Session hash management & IRI helpers
# ----------------------------------------------------------------------------------------------------------------------

def _get_or_create_session_hash(g: Graph) -> str:
    """Return a persistent short session hash. Create and store if missing (dc:identifier on SESSION_NODE)."""
    for _, _, h in g.triples((SESSION_NODE, DC.identifier, None)):
        if isinstance(h, Literal):
            return str(h)
    # Create
    sh = uuid.uuid4().hex[:8]
    g.add((SESSION_NODE, RDF.type, OWL.NamedIndividual))
    g.add((SESSION_NODE, RDFS.label, Literal(f"OntoMOPs Session {sh}")))
    g.add((SESSION_NODE, DC.identifier, Literal(sh)))
    return sh

INST_BASE = "https://www.theworldavatar.com/kg/OntoMOPs/instance"

def _slugify(text: str) -> str:
    """Unicode-aware, deterministic slug."""
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\-_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "entity"

def _iri_exists(g: Graph, iri: URIRef) -> bool:
    return (iri, None, None) in g or (None, None, iri) in g

def _resolve_class_uri(class_local: str) -> Optional[URIRef]:
    """Try to resolve a local name to a known class URI in typical namespaces."""
    for ns in (ONTOMOPS,):
        try:
            uri = getattr(ns, class_local)
            # rdflib Namespace returns URIRef on attribute access
            if isinstance(uri, URIRef):
                return uri
        except Exception:
            continue
    return None

def _find_by_type_and_label(g: Graph, class_uri: URIRef, label: str) -> Optional[URIRef]:
    """Return subject with given rdf:type and exact rdfs:label if exists."""
    lbl = Literal(label)
    for s in g.subjects(RDF.type, class_uri):
        if (s, RDFS.label, lbl) in g:
            return s
    return None

def _mint_iri(g: Graph, class_local: str, label: str) -> URIRef:
    """
    Deterministic IRI minting.
    Rule:
      1) If an individual with rdf:type=class_uri and rdfs:label==label exists, reuse its IRI.
      2) Else mint INST_BASE/<ClassLocal>/<slug(label)>, without numeric suffixes.
      3) If the canonical IRI already exists in graph, reuse it.
    """
    class_uri = _resolve_class_uri(class_local)
    if class_uri is not None:
        existing = _find_by_type_and_label(g, class_uri, label)
        if existing is not None:
            return existing

    slug = _slugify(label)
    base = URIRef(f"{INST_BASE}/{class_local}/{slug}")
    if _iri_exists(g, base):
        return base
    return base

# ----------------------------------------------------------------------------------------------------------------------
# Generic helpers: ensure individual exists with expected type and label
# ----------------------------------------------------------------------------------------------------------------------

def _ensure_individual(g: Graph, iri_str: str, expected_type: URIRef, default_label: Optional[str] = None) -> URIRef:
    iri = URIRef(iri_str)
    # If not present, create a minimal stub
    if (iri, RDF.type, None) not in g:
        g.add((iri, RDF.type, expected_type))
        g.add((iri, RDFS.label, Literal(default_label or iri_str)))
    else:
        # Ensure at least typed with expected_type
        if (iri, RDF.type, expected_type) not in g:
            g.add((iri, RDF.type, expected_type))
        # Ensure it has a label
        if (iri, RDFS.label, None) not in g:
            g.add((iri, RDFS.label, Literal(default_label or iri_str)))
    return iri

# ----------------------------------------------------------------------------------------------------------------------
# Memory API
# ----------------------------------------------------------------------------------------------------------------------

def init_memory() -> str:
    """Initialize or resume the global memory graph and return the session hash."""
    with locked_graph() as g:
        sh = _get_or_create_session_hash(g)
    return sh

def inspect_memory() -> str:
    """Return a detailed summary of the current graph: IRIs, types, labels, attributes, and connections."""
    lines: List[str] = []
    with locked_graph() as g:
        lines.append("=== OntoMOPs Memory Summary ===")
        # Group by subject
        subjects = set()
        for s, _, _ in g:
            if isinstance(s, URIRef):
                subjects.add(s)
        for s in sorted(subjects, key=lambda u: str(u)):
            lines.append(f"\nSubject: {s}")
            # types
            types = [str(o) for o in g.objects(s, RDF.type)]
            if types:
                lines.append("  rdf:type:")
                for t in types:
                    lines.append(f"    - {t}")
            # label
            for lbl in g.objects(s, RDFS.label):
                lines.append(f"  rdfs:label: {str(lbl)}")
            # data properties
            for p, o in g.predicate_objects(s):
                if p in (RDF.type, RDFS.label):
                    continue
                if isinstance(o, Literal):
                    lines.append(f"  {g.namespace_manager.normalizeUri(p)}: {o} ({o.datatype or 'xsd:string'})")
            # object properties
            for p, o in g.predicate_objects(s):
                if p in (RDF.type, RDFS.label):
                    continue
                if isinstance(o, URIRef):
                    lines.append(f"  {g.namespace_manager.normalizeUri(p)} -> {o}")
    return "\n".join(lines)

def export_memory(file_name: str) -> str:
    """Serialize the entire memory graph to a Turtle file (.ttl) and return the absolute path."""
    if not file_name.endswith(".ttl"):
        raise ValueError("Only .ttl allowed")
    with locked_graph() as g:
        g.serialize(destination=file_name, format="turtle")
    return os.path.abspath(file_name)

# ----------------------------------------------------------------------------------------------------------------------
# OntoMOPs Core Classes
# ----------------------------------------------------------------------------------------------------------------------

def create_chemical_building_unit(name: str, iri: Optional[str] = None) -> str:
    """
    Create ontomops:ChemicalBuildingUnit.
    
    Args:
        name: Human-readable name for the CBU
        iri: Optional existing IRI to use instead of minting a new one
    """
    with locked_graph() as g:
        if iri:
            # Use existing IRI
            iri_ref = URIRef(iri)
            g.add((iri_ref, RDF.type, ONTOMOPS.ChemicalBuildingUnit))
            g.add((iri_ref, RDFS.label, Literal(name)))
        else:
            # Mint new IRI
            iri_ref = _mint_iri(g, "ChemicalBuildingUnit", name)
            g.add((iri_ref, RDF.type, ONTOMOPS.ChemicalBuildingUnit))
            g.add((iri_ref, RDFS.label, Literal(name)))
        return str(iri_ref)

def create_metal_organic_polyhedron(name: str,
                                   ccdc_number: Optional[str] = None,
                                   mop_formula: Optional[str] = None,
                                   iri: Optional[str] = None) -> str:
    """
    Create ontomops:MetalOrganicPolyhedron.
    
    Args:
        name: Human-readable name for the MOP
        ccdc_number: CCDC number of the MOP (optional)
        mop_formula: Chemical formula of the MOP (optional)
        iri: Optional existing IRI to use instead of minting a new one
    """
    with locked_graph() as g:
        if iri:
            # Use existing IRI
            iri_ref = URIRef(iri)
            g.add((iri_ref, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
            g.add((iri_ref, RDFS.label, Literal(name)))
        else:
            # Mint new IRI
            iri_ref = _mint_iri(g, "MetalOrganicPolyhedron", name)
            g.add((iri_ref, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
            g.add((iri_ref, RDFS.label, Literal(name)))
        
        # Add CCDC number if provided
        if ccdc_number:
            g.add((iri_ref, ONTOMOPS.hasCCDCNumber, Literal(ccdc_number)))
        
        # Add MOP formula if provided
        if mop_formula:
            g.add((iri_ref, ONTOMOPS.hasMOPFormula, Literal(mop_formula)))
        
        return str(iri_ref)

# ----------------------------------------------------------------------------------------------------------------------
# Property updaters
# ----------------------------------------------------------------------------------------------------------------------

def update_mop_ccdc_number(mop_iri: str, ccdc_number: str) -> str:
    """Update or set the CCDC number for a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        mop = _ensure_individual(g, mop_iri, ONTOMOPS.MetalOrganicPolyhedron)
        # Remove existing CCDC numbers
        for o in list(g.objects(mop, ONTOMOPS.hasCCDCNumber)):
            g.remove((mop, ONTOMOPS.hasCCDCNumber, o))
        # Add new CCDC number
        g.add((mop, ONTOMOPS.hasCCDCNumber, Literal(ccdc_number)))
        return str(mop)

def update_mop_formula(mop_iri: str, mop_formula: str) -> str:
    """Update or set the MOP formula for a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        mop = _ensure_individual(g, mop_iri, ONTOMOPS.MetalOrganicPolyhedron)
        # Remove existing formulas
        for o in list(g.objects(mop, ONTOMOPS.hasMOPFormula)):
            g.remove((mop, ONTOMOPS.hasMOPFormula, o))
        # Add new formula
        g.add((mop, ONTOMOPS.hasMOPFormula, Literal(mop_formula)))
        return str(mop)

# ----------------------------------------------------------------------------------------------------------------------
# Relationship creators
# ----------------------------------------------------------------------------------------------------------------------

def add_cbu_to_mop(mop_iri: str, cbu_iri: str) -> str:
    """Add a ChemicalBuildingUnit to a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        mop = _ensure_individual(g, mop_iri, ONTOMOPS.MetalOrganicPolyhedron)
        cbu = _ensure_individual(g, cbu_iri, ONTOMOPS.ChemicalBuildingUnit)
        g.add((mop, ONTOMOPS.hasChemicalBuildingUnit, cbu))
        return str(mop)

def remove_cbu_from_mop(mop_iri: str, cbu_iri: str) -> str:
    """Remove a ChemicalBuildingUnit from a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        mop = _ensure_individual(g, mop_iri, ONTOMOPS.MetalOrganicPolyhedron)
        cbu = _ensure_individual(g, cbu_iri, ONTOMOPS.ChemicalBuildingUnit)
        g.remove((mop, ONTOMOPS.hasChemicalBuildingUnit, cbu))
        return str(mop)

# ----------------------------------------------------------------------------------------------------------------------
# Query utilities
# ----------------------------------------------------------------------------------------------------------------------

def find_mops_by_ccdc_number(ccdc_number: str) -> List[str]:
    """Find all MetalOrganicPolyhedra with a specific CCDC number."""
    with locked_graph() as g:
        results = []
        for s in g.subjects(ONTOMOPS.hasCCDCNumber, Literal(ccdc_number)):
            if (s, RDF.type, ONTOMOPS.MetalOrganicPolyhedron) in g:
                results.append(str(s))
        return results

def find_mops_by_formula(mop_formula: str) -> List[str]:
    """Find all MetalOrganicPolyhedra with a specific formula."""
    with locked_graph() as g:
        results = []
        for s in g.subjects(ONTOMOPS.hasMOPFormula, Literal(mop_formula)):
            if (s, RDF.type, ONTOMOPS.MetalOrganicPolyhedron) in g:
                results.append(str(s))
        return results

def get_mop_cbus(mop_iri: str) -> List[str]:
    """Get all ChemicalBuildingUnits associated with a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        mop = URIRef(mop_iri)
        results = []
        for o in g.objects(mop, ONTOMOPS.hasChemicalBuildingUnit):
            results.append(str(o))
        return results


# ----------------------------------------------------------------------------------------------------------------------
# Delete utilities
# ----------------------------------------------------------------------------------------------------------------------

def delete_entity(entity_iri: str) -> str:
    """Remove all triples where entity_iri is subject or object."""
    with locked_graph() as g:
        s = URIRef(entity_iri)
        # Remove subject triples
        for p, o in list(g.predicate_objects(s)):
            g.remove((s, p, o))
        # Remove inbound triples
        for subj, pred in list(g.subject_predicates(s)):
            g.remove((subj, pred, s))
        return entity_iri

def delete_triple(subject_iri: str, predicate_uri: str, object_iri_or_literal: str, is_object_literal: bool = False) -> str:
    """Delete a specific triple from the graph."""
    with locked_graph() as g:
        s = URIRef(subject_iri)
        p = URIRef(predicate_uri)
        if is_object_literal:
            # Attempt to remove as string literal; if multiple typed literals exist, remove all string-equal
            for o in list(g.objects(s, p)):
                if isinstance(o, Literal) and str(o) == object_iri_or_literal:
                    g.remove((s, p, o))
        else:
            o = URIRef(object_iri_or_literal)
            g.remove((s, p, o))
        return subject_iri

# ----------------------------------------------------------------------------------------------------------------------
# Example usage
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # Initialize memory
    sh = init_memory()
    print(f"OntoMOPs Session hash: {sh}")

    # Create ChemicalBuildingUnits (minting new IRIs)
    cbu1 = create_chemical_building_unit("1,3,5-benzenetricarboxylic acid")
    cbu2 = create_chemical_building_unit("1,3,5-tris(4-pyridyl)benzene")
    
    # Create ChemicalBuildingUnit with existing IRI (entity linking)
    cbu3 = create_chemical_building_unit(
        "Copper(II) ion", 
        iri="https://www.theworldavatar.com/kg/OntoMOPs/instance/ChemicalBuildingUnit/copper-ii-ion"
    )

    # Create MetalOrganicPolyhedron (minting new IRI)
    mop1 = create_metal_organic_polyhedron(
        name="MOP-123",
        ccdc_number="1234567",
        mop_formula="C36H27N3O6"
    )
    
    # Create MetalOrganicPolyhedron with existing IRI (entity linking)
    mop2 = create_metal_organic_polyhedron(
        name="MOP-456",
        ccdc_number="1234568",
        mop_formula="C42H30N6O6",
        iri="https://www.theworldavatar.com/kg/OntoMOPs/instance/MetalOrganicPolyhedron/mop-456"
    )

    # Update properties
    update_mop_ccdc_number(mop1, "1234568")
    update_mop_formula(mop1, "C36H27N3O6·2H2O")

    # Connect ChemicalBuildingUnits to MetalOrganicPolyhedra
    add_cbu_to_mop(mop1, cbu1)
    add_cbu_to_mop(mop1, cbu2)
    add_cbu_to_mop(mop2, cbu3)

    # Query examples
    print(f"\nMOPs with CCDC number 1234568: {find_mops_by_ccdc_number('1234568')}")
    print(f"MOPs with formula C36H27N3O6·2H2O: {find_mops_by_formula('C36H27N3O6·2H2O')}")
    print(f"CBUs in MOP-123: {get_mop_cbus(mop1)}")

    # Export and inspect
    out_path = export_memory("ontomops_snapshot.ttl")
    print(f"\nExported TTL: {out_path}")
    print("\n" + inspect_memory())
