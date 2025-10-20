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
from urllib.parse import urlparse
from datetime import datetime, timezone
import hashlib

from filelock import FileLock
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL, XSD
from models.locations import DATA_DIR

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
# Global state (JSON) + Memory management & locking
# ----------------------------------------------------------------------------------------------------------------------
GLOBAL_STATE_DIR = DATA_DIR
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "ontomops_global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "ontomops_global_state.lock")

def _read_global_state() -> Tuple[str, str]:
    """Read global state: returns (doi, top_level_entity_name). Read-only, no writes."""
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        if not os.path.exists(GLOBAL_STATE_JSON):
            raise RuntimeError("Global state file not found at data/ontomops_global_state.json")
        import json
        with open(GLOBAL_STATE_JSON, "r", encoding="utf-8") as f:
            state = json.load(f)
        doi = (state.get("doi") or "").strip()
        entity = (state.get("top_level_entity_name") or "").strip()
        if not doi:
            raise RuntimeError("Global state missing 'doi'")
        if not entity:
            raise RuntimeError("Global state missing 'top_level_entity_name'")
        return doi, entity
    finally:
        lock.release()

def _hash_for_doi(doi_us: str) -> str:
    """Resolve hash directory for a given pipeline DOI (underscore form).

    Falls back to doi_us if mapping not found. Accepts an 8-char hex hash as pass-through.
    """
    try:
        # Pass-through when an 8-char hex hash is supplied already
        if isinstance(doi_us, str) and len(doi_us) == 8 and all(c in "0123456789abcdef" for c in doi_us.lower()):
            return doi_us
        import json
        mapping_path = os.path.join(DATA_DIR, "doi_to_hash.json")
        if os.path.exists(mapping_path):
            with open(mapping_path, "r", encoding="utf-8") as f:
                mapping = json.load(f) or {}
            hv = mapping.get(doi_us)
            if hv:
                return hv
        print(f"Warning: hash not found for DOI '{doi_us}', using DOI as folder name (compat mode)")
    except Exception as e:
        print(f"Warning: failed to resolve hash for DOI '{doi_us}': {e}")
    return doi_us

def get_memory_paths(doi: str, top_level_entity_name: str):
    """Get memory paths based on hash (resolved from DOI) and top-level entity name."""
    hash_value = _hash_for_doi(doi)
    mem_dir = os.path.join(DATA_DIR, hash_value, "memory_ontomops")    
    os.makedirs(mem_dir, exist_ok=True)
    return {
        'dir': mem_dir,
        'ttl': os.path.join(mem_dir, f"{top_level_entity_name}.ttl"),
        'lock': os.path.join(mem_dir, f"{top_level_entity_name}.lock")
    }

@contextmanager
def locked_graph(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None, timeout: float = 30.0):
    """Lock, load, yield, then atomically write-back the memory graph for the given DOI and entity.
    If doi/top_level_entity_name are not provided, resolve them from ontomops_global_state.json.
    """
    if not doi or not top_level_entity_name:
        doi_g, entity_g = _read_global_state()
        doi = doi or doi_g
        top_level_entity_name = top_level_entity_name or entity_g
    paths = get_memory_paths(doi, top_level_entity_name)
    lock = FileLock(paths['lock'])
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
    if os.path.exists(paths['ttl']):
        g.parse(paths['ttl'], format="turtle")
    try:
        yield g
        fd, tmp = tempfile.mkstemp(dir=paths['dir'], suffix=".ttl.tmp")
        os.close(fd)
        g.serialize(destination=tmp, format="turtle")
        os.replace(tmp, paths['ttl'])
    finally:
        lock.release()

# ----------------------------------------------------------------------------------------------------------------------
# IRI helpers
# ----------------------------------------------------------------------------------------------------------------------

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

