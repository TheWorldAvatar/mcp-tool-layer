# ontospecies_extension_v3_singlecall.py
# OntoSpecies A-Box utilities — single-call creation, hardcoded invariants, predictable behavior
# - One-shot creators accept required literal values
# - IR bands stored ONLY as one literal string on the IR data node (dc:description)
# - CCDC number requires a value at creation; empty inputs become "N/A"
# - Deterministic linking: replace existing links when semantic cardinality is 1
# - File-locked, atomic writes; idempotent by label+class

import os
import re
import uuid
import tempfile
import unicodedata
from contextlib import contextmanager
from typing import Optional, List, Tuple
from filelock import FileLock
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL, XSD
from models.locations import DATA_DIR
from urllib.parse import quote
# -------------------------------------------------------------------------------------------------
# Namespaces
# -------------------------------------------------------------------------------------------------
KG = Namespace("https://www.theworldavatar.com/kg/")
ONTOSPECIES = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")
PERIODIC = Namespace("http://www.daml.org/2003/01/periodictable/PeriodicTable#")
DC = Namespace("http://purl.org/dc/elements/1.1/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")

# -------------------------------------------------------------------------------------------------
# Memory + locking
# -------------------------------------------------------------------------------------------------
def get_memory_paths(hash_value: str):
    """Get memory paths for a specific hash."""
    mem_dir = os.path.join(DATA_DIR, hash_value, "memory_ontospecies")
    mem_ttl = os.path.join(mem_dir, "memory_ontospecies.ttl")
    mem_lock = os.path.join(mem_dir, "memory_ontospecies.lock")
 
    os.makedirs(mem_dir, exist_ok=True)
    return mem_dir, mem_ttl, mem_lock

 
SESSION_NODE = URIRef("https://www.theworldavatar.com/kg/OntoSpecies/instance/Session")

@contextmanager
def locked_graph(hash_value: str, timeout: float = 30.0):
    """Exclusive-lock graph context. Atomic write via temp file."""
    mem_dir, mem_ttl, mem_lock = get_memory_paths(hash_value)
    
    lock = FileLock(mem_lock)
    lock.acquire(timeout=timeout)
    g = Graph()
    g.bind("kg", KG); g.bind("ontospecies", ONTOSPECIES); g.bind("periodic", PERIODIC)
    g.bind("dc", DC); g.bind("owl", OWL); g.bind("rdf", RDF); g.bind("rdfs", RDFS); g.bind("xsd", XSD)
    if os.path.exists(mem_ttl):
        g.parse(mem_ttl, format="turtle")
    try:
        yield g
        fd, tmp = tempfile.mkstemp(dir=mem_dir, suffix=".ttl.tmp"); os.close(fd)
        g.serialize(destination=tmp, format="turtle"); os.replace(tmp, mem_ttl)
    finally:
        lock.release()

# -------------------------------------------------------------------------------------------------
# Session helpers
# -------------------------------------------------------------------------------------------------
def _get_or_create_session_hash(g: Graph) -> str:
    for _, _, h in g.triples((SESSION_NODE, DC.identifier, None)):
        if isinstance(h, Literal): return str(h)
    sh = uuid.uuid4().hex[:8]
    g.add((SESSION_NODE, RDF.type, OWL.NamedIndividual))
    g.add((SESSION_NODE, RDFS.label, Literal(f"OntoSpecies Session {sh}")))
    g.add((SESSION_NODE, DC.identifier, Literal(sh)))
    return sh

def init_memory(hash_value: str) -> str:
    """Initialize persistent graph and return session hash."""
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        return _get_or_create_session_hash(g)

def inspect_memory(hash_value: str) -> str:
    """Readable dump of subjects and key properties (agent-friendly)."""
    lines: List[str] = ["=== OntoSpecies Memory Summary ==="]
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        subs = sorted({s for s, _, _ in g if isinstance(s, URIRef)}, key=lambda u: str(u))
        for s in subs:
            lines.append(f"\nSubject: {s}")
            for o in g.objects(s, RDF.type): lines.append(f"  rdf:type: {o}")
            for o in g.objects(s, RDFS.label): lines.append(f"  rdfs:label: {o}")
            for p, o in g.predicate_objects(s):
                if p in (RDF.type, RDFS.label): continue
                if isinstance(o, Literal):
                    lines.append(f"  {g.namespace_manager.normalizeUri(p)}: {o} ({o.datatype or 'xsd:string'})")
                else:
                    lines.append(f"  {g.namespace_manager.normalizeUri(p)} -> {o}")
    return "\n".join(lines)

