import os
import json
from typing import Dict, List, Optional, Tuple
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import RDF, RDFS
from models.locations import DATA_DIR


def _list_hashes() -> List[str]:
    out: List[str] = []
    for name in os.listdir(DATA_DIR):
        p = os.path.join(DATA_DIR, name)
        if os.path.isdir(p) and len(name) == 8:
            out.append(name)
    return sorted(out)


def _safe_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c in ("_", "-", " ", "(", ")", ",", "'", "+", ".", "{", "}", "·")) else "_" for c in name).replace(" ", "_")


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _find_top_entities(hash_value: str) -> List[str]:
    """Derive top-level entity names from ontomops_output/ontomops_extension_*.ttl filenames."""
    out: List[str] = []
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    if not os.path.isdir(ttl_dir):
        return out
    for name in sorted(os.listdir(ttl_dir)):
        if not name.startswith("ontomops_extension_") or not name.endswith(".ttl"):
            continue
        entity = name[len("ontomops_extension_"):-len(".ttl")]
        if entity:
            out.append(entity)
    return out


def _read_metal_cbu_pair(hash_value: str, entity_name: str) -> Dict[str, str]:
    """Read metal CBU (formula, iri) from structured outputs if available.
    Looks under data/<hash>/cbu_derivation/metal/structured/ for <entity>.json, <entity>.txt and <entity>_iri.txt.
    """
    root = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "metal", "structured")
    data: Dict[str, str] = {"formula": "", "iri": ""}
    try:
        json_path = os.path.join(root, f"{entity_name}.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
            # our writer used key 'metal_cbu'
            mc = j.get("metal_cbu")
            if isinstance(mc, str):
                data["formula"] = mc
            elif isinstance(mc, dict):
                data["formula"] = mc.get("formula") or data["formula"]
                data["iri"] = mc.get("iri") or data["iri"]
    except Exception:
        pass
    # Fallback to txt and iri files
    txt_path = os.path.join(root, f"{entity_name}.txt")
    iri_path = os.path.join(root, f"{entity_name}_iri.txt")
    if not data["formula"]:
        data["formula"] = _read_text_file(txt_path)
    if not data["iri"]:
        data["iri"] = _read_text_file(iri_path)
    return data


def _read_organic_cbu_pair(hash_value: str, entity_name: str) -> Dict[str, str]:
    """Read organic CBU (formula, iri) from structured outputs if available.
    Looks under data/<hash>/cbu_derivation/organic/structured/ for <entity>.json, <entity>.txt and <entity>_iri.txt.
    """
    root = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "organic", "structured")
    data: Dict[str, str] = {"formula": "", "iri": ""}
    try:
        json_path = os.path.join(root, f"{entity_name}.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
            oc = j.get("organic_cbu")
            if isinstance(oc, str):
                data["formula"] = oc
            elif isinstance(oc, dict):
                data["formula"] = oc.get("formula") or data["formula"]
                data["iri"] = oc.get("iri") or data["iri"]
    except Exception:
        pass
    # Fallback to txt and iri files
    txt_path = os.path.join(root, f"{entity_name}.txt")
    iri_path = os.path.join(root, f"{entity_name}_iri.txt")
    if not data["formula"]:
        data["formula"] = _read_text_file(txt_path)
    if not data["iri"]:
        data["iri"] = _read_text_file(iri_path)
    return data
def _sanitize_iri(iri: str) -> str:
    """Strip surrounding brackets/quotes and whitespace. Return empty string if invalid."""
    if not iri:
        return ""
    s = str(iri).strip()
    # strip surrounding angle brackets or quotes
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    # basic sanity: must look like http(s) IRI
    if not (s.startswith("http://") or s.startswith("https://")):
        return ""
    return s



def integrate_hash(hash_value: str) -> List[Dict[str, str]]:
    entities = _find_top_entities(hash_value)
    integrated_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "integrated")
    os.makedirs(integrated_dir, exist_ok=True)
    results: List[Dict[str, str]] = []
    for entity in entities:
        metal_pair = _read_metal_cbu_pair(hash_value, entity)
        organic_pair = _read_organic_cbu_pair(hash_value, entity)
        data = {
            "entity": entity,
            "metal_cbu": {"formula": metal_pair.get("formula", ""), "iri": metal_pair.get("iri", "")},
            "organic_cbu": {"formula": organic_pair.get("formula", ""), "iri": organic_pair.get("iri", "")},
        }
        results.append(data)
        out_fn = os.path.join(integrated_dir, f"{entity}.json")
        with open(out_fn, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Attempt TTL rewrite alongside JSON
        try:
            _write_integrated_ttl(hash_value, entity, data["metal_cbu"], data["organic_cbu"], integrated_dir)
        except Exception:
            # Non-fatal; JSON written regardless
            pass
    return results


def integrate_all() -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = {}
    for hv in _list_hashes():
        out[hv] = integrate_hash(hv)
    return out


def _write_integrated_ttl(hash_value: str, entity: str, metal_cbu: Dict[str, str], organic_cbu: Dict[str, str], out_dir: str) -> None:
    """Copy the ontomops_extension TTL for entity, keep core MOP node and ONLY the specified CBUs with given IRIs and labels."""
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    src_path = os.path.join(ttl_dir, f"ontomops_extension_{entity}.ttl")
    if not os.path.exists(src_path):
        return
    g = Graph()
    g.parse(src_path, format="turtle")
    ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    g.bind("ontomops", ONTOMOPS)
    g.bind("rdfs", RDFS)

    # Find the MOP subject
    mop_subject = None
    for s, _, _ in g.triples((None, RDF.type, ONTOMOPS.MetalOrganicPolyhedron)):
        mop_subject = s
        break
    if mop_subject is None:
        return

    # Collect hasChemicalBuildingUnit objects and existing labels
    cbu_nodes: List = []
    for _, _, cbu in g.triples((mop_subject, ONTOMOPS.hasChemicalBuildingUnit, None)):
        cbu_nodes.append(cbu)

    # Extract original string labels for classification
    original_labels: Dict = {}
    for cbu in cbu_nodes:
        for _, _, lbl in g.triples((cbu, RDFS.label, None)):
            try:
                original_labels[cbu] = str(lbl)
            except Exception:
                original_labels[cbu] = ""
            break

    # Build a fresh graph with required triples only
    outg = Graph()
    outg.bind("ontomops", ONTOMOPS)
    outg.bind("rdfs", RDFS)

    # Keep MOP node with selected properties
    outg.add((mop_subject, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
    # Ensure hasCCDCNumber exists; if not, skip writing (not an actual MOP)
    has_ccdc = False
    for _, _, _o in g.triples((mop_subject, ONTOMOPS.hasCCDCNumber, None)):
        has_ccdc = True
        break
    if not has_ccdc:
        return
    for p in (RDFS.label, ONTOMOPS.hasCCDCNumber, ONTOMOPS.hasMOPFormula):
        for _, _, o in g.triples((mop_subject, p, None)):
            outg.add((mop_subject, p, o))
    # Keep ONLY the specified CBUs from JSON and drop others
    keep_cbus: List[Tuple[str, str]] = []  # list of (iri, label)
    m_formula = (metal_cbu.get("formula") or "").strip()
    m_iri = _sanitize_iri(metal_cbu.get("iri") or "")
    o_formula = (organic_cbu.get("formula") or "").strip()
    o_iri = _sanitize_iri(organic_cbu.get("iri") or "")
    if m_iri:
        keep_cbus.append((m_iri, m_formula))
    if o_iri and o_iri != m_iri:
        keep_cbus.append((o_iri, o_formula))

    # Add hasChemicalBuildingUnit for kept CBUs and their labels/types and derived formulas
    for iri_str, lbl in keep_cbus:
        if not iri_str:
            continue
        try:
            cbu_ref = __import__('rdflib').term.URIRef(iri_str)
        except Exception:
            continue
        outg.add((mop_subject, ONTOMOPS.hasChemicalBuildingUnit, cbu_ref))
        outg.add((cbu_ref, RDF.type, ONTOMOPS.ChemicalBuildingUnit))
        if lbl:
            outg.add((cbu_ref, RDFS.label, Literal(lbl)))
            # Insert the derived CBU formula as an explicit relation
            try:
                outg.add((cbu_ref, ONTOMOPS.hasCBUFormula, Literal(lbl)))
            except Exception:
                pass

    out_path = os.path.join(out_dir, f"{entity}.ttl")
    outg.serialize(destination=out_path, format="turtle")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Integrate metal and organic CBU results into per-entity JSON")
    ap.add_argument("--file", help="Run for a specific DOI/hash (optional)")
    args = ap.parse_args()
    if args.file:
        hv = args.file if len(args.file) == 8 else __import__('hashlib').sha256(args.file.encode()).hexdigest()[:8]
        integrate_hash(hv)
    else:
        integrate_all()
