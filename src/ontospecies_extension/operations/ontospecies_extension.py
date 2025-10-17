#!/usr/bin/env python3
# ontospecies_extension.py — creation-time hard typing for every node

import os, tempfile, hashlib, re
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, List, Tuple
from urllib.parse import urlparse

from filelock import FileLock
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, XSD

# ========= Namespaces =========
OS  = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")
PER = Namespace("http://www.daml.org/2003/01/periodictable/PeriodicTable#")

INST_BASE = "https://www.theworldavatar.com/kg/OntoSpecies/instance"

# ========= Storage =========
DATA_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(DATA_DIR, "ontospecies_global_state.json")
GLOBAL_STATE_LOCK = os.path.join(DATA_DIR, "ontospecies_global_state.lock")

# ========= Helpers =========
def _is_abs_iri(s: str) -> bool:
    try:
        u = urlparse(s); return bool(u.scheme) and bool(u.netloc)
    except Exception:
        return False

def _class(ns: Namespace, local: str) -> URIRef:
    return getattr(ns, local)

def _mint_hash_iri(class_local: str) -> URIRef:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    h = hashlib.sha1(f"{ts}|{class_local}".encode("utf-8")).hexdigest()
    return URIRef(f"{INST_BASE}/{class_local}/{h}")

def _read_global_state():
    if not os.path.exists(GLOBAL_STATE_JSON):
        return "default-doi", "default-entity"
    from json import load
    with FileLock(GLOBAL_STATE_LOCK):
        with open(GLOBAL_STATE_JSON, "r", encoding="utf-8") as f:
            st = load(f)
    doi = (st.get("doi") or "default-doi").strip()
    ent = (st.get("top_level_entity_name") or "default-entity").strip()
    return doi, ent

def _slugify(text: str, maxlen: int = 120) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", str(text)).strip()
    s = re.sub(r"\s+", "-", s); s = re.sub(r"[^\w\-.~]", "-", s)
    s = re.sub(r"[-_]{2,}", "-", s).strip("-_.")
    s = s[:maxlen].rstrip("-_.") or "entity"
    from urllib.parse import quote
    return quote(s, safe="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")

def _memory_paths():
    doi, ent = _read_global_state()
    mem_dir = os.path.join(DATA_DIR, doi, "memory_ontospecies")
    os.makedirs(mem_dir, exist_ok=True)
    return {
        "ttl":  os.path.join(mem_dir, f"{_slugify(ent)}.ttl"),
        "lock": os.path.join(mem_dir, f"{_slugify(ent)}.lock"),
        "dir":  mem_dir,
    }

@contextmanager
def locked_graph(timeout: float = 30.0):
    paths = _memory_paths()
    lock = FileLock(paths["lock"])
    lock.acquire(timeout=timeout)
    g = Graph()
    # Bind needed prefixes (rdf first so Turtle prints 'a')
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)
    g.bind("xsd", XSD)
    g.bind("ontospecies", OS)
    g.bind("periodic", PER)
    if os.path.exists(paths["ttl"]):
        g.parse(paths["ttl"], format="turtle")
    try:
        yield g
        fd, tmp = tempfile.mkstemp(dir=paths["dir"], suffix=".ttl.tmp"); os.close(fd)
        g.serialize(destination=tmp, format="turtle")
        os.replace(tmp, paths["ttl"])
    finally:
        lock.release()

def _ensure_type_with_label(g: Graph, iri: URIRef, cls: URIRef, label: Optional[str] = None) -> None:
    g.add((iri, RDF.type, cls))
    if label is not None:
        g.set((iri, RDFS.label, Literal(label)))

def _safe_parent(parent_iri: str) -> Optional[URIRef]:
    return URIRef(parent_iri) if _is_abs_iri(parent_iri) else None

# Empirical formula validator (compact elemental only)
_EMP_RE = re.compile(r"^(?:[A-Z][a-z]?\d*)+$")
def _is_empirical(s: str) -> bool:
    return bool(_EMP_RE.fullmatch((s or "").strip()))

