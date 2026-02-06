import os
import json
import sys
from typing import Dict, List, Optional, Tuple, Union
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import RDF, RDFS
from models.locations import DATA_DIR


def _configure_utf8_stdio() -> None:
    """Ensure Windows consoles don't crash on non-ASCII output."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_utf8_stdio()


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


def _read_derived_mop_formula(hash_value: str, entity: str) -> str:
    """Read derived mop_formula if present under data/<hash>/cbu_derivation/full/<entity>.json.
    
    This is populated by agent_mop_formula.py which combines metal and organic CBU formulas
    to derive the complete MOP formula.
    """
    try:
        full_path = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "full", f"{entity}.json")
        if not os.path.exists(full_path):
            return ""
        with open(full_path, "r", encoding="utf-8") as f:
            j = json.load(f) or {}
        v = str((j or {}).get("mop_formula") or "").strip()
        # Validation: reject invalid formulas
        if not v or v.upper() == "N/A" or "[]" in v or "[" not in v or "]" not in v:
            return ""
        return v
    except Exception:
        return ""


def _find_top_entities(hash_value: str) -> List[Tuple[str, str]]:
    """Derive top-level entity names from ontomops_output/ontomops_extension_*.ttl filenames.
    
    Returns:
        List of tuples: (actual_entity_label, filename)
    """
    out: List[Tuple[str, str]] = []
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    if not os.path.isdir(ttl_dir):
        return out
    
    # Load mapping file to convert filenames to actual entity labels
    mapping_file = os.path.join(ttl_dir, "ontomops_output_mapping.json")
    filename_to_label = {}  # Maps filename -> actual entity label
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r', encoding='utf-8') as mf:
                mapping = json.load(mf)
                # Reverse mapping: filename -> entity_label
                for entity_label, filename in mapping.items():
                    if not entity_label.startswith("https://"):  # Skip IRI entries, keep only label entries
                        filename_to_label[filename] = entity_label
        except Exception:
            pass
    
    for name in sorted(os.listdir(ttl_dir)):
        if not name.startswith("ontomops_extension_") or not name.endswith(".ttl"):
            continue
        # Try to get actual entity label from mapping, fallback to filename-based label
        actual_entity_label = filename_to_label.get(name, name[len("ontomops_extension_"):-len(".ttl")])
        if actual_entity_label:
            out.append((actual_entity_label, name))
    return out


def _read_metal_cbu_pair(hash_value: str, entity_name: str) -> Dict[str, str]:
    """Read metal CBU (formula, iri) from structured outputs if available.
    Looks under data/<hash>/cbu_derivation/metal/structured/ for <entity>.json, <entity>.txt and <entity>_iri.txt.
    """
    root = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "metal", "structured")
    data: Dict[str, str] = {"formula": "", "iri": ""}
    
    # Convert entity name to safe file name (spaces to underscores)
    safe_entity = entity_name.replace(' ', '_')
    
    try:
        json_path = os.path.join(root, f"{safe_entity}.json")
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
    txt_path = os.path.join(root, f"{safe_entity}.txt")
    iri_path = os.path.join(root, f"{safe_entity}_iri.txt")
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
    
    # Convert entity name to safe file name (spaces to underscores)
    safe_entity = entity_name.replace(' ', '_')
    
    try:
        json_path = os.path.join(root, f"{safe_entity}.json")
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
    txt_path = os.path.join(root, f"{safe_entity}.txt")
    iri_path = os.path.join(root, f"{safe_entity}_iri.txt")
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
    for entity_tuple in entities:
        # Handle both tuple format (entity_label, filename) and legacy string format
        if isinstance(entity_tuple, tuple):
            actual_entity_label, ttl_filename = entity_tuple
        else:
            # Legacy format: just entity name
            actual_entity_label = entity_tuple
            ttl_filename = f"ontomops_extension_{entity_tuple}.ttl"

        # Read derived formulas from structured outputs using actual entity label
        metal_pair = _read_metal_cbu_pair(hash_value, actual_entity_label)
        organic_pair = _read_organic_cbu_pair(hash_value, actual_entity_label)
        m_formula = (metal_pair.get("formula") or "").strip()
        o_formula = (organic_pair.get("formula") or "").strip()

        # Extract CCDC and prepare graphs for candidate build
        ccdc_number: str = ""
        ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
        # Use the actual filename from mapping
        src_path = os.path.join(ttl_dir, ttl_filename)
        # For root TTL, use safe name of actual entity label
        root_ttl_path = os.path.join(DATA_DIR, hash_value, f"output_{_safe_name(actual_entity_label)}.ttl")

        g = Graph()
        root = Graph()
        try:
            if os.path.exists(src_path):
                g.parse(src_path, format="turtle")
        except Exception:
            g = Graph()
        try:
            if os.path.exists(root_ttl_path):
                root.parse(root_ttl_path, format="turtle")
        except Exception:
            root = Graph()

        ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
        ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")

        mop_subject = None
        # First try to find properly typed MOPs
        for s, _, _ in g.triples((None, RDF.type, ONTOMOPS.MetalOrganicPolyhedron)):
            mop_subject = s
            for _, _, o in g.triples((s, ONTOMOPS.hasCCDCNumber, None)):
                ccdc_number = str(o)
                break
            break

        # If no properly typed MOP found, try to find MOPs with CCDC numbers or MOP formulas
        if mop_subject is None:
            for s, _, _ in g.triples((None, ONTOMOPS.hasCCDCNumber, None)):
                mop_subject = s
                for _, _, o in g.triples((s, ONTOMOPS.hasCCDCNumber, None)):
                    ccdc_number = str(o)
                    break
                break

        if mop_subject is None:
            for s, _, _ in g.triples((None, ONTOMOPS.hasMOPFormula, None)):
                mop_subject = s
                for _, _, o in g.triples((s, ONTOMOPS.hasCCDCNumber, None)):
                    ccdc_number = str(o)
                    break
                break

        if mop_subject is None:
            # No MOP node found; write minimal JSON and continue
            print(f"[INTEGRATION] No MOP node found in TTL for {actual_entity_label}, skipping IRI selection")
            # Use safe name for output filename to match existing format
            safe_entity_name = _safe_name(actual_entity_label)
            data = {
                "entity": actual_entity_label,
                "metal_cbu": {"formula": m_formula, "iri": ""},
                "organic_cbu": {"formula": o_formula, "iri": ""},
                "ccdc_number": ccdc_number or "",
            }
            results.append(data)
            out_fn = os.path.join(integrated_dir, f"{safe_entity_name}.json")
            with open(out_fn, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            continue

        # Build candidate list from TTLs
        candidates: List[Dict[str, object]] = []
        mop_labels = [str(o) for _, _, o in g.triples((mop_subject, RDFS.label, None))]
        mop_ccdc = ccdc_number

        cbu_nodes: List = [cbu for _, _, cbu in g.triples((mop_subject, ONTOMOPS.hasChemicalBuildingUnit, None))]
        for cbu in cbu_nodes:
            iri = str(cbu)
            labels: List[str] = []
            alt_names: List[str] = []
            formulas: List[str] = []
            amounts: List[str] = []
            is_ci = False
            try:
                for _, _, lbl in root.triples((cbu, RDFS.label, None)):
                    s = str(lbl).strip()
                    if s and s not in labels:
                        labels.append(s)
                for _, _, lbl in g.triples((cbu, RDFS.label, None)):
                    s = str(lbl).strip()
                    if s and s not in labels:
                        labels.append(s)
                for _, _, an in root.triples((cbu, ONTOSYN.hasAlternativeNames, None)):
                    s = str(an).strip()
                    if s and s not in alt_names:
                        alt_names.append(s)
                for _, _, an in g.triples((cbu, ONTOSYN.hasAlternativeNames, None)):
                    s = str(an).strip()
                    if s and s not in alt_names:
                        alt_names.append(s)
                for _, _, cf in root.triples((cbu, ONTOSYN.hasChemicalFormula, None)):
                    s = str(cf).strip()
                    if s and s not in formulas:
                        formulas.append(s)
                for _, _, cf in g.triples((cbu, ONTOSYN.hasChemicalFormula, None)):
                    s = str(cf).strip()
                    if s and s not in formulas:
                        formulas.append(s)
                for _, _, am in root.triples((cbu, ONTOSYN.hasAmount, None)):
                    s = str(am).strip()
                    if s and s not in amounts:
                        amounts.append(s)
                for _, _, am in g.triples((cbu, ONTOSYN.hasAmount, None)):
                    s = str(am).strip()
                    if s and s not in amounts:
                        amounts.append(s)
                is_ci = any(True for _ in g.triples((cbu, RDF.type, ONTOSYN.ChemicalInput))) or any(True for _ in root.triples((cbu, RDF.type, ONTOSYN.ChemicalInput)))
            except Exception:
                pass
            candidates.append({
                "iri": iri,
                "labels": labels,
                "alt_names": alt_names,
                "formulas": formulas,
                "amounts": amounts,
                "is_ci": is_ci,
            })

        # Save debug information about what we're trying to match
        try:
            debug_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "debug")
            os.makedirs(debug_dir, exist_ok=True)
            debug_file = os.path.join(debug_dir, f"{actual_entity_label}_integration_debug.md")
            with open(debug_file, "w", encoding="utf-8") as df:
                df.write(f"# Integration Debug - {actual_entity_label}\n\n")
                df.write(f"**Timestamp:** {__import__('datetime').datetime.now().isoformat()}\n\n")
                df.write(f"**Metal Formula:** {m_formula}\n")
                df.write(f"**Organic Formula:** {o_formula}\n")
                df.write(f"**MOP Labels:** {mop_labels}\n")
                df.write(f"**CCDC:** {mop_ccdc}\n")
                df.write(f"**TTL File:** {ttl_filename}\n\n")
                df.write(f"**Candidates ({len(candidates)}):**\n")
                for i, cand in enumerate(candidates):
                    df.write(f"- {i+1}: IRI={cand.get('iri', '')}\n")
                    df.write(f"  Labels: {cand.get('labels', [])}\n")
                    df.write(f"  Alt Names: {cand.get('alt_names', [])}\n")
                    df.write(f"  Formula: {cand.get('formulas', [])}\n\n")
        except Exception as e:
            print(f"Warning: Failed to save integration debug info: {e}")

        # LLM selection of IRIs
        try:
            from src.agents.mops.cbu_derivation.utils.iri_selection import llm_select_cbu_iris
            sel_m, sel_o = llm_select_cbu_iris(
                entity=actual_entity_label,
                mop_labels=mop_labels,
                mop_ccdc=mop_ccdc,
                candidates=candidates,
                metal_formula=m_formula,
                organic_formula=o_formula,
                hash_value=hash_value,
            )
        except Exception as e:
            # Ensure downstream logic does not crash if selection fails.
            sel_m, sel_o = None, None
            # Save the exception details to debug file
            try:
                debug_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "debug")
                os.makedirs(debug_dir, exist_ok=True)
                error_file = os.path.join(debug_dir, f"{actual_entity_label}_integration_error.md")
                with open(error_file, "w", encoding="utf-8") as ef:
                    ef.write(f"# Integration Error - {actual_entity_label}\n\n")
                    ef.write(f"**Timestamp:** {__import__('datetime').datetime.now().isoformat()}\n\n")
                    ef.write(f"**Error:** {str(e)}\n")
                    ef.write(f"**Error Type:** {type(e).__name__}\n\n")
                    ef.write(f"**Metal Formula:** {m_formula}\n")
                    ef.write(f"**Organic Formula:** {o_formula}\n")
                    ef.write(f"**MOP Labels:** {mop_labels}\n")
                    ef.write(f"**CCDC:** {mop_ccdc}\n")
            except Exception:
                pass

        # Check if IRI selection succeeded
        if sel_m is None or sel_o is None:
            print(f"[ERROR] IRI selection failed for entity {actual_entity_label}: Could not match CBU formulas to IRIs. "
                  f"Metal formula: '{m_formula}', Organic formula: '{o_formula}'. "
                  f"This usually indicates LLM failure or mismatched CBU formulas.")
            print(f"[INTEGRATION] Skipping {actual_entity_label} due to IRI selection failure, continuing with other entities")
            continue

        sel_m = _sanitize_iri(sel_m)
        sel_o = _sanitize_iri(sel_o)

        # Validate that we got actual IRIs, not empty strings
        if not sel_m or not sel_o:
            print(f"[ERROR] IRI selection returned empty IRIs for entity {actual_entity_label}. "
                  f"Selected metal IRI: '{sel_m}', organic IRI: '{sel_o}'. "
                  f"Check LLM responses and CBU formula matching.")
            print(f"[INTEGRATION] Skipping {actual_entity_label} due to empty IRI selection, continuing with other entities")
            continue

        # Use safe name for output filename to match existing format
        safe_entity_name = _safe_name(actual_entity_label)
        data = {
            "entity": actual_entity_label,
            "metal_cbu": {"formula": m_formula, "iri": sel_m},
            "organic_cbu": {"formula": o_formula, "iri": sel_o},
            "ccdc_number": ccdc_number or "",
        }
        results.append(data)

        out_fn = os.path.join(integrated_dir, f"{safe_entity_name}.json")
        with open(out_fn, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Write TTL using the LLM-selected IRIs from JSON
        # Generate TTL if we have valid IRIs, even if CCDC is missing
        metal_iri = data["metal_cbu"].get("iri", "").strip()
        organic_iri = data["organic_cbu"].get("iri", "").strip()
        if metal_iri or organic_iri:
            try:
                # Read derived MOP formula from agent_mop_formula.py (if available)
                # This will override the MOP formula in the TTL with the derived one
                mop_formula_override = _read_derived_mop_formula(hash_value, safe_entity_name)
                _write_integrated_ttl(hash_value, actual_entity_label, ttl_filename, data["metal_cbu"], data["organic_cbu"], integrated_dir, mop_formula_override=mop_formula_override)
                print(f"[INTEGRATION] Generated TTL for {actual_entity_label} with formula override: '{mop_formula_override}'")
            except Exception as e:
                print(f"[INTEGRATION] Failed to generate TTL for {actual_entity_label}: {e}")

    return results


def integrate_all() -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = {}
    for hv in _list_hashes():
        out[hv] = integrate_hash(hv)
    return out


def _write_integrated_ttl(hash_value: str, entity_label: str, ttl_filename: str, metal_cbu: Dict[str, str], organic_cbu: Dict[str, str], out_dir: str, *, mop_formula_override: str = "") -> None:
    """Copy the ontomops_extension TTL for entity, keep core MOP node and ONLY the specified CBUs with given IRIs and labels."""
    try:
        ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
        # Use the actual filename from mapping
        src_path = os.path.join(ttl_dir, ttl_filename)
        if not os.path.exists(src_path):
            print(f"[INTEGRATION] Source TTL not found: {src_path}")
            return
        g = Graph()
        g.parse(src_path, format="turtle")
        print(f"[INTEGRATION] Parsed source TTL with {len(g)} triples")
        ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
        ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
        g.bind("ontomops", ONTOMOPS)
        g.bind("rdfs", RDFS)
        g.bind("ns1", ONTOSYN)
    except Exception as e:
        print(f"[INTEGRATION] Failed to load source TTL {src_path}: {e}")
        return

    # Load the root entity TTL to fetch detailed labels/types for CBUs referenced by the extension
    # Use safe name for root TTL path
    root_ttl_path = os.path.join(DATA_DIR, hash_value, f"output_{_safe_name(entity_label)}.ttl")
    root = Graph()
    try:
        if os.path.exists(root_ttl_path):
            root.parse(root_ttl_path, format="turtle")
            print(f"[INTEGRATION] Parsed root TTL with {len(root)} triples")
        else:
            print(f"[INTEGRATION] Root TTL not found: {root_ttl_path}")
    except Exception as e:
        print(f"[INTEGRATION] Failed to parse root TTL {root_ttl_path}: {e}")
        root = Graph()

    # Find the MOP subject - select the properly typed MOP with the most CBUs
    mop_candidates = []

    # First try to find properly typed MOPs
    mop_count = 0
    for s, _, _ in g.triples((None, RDF.type, ONTOMOPS.MetalOrganicPolyhedron)):
        cbu_count = len(list(g.triples((s, ONTOMOPS.hasChemicalBuildingUnit, None))))
        mop_candidates.append((cbu_count, s))
        mop_count += 1

    # If no properly typed MOPs found, look for subjects that have MOP properties
    # (hasChemicalBuildingUnit is required, others are optional)
    if not mop_candidates:
        print("[INTEGRATION] No properly typed MOPs found, looking for MOP-like subjects...")
        candidate_subjects = set()

        # Find subjects that have hasChemicalBuildingUnit (key MOP property)
        for s, _, _ in g.triples((None, ONTOMOPS.hasChemicalBuildingUnit, None)):
            candidate_subjects.add(s)

        for subject in candidate_subjects:
            cbu_count = len(list(g.triples((subject, ONTOMOPS.hasChemicalBuildingUnit, None))))
            if cbu_count > 0:  # Must have at least one CBU
                mop_candidates.append((cbu_count, subject))
                mop_count += 1
                print(f"[INTEGRATION] Found MOP-like subject: {subject} with {cbu_count} CBUs")

    print(f"[INTEGRATION] Found {mop_count} MOP subjects with candidates: {[(count, str(s)) for count, s in mop_candidates]}")

    # Select the MOP with the most CBUs (among properly typed MOPs)
    if mop_candidates:
        mop_candidates.sort(reverse=True)  # Sort by CBU count descending
        mop_subject = mop_candidates[0][1]
        print(f"[INTEGRATION] Selected MOP: {mop_subject}")
    else:
        print("[INTEGRATION] No MOP subjects found, skipping TTL creation")
        return

    # Collect available CBUs from the source TTL
    cbu_nodes: List = []
    for _, _, cbu in g.triples((mop_subject, ONTOMOPS.hasChemicalBuildingUnit, None)):
        cbu_nodes.append(cbu)
    candidate_iris = {str(c) for c in cbu_nodes}
    print(f"[INTEGRATION] Found {len(candidate_iris)} candidate CBUs: {candidate_iris}")

    # Build an output graph with the MOP header
    outg = Graph()
    outg.bind("ontomops", ONTOMOPS)
    outg.bind("rdfs", RDFS)
    # Emit OntoSyn prefix in the output TTL
    outg.bind("ns1", ONTOSYN)
    outg.add((mop_subject, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))

    # Note: We no longer require CCDC to exist for integrated TTL generation
    # The CBU IRIs are what matter for the integration

    # Copy label and CCDC; handle MOP formula separately to allow override
    for p in (RDFS.label, ONTOMOPS.hasCCDCNumber):
        for _, _, o in g.triples((mop_subject, p, None)):
            outg.add((mop_subject, p, o))
    # Write MOP formula: prefer override; else copy existing
    written_formula = False
    mf = (mop_formula_override or "").strip()
    if mf:
        try:
            outg.add((mop_subject, ONTOMOPS.hasMOPFormula, Literal(mf)))
            written_formula = True
        except Exception:
            written_formula = False
    if not written_formula:
        for _, _, o in g.triples((mop_subject, ONTOMOPS.hasMOPFormula, None)):
            outg.add((mop_subject, ONTOMOPS.hasMOPFormula, o))

    # Copy ChemicalSynthesis node that links to this MOP (if present in source TTL)
    try:
        for cs_node, _, _ in g.triples((None, ONTOSYN.hasChemicalOutput, mop_subject)):
            outg.add((cs_node, RDF.type, ONTOSYN.ChemicalSynthesis))
            outg.add((cs_node, ONTOSYN.hasChemicalOutput, mop_subject))
            # Only one expected per entity; break after first to avoid duplicates
            break
    except Exception:
        pass

    # Use the LLM-selected IRIs provided in JSON, not any legacy fallbacks
    m_formula = (metal_cbu.get("formula") or "").strip()
    o_formula = (organic_cbu.get("formula") or "").strip()
    sel_m = _sanitize_iri(metal_cbu.get("iri") or "")
    sel_o = _sanitize_iri(organic_cbu.get("iri") or "")

    print(f"[INTEGRATION] Selected metal IRI: {sel_m} (formula: {m_formula})")
    print(f"[INTEGRATION] Selected organic IRI: {sel_o} (formula: {o_formula})")

    # Collect all selected IRIs (both existing and newly generated)
    selected_cbus: List[Tuple[str, str, bool]] = []  # (iri, formula, is_generated)
    if sel_m:
        is_generated = sel_m not in candidate_iris
        selected_cbus.append((sel_m, m_formula, is_generated))
        print(f"[INTEGRATION] Metal CBU {'generated' if is_generated else 'existing'}: {sel_m}")
    if sel_o and sel_o != sel_m:
        is_generated = sel_o not in candidate_iris
        selected_cbus.append((sel_o, o_formula, is_generated))
        print(f"[INTEGRATION] Organic CBU {'generated' if is_generated else 'existing'}: {sel_o}")

    # Emit selected CBUs with derived-formula labels
    for iri_str, lbl, is_generated in selected_cbus:
        try:
            cbu_ref = __import__('rdflib').term.URIRef(iri_str)
        except Exception:
            continue

        outg.add((mop_subject, ONTOMOPS.hasChemicalBuildingUnit, cbu_ref))
        outg.add((cbu_ref, RDF.type, ONTOMOPS.ChemicalBuildingUnit))

        # Always add label if present (including empty string), but only add formula if non-empty
        if lbl is not None:
            outg.add((cbu_ref, RDFS.label, Literal(lbl)))
        if lbl:
            try:
                outg.add((cbu_ref, ONTOMOPS.hasCBUFormula, Literal(lbl)))
            except Exception:
                pass

        # For generated CBUs, also add as ChemicalInput type
        if is_generated:
            ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
            outg.add((cbu_ref, RDF.type, ONTOSYN.ChemicalInput))
            print(f"[INTEGRATION] Created new CBU with IRI: {iri_str}")

    # Use safe name for output filename to match existing format
    safe_entity_name = _safe_name(entity_label)
    out_path = os.path.join(out_dir, f"{safe_entity_name}.ttl")
    try:
        outg.serialize(destination=out_path, format="turtle")
        print(f"[INTEGRATION] Successfully created TTL: {out_path} with {len(outg)} triples")
    except Exception as e:
        print(f"[INTEGRATION] Failed to serialize TTL to {out_path}: {e}")
        raise


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