def export_memory(hash_value: str) -> str:
    """Serialize the entire memory graph to a Turtle file and return the absolute path."""
    # Hardcoded output filename
    file_name = "ontospecies_extension.ttl"
    
    # If hash_value is provided, save to data/{hash}/ directory
    if hash_value:
        output_path = os.path.join(DATA_DIR, hash_value, file_name)
    else:
        output_path = file_name
    
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        g.serialize(destination=output_path, format="turtle")
    return os.path.abspath(output_path)

# -------------------------------------------------------------------------------------------------
# Core engine
# -------------------------------------------------------------------------------------------------
INST_BASE = "https://www.theworldavatar.com/kg/OntoSpecies/instance"

def _slugify(text: str, *, maxlen: int = 120) -> str:
    # Normalize and lowercase
    s = unicodedata.normalize("NFKC", str(text)).strip().casefold()
    # Whitespace -> hyphen
    s = re.sub(r"\s+", "-", s)
    # Keep word chars plus -_.~ ; replace everything else with hyphen
    s = re.sub(r"[^\w\-\.~]", "-", s)
    # Collapse runs of - or _
    s = re.sub(r"[-_]{2,}", "-", s)
    # Trim leading/trailing separators
    s = s.strip("-_.")
    # Fallback if empty
    if not s:
        return "entity"
    # Length cap, keep clean ending
    s = s[:maxlen].rstrip("-_.")
    # Percent-encode non-ASCII to keep IRIs ASCII-safe
    return quote(s, safe="abcdefghijklmnopqrstuvwxyz0123456789-_.~")

def _iri_exists(g: Graph, iri: URIRef) -> bool:
    return (iri, None, None) in g or (None, None, iri) in g

def _resolve_class_uri(class_local: str) -> Optional[URIRef]:
    for ns in (ONTOSPECIES, PERIODIC):
        uri = getattr(ns, class_local, None)
        if isinstance(uri, URIRef): return uri
    return None

def _find_by_type_and_label(g: Graph, class_uri: URIRef, label: str) -> Optional[URIRef]:
    lbl = Literal(label)
    for s in g.subjects(RDF.type, class_uri):
        if (s, RDFS.label, lbl) in g: return s
    return None

def _mint_iri(g: Graph, class_local: str, label: str) -> URIRef:
    class_uri = _resolve_class_uri(class_local)
    if class_uri is not None:
        existing = _find_by_type_and_label(g, class_uri, label)
        if existing is not None: return existing
    slug = _slugify(label)
    iri = URIRef(f"{INST_BASE}/{class_local}/{slug}")
    return iri if not _iri_exists(g, iri) else iri

def _ensure_individual(g: Graph, iri: URIRef, class_uri: URIRef, label: Optional[str] = None) -> URIRef:
    if (iri, RDF.type, None) not in g: g.add((iri, RDF.type, class_uri))
    if (iri, RDF.type, class_uri) not in g: g.add((iri, RDF.type, class_uri))
    if (iri, RDFS.label, None) not in g and label is not None: g.add((iri, RDFS.label, Literal(label)))
    return iri