# ========= Top-level creators =========
def create_species(label: str, product_name: Optional[str] = None) -> str:
    with locked_graph() as g:
        s = _mint_hash_iri("Species")
        _ensure_type_with_label(g, s, _class(OS, "Species"), label)
        if product_name:
            g.set((s, _class(OS, "hasProductName"), Literal(product_name)))
        return str(s)

def create_element(label: str, symbol: Optional[str] = None) -> str:
    with locked_graph() as g:
        e = _mint_hash_iri("Element")
        _ensure_type_with_label(g, e, _class(PER, "Element"), label)
        g.set((e, _class(OS, "hasElementName"), Literal(label)))
        if symbol:
            g.set((e, _class(OS, "hasElementSymbol"), Literal(symbol)))
        return str(e)

# ========= Link helpers (always type the NEW child) =========
def _mint_and_link_child(
    parent_iri: str,
    predicate_local: str,            # object property on OS
    child_class_ns: Namespace,
    child_class_local: str,
    child_label: Optional[str] = None,
    literals: Optional[List[Tuple[Namespace, str, Literal]]] = None,
    replace: bool = False,
) -> str:
    with locked_graph() as g:
        parent = _safe_parent(parent_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"
        child = _mint_hash_iri(child_class_local)
        _ensure_type_with_label(g, child, _class(child_class_ns, child_class_local), child_label)
        if literals:
            for ns, pred_local, lit in literals:
                g.set((child, _class(ns, pred_local), lit))
        g.add((parent, _class(OS, predicate_local), child))
        if replace:
            # optional replace semantics if needed by caller (not used by default)
            pass
        return str(child)

# ========= Characterization session + devices =========
def add_characterization_session_to_species(species_iri: str, session_label: str) -> str:
    return _mint_and_link_child(
        species_iri, "hasCharacterizationSession", OS, "CharacterizationSession", session_label
    )

def add_hnmr_device_to_characterization_session(session_iri: str, device_name: str, frequency: Optional[str] = None) -> str:
    lits = [ (OS, "hasDeviceName", Literal(device_name)) ]
    if frequency:
        lits.append((OS, "hasFrequency", Literal(frequency)))
    return _mint_and_link_child(
        session_iri, "hasHNMRDevice", OS, "HNMRDevice", device_name, lits
    )

def add_elemental_analysis_device_to_characterization_session(session_iri: str, device_name: str) -> str:
    lits = [ (OS, "hasDeviceName", Literal(device_name)) ]
    return _mint_and_link_child(
        session_iri, "hasElementalAnalysisDevice", OS, "ElementalAnalysisDevice", device_name, lits
    )

def add_infrared_spectroscopy_device_to_characterization_session(session_iri: str, device_name: str) -> str:
    lits = [ (OS, "hasDeviceName", Literal(device_name)) ]
    return _mint_and_link_child(
        session_iri, "hasInfraredSpectroscopyDevice", OS, "InfraredSpectroscopyDevice", device_name, lits
    )

# ========= HNMR data =========
def add_hnmr_data_to_species(
    species_iri: str,
    data_label: str,
    shifts: Optional[str] = None,
    temperature: Optional[str] = None,
    solvent_name: Optional[str] = None,
) -> str:
    with locked_graph() as g:
        parent = _safe_parent(species_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"

        # HNMRData
        hn = _mint_hash_iri("HNMRData")
        _ensure_type_with_label(g, hn, _class(OS, "HNMRData"), data_label)
        if shifts is not None:
            g.set((hn, _class(OS, "hasShifts"), Literal(shifts)))
        if temperature is not None:
            g.set((hn, _class(OS, "hasTemperature"), Literal(temperature)))

        # Optional Solvent node (object) + name literal
        if solvent_name:
            sv = _mint_hash_iri("Solvent")
            _ensure_type_with_label(g, sv, _class(OS, "Solvent"), solvent_name)
            g.set((sv, _class(OS, "hasSolventName"), Literal(solvent_name)))
            g.add((hn, _class(OS, "usesSolvent"), sv))

        g.add((parent, _class(OS, "hasHNMRData"), hn))
        return str(hn)

def add_chemical_shift_to_hnmrdata(hnmr_data_iri: str, shift_label: str) -> str:
    # optional granular NMR peaks
    return _mint_and_link_child(
        hnmr_data_iri, "hasChemicalShift", OS, "ChemicalShift", shift_label
    )

# ========= Elemental analysis data =========
def add_elemental_analysis_data_to_species(
    species_iri: str,
    data_label: str,
    calculated_value_text: Optional[str] = None,     # e.g., "C 40.09; H 5.43; N 8.05"
    experimental_value_text: Optional[str] = None,   # e.g., "C 39.86; H 5.48; N 8.22"
    empirical_molecular_formula: Optional[str] = None,  # enforce empirical
) -> str:
    with locked_graph() as g:
        parent = _safe_parent(species_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"

        ea = _mint_hash_iri("ElementalAnalysisData")
        _ensure_type_with_label(g, ea, _class(OS, "ElementalAnalysisData"), data_label)

        # Calculated WeightPercentage node
        if calculated_value_text is not None:
            wp_c = _mint_hash_iri("WeightPercentage")
            _ensure_type_with_label(g, wp_c, _class(OS, "WeightPercentage"), "Calculated")
            g.set((wp_c, _class(OS, "hasWeightPercentageCalculatedValue"), Literal(calculated_value_text)))
            g.add((ea, _class(OS, "hasWeightPercentageCalculated"), wp_c))

        # Experimental WeightPercentage node
        if experimental_value_text is not None:
            wp_e = _mint_hash_iri("WeightPercentage")
            _ensure_type_with_label(g, wp_e, _class(OS, "WeightPercentage"), "Experimental")
            g.set((wp_e, _class(OS, "hasWeightPercentageExperimentalValue"), Literal(experimental_value_text)))
            g.add((ea, _class(OS, "hasWeightPercentageExperimental"), wp_e))

        # Empirical molecular formula node (MolecularFormula) if provided
        if empirical_molecular_formula is not None:
            if not _is_empirical(empirical_molecular_formula):
                return "empirical_molecular_formula must be compact elemental, e.g., 'C230H308N34O103Fe12S12'"
            mf = _mint_hash_iri("MolecularFormula")
            _ensure_type_with_label(g, mf, _class(OS, "MolecularFormula"), empirical_molecular_formula)
            g.set((mf, _class(OS, "hasMolecularFormulaValue"), Literal(empirical_molecular_formula)))
            g.add((parent, _class(OS, "hasMolecularFormula"), mf))

        g.add((parent, _class(OS, "hasElementalAnalysisData"), ea))
        return str(ea)

# ========= IR data =========
def add_infrared_spectroscopy_data_to_species(
    species_iri: str,
    data_label: str,
    bands_text: Optional[str] = None,
    material_name: Optional[str] = None,
) -> str:
    with locked_graph() as g:
        parent = _safe_parent(species_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"

        ir = _mint_hash_iri("InfraredSpectroscopyData")
        _ensure_type_with_label(g, ir, _class(OS, "InfraredSpectroscopyData"), data_label)
        if bands_text is not None:
            g.set((ir, _class(OS, "hasBands"), Literal(bands_text)))

        if material_name:
            m = _mint_hash_iri("Material")
            _ensure_type_with_label(g, m, _class(OS, "Material"), material_name)
            g.set((m, _class(OS, "hasMaterialName"), Literal(material_name)))
            g.add((ir, _class(OS, "usesMaterial"), m))

        g.add((parent, _class(OS, "hasInfraredSpectroscopyData"), ir))
        return str(ir)

def add_infrared_band_to_irdata(ir_data_iri: str, band_label: str) -> str:
    return _mint_and_link_child(
        ir_data_iri, "hasInfraredBand", OS, "InfraredBand", band_label
    )

# ========= Chemical / molecular formulae =========
def add_molecular_formula_to_species(species_iri: str, empirical_formula: str, label: Optional[str] = None) -> str:
    if not _is_empirical(empirical_formula):
        return "empirical_formula must be compact elemental, e.g., 'C108H84N12O88S12'"
    with locked_graph() as g:
        parent = _safe_parent(species_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"
        mf = _mint_hash_iri("MolecularFormula")
        _ensure_type_with_label(g, mf, _class(OS, "MolecularFormula"), label or empirical_formula)
        g.set((mf, _class(OS, "hasMolecularFormulaValue"), Literal(empirical_formula)))
        g.add((parent, _class(OS, "hasMolecularFormula"), mf))
        return str(mf)

def add_chemical_formula_to_species(species_iri: str, formula_text: str, label: Optional[str] = None) -> str:
    # free-form structural/extended formula
    with locked_graph() as g:
        parent = _safe_parent(species_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"
        cf = _mint_hash_iri("ChemicalFormula")
        _ensure_type_with_label(g, cf, _class(OS, "ChemicalFormula"), label or formula_text)
        g.set((cf, _class(OS, "hasChemicalFormulaValue"), Literal(formula_text)))
        g.add((parent, _class(OS, "hasChemicalFormula"), cf))
        return str(cf)

# ========= CCDC =========
def add_ccdc_number_to_species(species_iri: str, ccdc_value: str) -> str:
    with locked_graph() as g:
        parent = _safe_parent(species_iri)
        if parent is None:
            return "parent IRI must be absolute https IRI"
        c = _mint_hash_iri("CCDCNumber")
        _ensure_type_with_label(g, c, _class(OS, "CCDCNumber"), f"CCDC {ccdc_value}" if ccdc_value else "CCDC N/A")
        g.set((c, _class(OS, "hasCCDCNumberValue"), Literal(ccdc_value or "N/A")))
        g.add((parent, _class(OS, "hasCCDCNumber"), c))
        return str(c)

# ========= Elements / atomic data =========
def add_atomic_weight_to_element(element_iri: str, value) -> str:
    """
    Attach atomic weight to an element node. Handles both float and "N/A" values.
    """
    with locked_graph() as g:
        parent = _safe_parent(element_iri)
        if parent is None:
            return "element IRI must be absolute https IRI"
        aw = _mint_hash_iri("AtomicWeight")
        # Compose label
        label = f"Atomic Weight {value if value != 'N/A' else 'N/A'}"
        _ensure_type_with_label(g, aw, _class(OS, "AtomicWeight"), label)
        # Handle N/A specially as a string literal; otherwise use float datatype
        if value == "N/A":
            g.set((aw, _class(OS, "hasAtomicWeightValue"), Literal("N/A")))
        else:
            try:
                g.set((aw, _class(OS, "hasAtomicWeightValue"), Literal(float(value), datatype=XSD.float)))
            except Exception:
                return f"Invalid atomic weight value: {value!r}"
        g.add((parent, _class(OS, "hasAtomicWeight"), aw))
        return str(aw)


def delete_entity(entity_iri: str) -> str:
    """Remove all triples where the given entity is subject or object."""
    with locked_graph() as g:
        if not _is_abs_iri(entity_iri):
            return f"entity_iri must be an absolute https IRI, got {entity_iri!r}"
        e = URIRef(entity_iri)
        # remove outgoing
        for p, o in list(g.predicate_objects(e)):
            g.remove((e, p, o))
        # remove incoming
        for s, p in list(g.subject_predicates(e)):
            g.remove((s, p, e))
        return str(e)
def delete_triple(subject_iri: str, predicate_iri: str, object_value: str) -> str:
    """Remove one RDF triple matching the given subject, predicate, and object."""
    with locked_graph() as g:
        if not _is_abs_iri(subject_iri) or not _is_abs_iri(predicate_iri):
            return "subject_iri and predicate_iri must be absolute https IRIs"

        s = URIRef(subject_iri)
        p = URIRef(predicate_iri)

        # Determine if object is IRI or literal
        if _is_abs_iri(object_value):
            o = URIRef(object_value)
        else:
            o = Literal(object_value)

        if (s, p, o) in g:
            g.remove((s, p, o))
            return f"Removed triple ({s}, {p}, {o})"
        else:
            return f"No such triple found: ({s}, {p}, {o})"

def add_material_to_infrared_spectroscopy_data(ir_data_iri: str, material_name: str) -> str:
    """Create a Material node, type it, attach name, and link from an IR data node."""
    with locked_graph() as g:
        parent = _safe_parent(ir_data_iri)
        if parent is None:
            return "ir_data_iri must be an absolute https IRI"
        m = _mint_hash_iri("Material")
        _ensure_type_with_label(g, m, _class(OS, "Material"), material_name)
        g.set((m, _class(OS, "hasMaterialName"), Literal(material_name)))
        g.add((parent, _class(OS, "usesMaterial"), m))
        return str(m)

# ========= Introspection / export =========
def init_memory() -> str:
    with locked_graph():
        pass
    doi, ent = _read_global_state()
    return f"OntoSpecies memory ready for DOI='{doi}', entity='{ent}'"

def inspect_memory() -> str:
    lines: List[str] = []
    with locked_graph() as g:
        lines.append(f"Triples: {len(g)}")
        for s in sorted({s for s, _, _ in g if isinstance(s, URIRef)}, key=str):
            lines.append(f"\n{s}")
            for t in g.objects(s, RDF.type):
                lines.append(f"  rdf:type: {t}")
            for lbl in g.objects(s, RDFS.label):
                lines.append(f"  rdfs:label: {lbl}")
            for p, o in g.predicate_objects(s):
                if p in (RDF.type, RDFS.label):
                    continue
                lines.append(f"  {p} {'=' if isinstance(o, Literal) else '->'} {o}")
    return "\n".join(lines)

def export_memory() -> str:
    doi, ent = _read_global_state()
    mem = _memory_paths()
    out_dir = os.path.join(os.path.dirname(mem["dir"]), "ontospecies_output")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{_slugify(ent)}.ttl")
    with locked_graph() as g:
        g.serialize(destination=out, format="turtle")
    return os.path.abspath(out)

# ========= Demo =========
if __name__ == "__main__":
    print(init_memory())
    sp = create_species("IRMOP-50", product_name="IRMOP-50")
    print("species:", sp)

    ses = add_characterization_session_to_species(sp, "IRMOP-50 Characterization")
    print("session:", ses)

    dev_ir = add_infrared_spectroscopy_device_to_characterization_session(ses, "Nicolet FT-IR Impact 400")
    dev_ch = add_elemental_analysis_device_to_characterization_session(ses, "Elemental microanalysis, UMich")
    dev_nmr = add_hnmr_device_to_characterization_session(ses, "Bruker Avance III", "400 MHz")
    print("devices:", dev_ir, dev_ch, dev_nmr)

    ir = add_infrared_spectroscopy_data_to_species(sp, "IRMOP-50 IR Data",
                                                   "3436 (m), 3068 (m), 2939 (m), 2815 (w), 1658 (s), ...",
                                                   "KBr pellet")
    print("ir data:", ir)

    hn = add_hnmr_data_to_species(sp, "IRMOP-50 1H NMR", "δ 7.2–8.5 (m)", "295 K", "DMSO-d6")
    print("hnmr data:", hn)

    ea = add_elemental_analysis_data_to_species(
        sp,
        "IRMOP-50 EA",
        calculated_value_text="C 37.94, H 4.96, N 3.63",
        experimental_value_text="C 37.93, H 4.76, N 3.62",
        empirical_molecular_formula="C108H84N12O88S12",
    )
    print("ea:", ea)

    mf = add_molecular_formula_to_species(sp, "C108H84N12O88S12")
    print("mf:", mf)

    cf = add_chemical_formula_to_species(sp, "[NH2(CH3)2]8[Fe12O4(SO4)12(BDC)6(py)12]·(DMF)15(py)2(H2O)30")
    print("cf:", cf)

    el = create_element("Carbon", "C")
    aw = add_atomic_weight_to_element(el, 12.011)
    print("element+aw:", el, aw)

    print("export:", export_memory())