def _mint_hash_iri(class_local: str) -> URIRef:
    """Mint IRI using SHA-1 of timestamp+class for stability and uniqueness."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    h = hashlib.sha1(f"{ts}|{class_local}".encode("utf-8")).hexdigest()
    return URIRef(f"{INST_BASE}/{class_local}/{h}")

# ----------------------------------------------------------------------------------------------------------------------
# Generic helpers: ensure individual exists with expected type and label
# ----------------------------------------------------------------------------------------------------------------------

def _is_abs_iri(s: str) -> bool:
    try:
        u = urlparse(s); return bool(u.scheme) and bool(u.netloc) and u.scheme in ("http", "https")
    except Exception:
        return False

def _safe_parent(parent_iri: str) -> Optional[URIRef]:
    """Validate absolute HTTPS IRI parents; return URIRef or None to fail fast."""
    return URIRef(parent_iri) if _is_abs_iri(parent_iri) else None

# ----------------------------------------------------------------------------------------------------------------------
# Memory API
# ----------------------------------------------------------------------------------------------------------------------

def init_memory(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:
    """Initialize or resume the memory graph. Returns a success message.
    If doi/entity are None, they are resolved from ontomops_global_state.json.
    """
    with locked_graph(doi=doi, top_level_entity_name=top_level_entity_name, timeout=30.0) as g:
        # Just ensure the graph is loaded/created. No session hash needed.
        pass
    doi_actual, entity_actual = _read_global_state() if not (doi and top_level_entity_name) else (doi, top_level_entity_name)
    return f"OntoMOPs memory initialized for DOI: {doi_actual}, entity: {entity_actual}"

def inspect_memory() -> str:
    """
    Print a summary of the entire memory graph for the specific entity.
    Since memory is now entity-specific, we can return the full graph.
    """
    lines: List[str] = []
    doi, top_level_entity_name = _read_global_state()
    with locked_graph(timeout=30.0) as g:
        lines.append("=== OntoMOPs Memory Summary (entity-specific) ===")
        lines.append(f"Entity: {top_level_entity_name}")
        lines.append(f"DOI: {doi}")
        lines.append(f"Triples: {len(g)}")

        # group by subject
        subjects = sorted({sub for sub, _, _ in g if isinstance(sub, URIRef)}, key=str)
        for sub in subjects:
            lines.append(f"\nSubject: {sub}")
            # types
            types = [str(o) for o in g.objects(sub, RDF.type)]
            if types:
                lines.append("  rdf:type:")
                for t in types:
                    lines.append(f"    - {t}")
            # label
            for lbl in g.objects(sub, RDFS.label):
                lines.append(f"  rdfs:label: {str(lbl)}")
                break
            # data properties
            for p, o in g.predicate_objects(sub):
                if p in (RDF.type, RDFS.label):
                    continue
                if isinstance(o, Literal):
                    lines.append(f"  {p} = {o}")
            # object properties
            for p, o in g.predicate_objects(sub):
                if p in (RDF.type, RDFS.label):
                    continue
                if isinstance(o, URIRef):
                    lines.append(f"  {p} -> {o}")

    return "\n".join(lines)

def export_memory() -> str:
    """Serialize the entire memory graph to a Turtle file (.ttl) and return the absolute path."""
    # resolve state
    doi, top_level_entity_name = _read_global_state()
    # Generate filename based on entity name
    filename = f"ontomops_extension_{top_level_entity_name}.ttl"
    # Save to data/<hash>/ontomops_output directory
    hash_value = _hash_for_doi(doi)
    output_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, filename)
    with locked_graph(timeout=30.0) as g:
        g.serialize(destination=file_path, format="turtle")
    return os.path.abspath(file_path)

# ----------------------------------------------------------------------------------------------------------------------
# OntoMOPs Core Classes
# ----------------------------------------------------------------------------------------------------------------------

def add_chemical_building_unit(name: str) -> str:
    """
    Create ontomops:ChemicalBuildingUnit.
    
    Args:
        name: Human-readable name for the CBU
    """
    with locked_graph() as g:
        iri_ref = _mint_hash_iri("ChemicalBuildingUnit")
        g.add((iri_ref, RDF.type, ONTOMOPS.ChemicalBuildingUnit))
        g.add((iri_ref, RDFS.label, Literal(name)))
        return str(iri_ref)

def add_metal_organic_polyhedron(name: str,
                                 ccdc_number: Optional[str] = None,
                                 mop_formula: Optional[str] = None) -> str:
    """
    Create ontomops:MetalOrganicPolyhedron.
    
    Args:
        name: Human-readable name for the MOP
        ccdc_number: CCDC number of the MOP (optional)
        mop_formula: Chemical formula of the MOP (optional)
    """
    # check ccdc_number is a valid number, "N/A" is acceptable
    if ccdc_number.upper() != "N/A":
        if not ccdc_number.isdigit():
            return "ccdc_number must be a valid number, also, 1234567-1234568 is acceptable, must be a single number or 'N/A'"




    with locked_graph() as g:
        iri_ref = _mint_hash_iri("MetalOrganicPolyhedron")
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
    """Update or set the CCDC number for a MetalOrganicPolyhedron. Ensures single value."""

    # check ccdc_number is a valid number, "N/A" is acceptable
    if ccdc_number.upper() != "N/A":
        if not ccdc_number.isdigit():
            return "ccdc_number must be a valid number, also, 1234567-1234568 is acceptable, must be a single number"

    with locked_graph() as g:
        if not _is_abs_iri(mop_iri):
            return "mop_iri must be an absolute https IRI"
        mop = URIRef(mop_iri)
        # Remove existing CCDC numbers
        for o in list(g.objects(mop, ONTOMOPS.hasCCDCNumber)):
            g.remove((mop, ONTOMOPS.hasCCDCNumber, o))
        # Add new CCDC number
        g.add((mop, ONTOMOPS.hasCCDCNumber, Literal(ccdc_number)))
        return str(mop)

def update_mop_formula(mop_iri: str, mop_formula: str) -> str:
    """Update or set the MOP formula for a MetalOrganicPolyhedron. Ensures single value."""
    with locked_graph() as g:
        if not _is_abs_iri(mop_iri):
            return "mop_iri must be an absolute https IRI"
        mop = URIRef(mop_iri)
        # Remove existing formulas
        for o in list(g.objects(mop, ONTOMOPS.hasMOPFormula)):
            g.remove((mop, ONTOMOPS.hasMOPFormula, o))
        # Add new formula
        g.add((mop, ONTOMOPS.hasMOPFormula, Literal(mop_formula)))
        return str(mop)

def update_entity_label(entity_iri: str, label: str) -> str:
    """Update or set the rdfs:label for any entity. Ensures single value."""
    with locked_graph() as g:
        if not _is_abs_iri(entity_iri):
            return "entity_iri must be an absolute https IRI"
        entity = URIRef(entity_iri)
        # Remove existing labels
        for o in list(g.objects(entity, RDFS.label)):
            g.remove((entity, RDFS.label, o))
        # Add new label
        g.add((entity, RDFS.label, Literal(label)))
        return str(entity)

# ----------------------------------------------------------------------------------------------------------------------
# Relationship creators
# ----------------------------------------------------------------------------------------------------------------------

def add_cbu_to_mop(mop_iri: str, cbu_iri: str) -> str:
    """Add a ChemicalBuildingUnit to a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        if not _is_abs_iri(mop_iri) or not _is_abs_iri(cbu_iri):
            return "mop_iri and cbu_iri must be absolute https IRIs"
        mop = URIRef(mop_iri)
        cbu = URIRef(cbu_iri)
        g.add((mop, ONTOMOPS.hasChemicalBuildingUnit, cbu))
        return str(mop)