# --- literals -----------------------------------------------------------------------------------
def _set_literal_ns(subject_iri: str, predicate_ns: Namespace, predicate_local: str, value: str,
                    dtype: Optional[URIRef] = None, hash_value: str = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        s = URIRef(subject_iri)
        pred = getattr(predicate_ns, predicate_local, None)
        if pred is None: raise ValueError(f"Unknown data property: {predicate_ns}{predicate_local}")
        for o in list(g.objects(s, pred)):
            if isinstance(o, Literal): g.remove((s, pred, o))
        g.add((s, pred, Literal(value, datatype=dtype)))
        return subject_iri

# --- links --------------------------------------------------------------------------------------
def _link(subject_iri: str, predicate_local: str, object_iri: str, hash_value: str, expect: Optional[Tuple[str, str]] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        s, o = URIRef(subject_iri), URIRef(object_iri)
        pred = getattr(ONTOSPECIES, predicate_local, None)
        if pred is None: raise ValueError(f"Unknown object property: {predicate_local}")
        if expect:
            scls = _resolve_class_uri(expect[0]); ocls = _resolve_class_uri(expect[1])
            if scls is not None: g.add((s, RDF.type, scls))
            if ocls is not None: g.add((o, RDF.type, ocls))
        g.add((s, pred, o))
        return subject_iri

def _replace_links(subject_iri: str, predicate_local: str, object_iri: str, hash_value: str) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        s = URIRef(subject_iri)
        pred = getattr(ONTOSPECIES, predicate_local, None)
        for o in list(g.objects(s, pred)):
            g.remove((s, pred, o))
        g.add((s, pred, URIRef(object_iri)))
        return subject_iri

# -------------------------------------------------------------------------------------------------
# Public API — single-call creators (preferred)
# -------------------------------------------------------------------------------------------------
# Core entities
def create_species(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    """Create or fetch Species by label."""
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        class_uri = _resolve_class_uri("Species")
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "Species", name)
        _ensure_individual(g, iri_ref, class_uri, name)
        return str(iri_ref)

def create_characterization_session(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        class_uri = _resolve_class_uri("CharacterizationSession")
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "CharacterizationSession", name)
        _ensure_individual(g, iri_ref, class_uri, name)
        return str(iri_ref)

# Devices
def create_hnmr_device(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "HNMRDevice", name)
        _ensure_individual(g, iri_ref, _resolve_class_uri("HNMRDevice"), name)
        return str(iri_ref)

def create_elemental_analysis_device(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "ElementalAnalysisDevice", name)
        _ensure_individual(g, iri_ref, _resolve_class_uri("ElementalAnalysisDevice"), name)
        return str(iri_ref)

def create_infrared_spectroscopy_device(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "InfraredSpectroscopyDevice", name)
        _ensure_individual(g, iri_ref, _resolve_class_uri("InfraredSpectroscopyDevice"), name)
        return str(iri_ref)

# Data containers — SINGLE-CALL variants that also set literals
def create_hnmr_data(label: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "HNMRData", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("HNMRData"), label)
        return str(iri_ref)

def create_elemental_analysis_data(label: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "ElementalAnalysisData", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("ElementalAnalysisData"), label)
        return str(iri_ref)

def create_infrared_spectroscopy_data_with_bands(label: str, bands_text: Optional[str], hash_value: str, iri: Optional[str] = None) -> str:
    """Create IR data and store bands as ONE literal (dc:description). Any existing hasInfraredBand links are removed."""
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "InfraredSpectroscopyData", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("InfraredSpectroscopyData"), label)
    _set_ir_bands_text(str(iri_ref), bands_text, hash_value)
    return str(iri_ref)

# Parts — SINGLE-CALL creators with embedded value semantics
def create_ccdc_number(value: Optional[str], hash_value: str, label_prefix: str = "CCDC") -> str:
    """Create CCDCNumber with required value literal at creation.
    - value: the accession string; falsy -> "N/A"
    - label is f"{label_prefix} {value_or_NA}"
    - stores dc:identifier = value_or_NA
    """
    val = (value or "").strip() or "N/A"
    label = f"{label_prefix} {val}"
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = _mint_iri(g, "CCDCNumber", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("CCDCNumber"), label)
    _set_literal_ns(str(iri_ref), DC, "identifier", val, dtype=XSD.string, hash_value=hash_value)
    return str(iri_ref)

def create_molecular_formula(value: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        label = value
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "MolecularFormula", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("MolecularFormula"), label)
        return str(iri_ref)

def create_chemical_formula(value: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        label = value
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "ChemicalFormula", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("ChemicalFormula"), label)
        return str(iri_ref)

def create_weight_percentage(text: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        label = text
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "WeightPercentage", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("WeightPercentage"), label)
        return str(iri_ref)

def create_element(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "Element", name)
        _ensure_individual(g, iri_ref, _resolve_class_uri("Element"), name)
        return str(iri_ref)

def create_atomic_weight(value: str, hash_value: str, iri: Optional[str] = None) -> str:
    label = f"Atomic Weight {value}"
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "AtomicWeight", label)
        _ensure_individual(g, iri_ref, _resolve_class_uri("AtomicWeight"), label)
        return str(iri_ref)