def remove_cbu_from_mop(mop_iri: str, cbu_iri: str) -> str:
    """Remove a ChemicalBuildingUnit from a MetalOrganicPolyhedron."""
    with locked_graph() as g:
        if not _is_abs_iri(mop_iri) or not _is_abs_iri(cbu_iri):
            return "mop_iri and cbu_iri must be absolute https IRIs"
        mop = URIRef(mop_iri)
        cbu = URIRef(cbu_iri)
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
        if not _is_abs_iri(entity_iri):
            return "entity_iri must be an absolute https IRI"
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
        if not _is_abs_iri(subject_iri) or not _is_abs_iri(predicate_uri):
            return "subject_iri and predicate_uri must be absolute https IRIs"
        s = URIRef(subject_iri)
        p = URIRef(predicate_uri)
        if is_object_literal:
            # Attempt to remove as string literal; if multiple typed literals exist, remove all string-equal
            for o in list(g.objects(s, p)):
                if isinstance(o, Literal) and str(o) == object_iri_or_literal:
                    g.remove((s, p, o))
        else:
            if not _is_abs_iri(object_iri_or_literal):
                return "object_iri_or_literal must be an absolute https IRI when is_object_literal is False"
            o = URIRef(object_iri_or_literal)
            g.remove((s, p, o))
        return subject_iri

# ----------------------------------------------------------------------------------------------------------------------
# Example usage
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # Initialize memory using global state (reads from ontomops_global_state.json)
    result = init_memory()
    print(f"OntoMOPs Memory: {result}")

    # Add ChemicalBuildingUnits (minting new IRIs)
    cbu1 = add_chemical_building_unit("1,3,5-benzenetricarboxylic acid")
    cbu2 = add_chemical_building_unit("1,3,5-tris(4-pyridyl)benzene")
    cbu3 = add_chemical_building_unit("Copper(II) ion")

    # Add MetalOrganicPolyhedra (minting new IRIs)
    mop1 = add_metal_organic_polyhedron(
        name="MOP-123",
        ccdc_number="1234567",
        mop_formula="C36H27N3O6"
    )
    
    mop2 = add_metal_organic_polyhedron(
        name="MOP-456",
        ccdc_number="1234568",
        mop_formula="C42H30N6O6"
    )

    mop3 = add_metal_organic_polyhedron(
        name="MOP-789",
        ccdc_number="N/A",
        mop_formula="C36H27N3O6"
    )

    mop4 = add_metal_organic_polyhedron(
        name="MOP-1011",
        ccdc_number="1234567-1234568",
        mop_formula="C36H27N3O6"
    )

    # Update properties
    update_mop_ccdc_number(mop1, "1234568")
    update_mop_formula(mop1, "C36H27N3O6·2H2O")
    update_entity_label(cbu1, "1,3,5-Benzenetricarboxylic acid (updated)")

    # Connect ChemicalBuildingUnits to MetalOrganicPolyhedra
    add_cbu_to_mop(mop1, cbu1)
    add_cbu_to_mop(mop1, cbu2)
    add_cbu_to_mop(mop2, cbu3)

    # Query examples
    print(f"\nMOPs with CCDC number 1234568: {find_mops_by_ccdc_number('1234568')}")
    print(f"MOPs with formula C36H27N3O6·2H2O: {find_mops_by_formula('C36H27N3O6·2H2O')}")
    print(f"CBUs in MOP-123: {get_mop_cbus(mop1)}")

    # Export and inspect
    out_path = export_memory()
    print(f"\nExported TTL: {out_path}")
    print("\n" + inspect_memory())

    print(f"mop3: {mop3}")
    print(f"mop4: {mop4}")