def create_material(name: str, hash_value: str, iri: Optional[str] = None) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        iri_ref = URIRef(iri) if iri else _mint_iri(g, "Material", name)
        _ensure_individual(g, iri_ref, _resolve_class_uri("Material"), name)
        return str(iri_ref)

# -------------------------------------------------------------------------------------------------
# High-level single-call blocks (predictable behavior)
# -------------------------------------------------------------------------------------------------
def create_and_link_ccdc_number(species_iri: str, value: Optional[str], hash_value: str) -> str:
    """One call: create CCDC with value and replace existing Species->hasCCDCNumber links."""
    ccdc_iri = create_ccdc_number(value, hash_value)
    _replace_links(species_iri, "hasCCDCNumber", ccdc_iri, hash_value)
    return ccdc_iri

def create_and_link_ir_data(
    species_iri: str,
    ir_data_label: str,
    bands_text: Optional[str],
    hash_value: str,
    material_label: Optional[str] = None,
    device_label: Optional[str] = None,
    session_label: Optional[str] = None,
) -> str:
    """One call: create IR data with bands literal, replace existing band links, attach optional material/device/session."""
    ir_data_iri = create_infrared_spectroscopy_data_with_bands(ir_data_label, bands_text, hash_value)
    _replace_links(species_iri, "hasInfraredSpectroscopyData", ir_data_iri, hash_value)  # deterministic latest-wins

    if material_label:
        mat = create_material(material_label, hash_value)
        _link(ir_data_iri, "hasMaterial", mat, hash_value, expect=("InfraredSpectroscopyData","Material"))

    if device_label and session_label:
        dev = create_infrared_spectroscopy_device(device_label, hash_value)
        ses = create_characterization_session(session_label, hash_value)
        _link(ses, "hasInfraredSpectroscopyDevice", dev, hash_value, expect=("CharacterizationSession","InfraredSpectroscopyDevice"))
        _link(species_iri, "hasCharacterizationSession", ses, hash_value, expect=("Species","CharacterizationSession"))

    return ir_data_iri

# -------------------------------------------------------------------------------------------------
# Backward-compat links kept (thin wrappers)
# -------------------------------------------------------------------------------------------------
def add_characterization_session_to_species(species_iri: str, characterization_session_iri: str, hash_value: str) -> str:
    return _link(species_iri, "hasCharacterizationSession", characterization_session_iri, hash_value, expect=("Species","CharacterizationSession"))

def add_hnmr_data_to_species(species_iri: str, hnmr_data_iri: str, hash_value: str) -> str:
    return _link(species_iri, "hasHNMRData", hnmr_data_iri, hash_value, expect=("Species","HNMRData"))

def add_elemental_analysis_data_to_species(species_iri: str, elemental_data_iri: str, hash_value: str) -> str:
    return _link(species_iri, "hasElementalAnalysisData", elemental_data_iri, hash_value, expect=("Species","ElementalAnalysisData"))

def add_molecular_formula_to_species(species_iri: str, molecular_formula_iri: str, hash_value: str) -> str:
    return _link(species_iri, "hasMolecularFormula", molecular_formula_iri, hash_value, expect=("Species","MolecularFormula"))

def add_chemical_formula_to_species(species_iri: str, chemical_formula_iri: str, hash_value: str) -> str:
    return _link(species_iri, "hasChemicalFormula", chemical_formula_iri, hash_value, expect=("Species","ChemicalFormula"))

def add_ccdc_number_to_species(species_iri: str, ccdc_number_iri: str, hash_value: str) -> str:
    return _replace_links(species_iri, "hasCCDCNumber", ccdc_number_iri, hash_value)

def add_hnmr_device_to_characterization_session(characterization_session_iri: str, hnmr_device_iri: str, hash_value: str) -> str:
    return _link(characterization_session_iri, "hasHNMRDevice", hnmr_device_iri, hash_value, expect=("CharacterizationSession","HNMRDevice"))

def add_elemental_analysis_device_to_characterization_session(characterization_session_iri: str, elemental_device_iri: str, hash_value: str) -> str:
    return _link(characterization_session_iri, "hasElementalAnalysisDevice", elemental_device_iri, hash_value, expect=("CharacterizationSession","ElementalAnalysisDevice"))

def add_infrared_spectroscopy_device_to_characterization_session(characterization_session_iri: str, infrared_device_iri: str, hash_value: str) -> str:
    return _link(characterization_session_iri, "hasInfraredSpectroscopyDevice", infrared_device_iri, hash_value, expect=("CharacterizationSession","InfraredSpectroscopyDevice"))

def add_atomic_weight_to_element(element_iri: str, atomic_weight_iri: str, hash_value: str) -> str:
    return _link(element_iri, "hasAtomicWeight", atomic_weight_iri, hash_value, expect=("Element","AtomicWeight"))

def add_element_to_weight_percentage(weight_percentage_iri: str, element_iri: str, hash_value: str) -> str:
    return _link(weight_percentage_iri, "hasElement", element_iri, hash_value, expect=("WeightPercentage","Element"))

# -------------------------------------------------------------------------------------------------
# IR bands helpers (hardcoded invariant)
# -------------------------------------------------------------------------------------------------
def _set_ir_bands_text(ir_data_iri: str, bands_text: Optional[str], hash_value: str) -> str:
    # remove any InfraredBand links and store single literal under dc:description
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        s = URIRef(ir_data_iri)
        pred = getattr(ONTOSPECIES, "hasInfraredBand")
        for o in list(g.objects(s, pred)):
            g.remove((s, pred, o))
    if bands_text is None or not bands_text.strip():
        bands_text = "N/A"
    return _set_literal_ns(ir_data_iri, DC, "description", bands_text.strip(), dtype=XSD.string, hash_value=hash_value)

# -------------------------------------------------------------------------------------------------
# Deletes
# -------------------------------------------------------------------------------------------------
def delete_triple(subject_iri: str, predicate_uri: str, object_iri_or_literal: str, hash_value: str, is_object_literal: bool = False) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        s = URIRef(subject_iri); p = URIRef(predicate_uri)
        if is_object_literal:
            for o in list(g.objects(s, p)):
                if isinstance(o, Literal) and str(o) == object_iri_or_literal:
                    g.remove((s, p, o))
        else:
            o = URIRef(object_iri_or_literal); g.remove((s, p, o))
        return subject_iri

def delete_entity(entity_iri: str, hash_value: str) -> str:
    with locked_graph(hash_value=hash_value, timeout=30.0) as g:
        s = URIRef(entity_iri)
        for p, o in list(g.predicate_objects(s)):
            g.remove((s, p, o))
        for subj, pred in list(g.subject_predicates(s)):
            g.remove((subj, pred, s))
        return entity_iri

# -------------------------------------------------------------------------------------------------
# Minimal demo
# -------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    hash_value = "9f13ab77"
    sh = init_memory(hash_value=hash_value)
    print(f"OntoSpecies Session hash: {sh}")

    sp = create_species("VMOPa", hash_value=hash_value)

    # One-call CCDC creation+linking with required value (empty -> "N/A")
    create_and_link_ccdc_number(sp, "2345678", hash_value=hash_value)

    # One-call IR creation+linking with single-string bands
    create_and_link_ir_data(
        species_iri=sp,
        ir_data_label="VMOPa IR",
        bands_text="498 w; 579 w; 650 m; 782 m; 867 w; 947 s; 1038 m; 1228 w; 1420 vs; 1549 m; 1595 vs; 2816 w; 2924 w; 3423 w",
        material_label="KBr pellet",
        device_label="FT-IR Spectrometer",
        session_label="Characterization-of-VMOPa",
        hash_value=hash_value
    )

    print(export_memory(hash_value=hash_value))
    print(inspect_memory(hash_value=hash_value))
