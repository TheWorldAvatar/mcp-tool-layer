import os
import json
import sys
from typing import Dict, List, Optional, Tuple, Union
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS
from models.locations import DATA_DIR
from src.pipelines.utils.runtime_paths import (
    list_runtime_files,
    read_runtime_text,
    runtime_path_exists,
    write_runtime_text,
)
from src.pipelines.utils.top_entity_identity import entity_artifact_name
from src.pipelines.utils.ttl_publisher import get_output_naming_config, load_meta_task_config
from src.pipelines.utils.top_entity_identity import entity_scope_name


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
    return entity_artifact_name(name)


def _read_text_file(path: str) -> str:
    try:
        if not runtime_path_exists(path):
            return ""
        return read_runtime_text(path).strip()
    except Exception:
        return ""


def _normalize_label(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("_", " ").split())


def _resolve_root_ttl_path(hash_value: str, entity_label: str, *, data_dir: str) -> str:
    """Resolve the exact published main TTL through the canonical identity lock."""
    case_dir = os.path.join(data_dir, hash_value)
    identity_lock_path = os.path.join(
        case_dir,
        "mcp_run",
        "top_entity_identity_lock.json",
    )
    if not os.path.isfile(identity_lock_path):
        raise FileNotFoundError(
            f"Canonical top-entity identity lock is missing: {identity_lock_path}"
        )
    with open(identity_lock_path, "r", encoding="utf-8") as handle:
        identity_lock = json.load(handle)
    matches = [
        item
        for item in identity_lock.get("entities", [])
        if isinstance(item, dict) and item.get("label") == entity_label
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one canonical identity for {entity_label!r}; found {len(matches)}"
        )

    entity_uri = str(matches[0].get("uri") or "").strip()
    if not entity_uri:
        raise ValueError(f"Canonical identity URI is missing for {entity_label!r}")
    scope_name = entity_scope_name(entity_label, entity_uri)

    meta_cfg = load_meta_task_config()
    ontology_name = str(
        (meta_cfg.get("ontologies", {}).get("main", {}) or {}).get("name")
        or "ontosynthesis"
    ).strip() or "ontosynthesis"
    naming = get_output_naming_config(
        meta_cfg=meta_cfg,
        ontology_name=ontology_name,
    )
    root_path = os.path.join(
        case_dir,
        naming.output_dir,
        f"{scope_name}.ttl",
    )
    if not os.path.isfile(root_path):
        raise FileNotFoundError(f"Canonical main ontology TTL is missing: {root_path}")
    return root_path


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
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    if not os.path.isdir(ttl_dir):
        return []

    def _score_candidate(path: str, expected_label: str) -> Tuple[int, int]:
        try:
            g = Graph()
            g.parse(data=read_runtime_text(path), format="turtle")
        except Exception:
            return (-1, -1)

        label_matches = 0
        for synth in g.subjects(RDF.type, Namespace("https://www.theworldavatar.com/kg/OntoSyn/").ChemicalSynthesis):
            labels = [str(v) for v in g.objects(synth, RDFS.label)]
            if any(_normalize_label(v) == _normalize_label(expected_label) for v in labels):
                label_matches += 1
        if label_matches == 0:
            for synth, _, lbl in g.triples((None, RDFS.label, None)):
                if isinstance(lbl, Literal) and "ChemicalSynthesis/" in str(synth):
                    if _normalize_label(str(lbl)) == _normalize_label(expected_label):
                        label_matches += 1

        mop_facts = 0
        ontomops = Namespace("https://www.theworldavatar.com/kg/ontomops/")
        for subj in g.subjects(RDF.type, ontomops.MetalOrganicPolyhedron):
            mop_facts += 3 + len(list(g.triples((subj, None, None))))
        for pred in (ontomops.hasCCDCNumber, ontomops.hasMOPFormula, ontomops.hasChemicalBuildingUnit):
            mop_facts += sum(1 for _ in g.triples((None, pred, None)))
        return (label_matches, mop_facts)

    all_ttls = [
        os.path.basename(path)
        for path in sorted(list_runtime_files(ttl_dir, suffix=".ttl"))
        if os.path.basename(path).startswith("ontomops_extension_")
    ]

    # Load mapping file to convert filenames to actual entity labels
    mapping_file = os.path.join(ttl_dir, "ontomops_output_mapping.json")
    filename_to_label = {}  # Maps filename -> actual entity label
    if runtime_path_exists(mapping_file):
        try:
            mapping = json.loads(read_runtime_text(mapping_file))
            # Reverse mapping: filename -> entity_label
            for entity_label, filename in mapping.items():
                if not entity_label.startswith("https://"):  # Skip IRI entries, keep only label entries
                    filename_to_label[filename] = entity_label
        except Exception:
            pass

    preferred: List[Tuple[str, str]] = []
    used_files: set[str] = set()

    if filename_to_label:
        labels = sorted(set(filename_to_label.values()))
        label_to_files: Dict[str, List[str]] = {}
        for filename, label in filename_to_label.items():
            label_to_files.setdefault(label, []).append(filename)

        for label in labels:
            candidates = [
                os.path.join(ttl_dir, filename)
                for filename in label_to_files.get(label, [])
                if filename in all_ttls
            ]
            candidates.extend(
                os.path.join(ttl_dir, filename)
                for filename in all_ttls
                if filename not in used_files
            )
            scored = sorted(
                {
                    path: _score_candidate(path, label)
                    for path in candidates
                }.items(),
                key=lambda item: (item[1][0], item[1][1], item[0]),
                reverse=True,
            )
            if scored and scored[0][1][0] > 0 and scored[0][1][1] > 0:
                chosen = os.path.basename(scored[0][0])
                preferred.append((label, chosen))
                used_files.add(chosen)
        if preferred:
            return preferred

    out: List[Tuple[str, str]] = []
    for name in all_ttls:
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
    
    safe_entity = _safe_name(entity_name)
    
    try:
        json_path = os.path.join(root, f"{safe_entity}.json")
        if runtime_path_exists(json_path):
            j = json.loads(read_runtime_text(json_path)) or {}
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
    
    safe_entity = _safe_name(entity_name)
    
    try:
        json_path = os.path.join(root, f"{safe_entity}.json")
        if runtime_path_exists(json_path):
            j = json.loads(read_runtime_text(json_path)) or {}
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


def _select_primary_mop(graph: Graph, ontomops: Namespace):
    """Pick the real MOP node, not an empty typed stub.

    OntoMOPS graphs sometimes emit a bare ``MetalOrganicPolyhedron`` individual
    with no CBUs or CCDC before the actual product node. Integration must attach
    derived formulas to the node that already has building units.
    """
    typed: List[Tuple[int, int, object]] = []
    for subject, _, _ in graph.triples((None, RDF.type, ontomops.MetalOrganicPolyhedron)):
        cbu_count = len(list(graph.triples((subject, ontomops.hasChemicalBuildingUnit, None))))
        has_ccdc = any(True for _ in graph.triples((subject, ontomops.hasCCDCNumber, None)))
        typed.append((cbu_count, 1 if has_ccdc else 0, subject))
    typed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if typed and typed[0][0] > 0:
        return typed[0][2]

    likes: List[Tuple[int, object]] = []
    seen = set()
    for subject, _, _ in graph.triples((None, ontomops.hasChemicalBuildingUnit, None)):
        if subject in seen:
            continue
        seen.add(subject)
        cbu_count = len(list(graph.triples((subject, ontomops.hasChemicalBuildingUnit, None))))
        likes.append((cbu_count, subject))
    if likes:
        likes.sort(key=lambda item: item[0], reverse=True)
        return likes[0][1]
    if typed:
        return typed[0][2]
    return None


_METAL_HINTS = (
    "voso",
    "vanadyl",
    "vanadium",
    "v6o",
    "zr6",
    "fe3",
    "cu2",
    "cluster",
    "och3",
    "so4",
    "metal precursor",
)
_ORGANIC_HINTS = (
    "co2",
    "bdc",
    "btc",
    "bpdc",
    "edb",
    "carboxylic",
    "carboxylate",
    "benzene",
    "c6h",
    "c8h",
    "c9h",
    "c12h",
    "c14h",
    "linker",
)


def _candidate_blob(candidate: Dict[str, object]) -> str:
    parts: List[str] = []
    for key in ("labels", "alt_names", "formulas"):
        for item in candidate.get(key) or []:
            text = str(item or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts).casefold()


def _role_score(blob: str, hints: Tuple[str, ...]) -> int:
    return sum(1 for hint in hints if hint in blob)


def _try_heuristic_iris(
    candidates: List[Dict[str, object]],
    *,
    metal_formula: str = "",
    organic_formula: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """Assign existing CBU IRIs from labels/formulas when the split is obvious."""
    del metal_formula, organic_formula
    if not candidates:
        return None, None
    scored: List[Tuple[Dict[str, object], int, int]] = []
    for candidate in candidates:
        blob = _candidate_blob(candidate)
        scored.append(
            (
                candidate,
                _role_score(blob, _METAL_HINTS),
                _role_score(blob, _ORGANIC_HINTS),
            )
        )
    metal_iri: Optional[str] = None
    organic_iri: Optional[str] = None
    metal_ranked = sorted(scored, key=lambda item: (item[1] - item[2], item[1]), reverse=True)
    organic_ranked = sorted(scored, key=lambda item: (item[2] - item[1], item[2]), reverse=True)
    if metal_ranked and metal_ranked[0][1] > metal_ranked[0][2]:
        metal_iri = str(metal_ranked[0][0].get("iri") or "").strip() or None
    if organic_ranked and organic_ranked[0][2] > organic_ranked[0][1]:
        organic_iri = str(organic_ranked[0][0].get("iri") or "").strip() or None
    if metal_iri and organic_iri and metal_iri == organic_iri:
        metal_margin = metal_ranked[0][1] - metal_ranked[0][2]
        organic_margin = organic_ranked[0][2] - organic_ranked[0][1]
        if metal_margin >= organic_margin:
            organic_iri = None
            for candidate, _metal_score, organic_score in organic_ranked:
                iri = str(candidate.get("iri") or "").strip()
                if iri and iri != metal_iri and organic_score > 0:
                    organic_iri = iri
                    break
        else:
            metal_iri = None
            for candidate, metal_score, _organic_score in metal_ranked:
                iri = str(candidate.get("iri") or "").strip()
                if iri and iri != organic_iri and metal_score > 0:
                    metal_iri = iri
                    break
    return metal_iri, organic_iri


def _is_formula_like_label(text: str) -> bool:
    value = str(text or "").strip()
    return bool(value.startswith("[") and value.endswith("]") and len(value) > 2)


def _overwrite_source_cbu_formulas(
    src_path: str,
    selected: List[Tuple[str, str]],
) -> None:
    """Replace OntoMOPS draft formulas on the selected CBU IRIs.

    Merge unions ``ontomops_output`` with ``integrated``. If the source file
    keeps the draft ``hasCBUFormula``, conversion will still score that value.
    """
    replacements = [
        (_sanitize_iri(iri), str(formula).strip())
        for iri, formula in selected
        if _sanitize_iri(iri) and str(formula or "").strip()
    ]
    if not replacements or not runtime_path_exists(src_path):
        return

    graph = Graph()
    graph.parse(data=read_runtime_text(src_path), format="turtle")
    ontomops = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    for iri_str, formula in replacements:
        cbu = URIRef(iri_str)
        for obj in list(graph.objects(cbu, ontomops.hasCBUFormula)):
            graph.remove((cbu, ontomops.hasCBUFormula, obj))
        graph.add((cbu, ontomops.hasCBUFormula, Literal(formula)))
        for obj in list(graph.objects(cbu, RDFS.label)):
            if _is_formula_like_label(str(obj)):
                graph.remove((cbu, RDFS.label, obj))
        if not any(_is_formula_like_label(str(obj)) for obj in graph.objects(cbu, RDFS.label)):
            graph.add((cbu, RDFS.label, Literal(formula)))

    payload = graph.serialize(format="turtle")
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    write_runtime_text(src_path, payload)
    print(f"[INTEGRATION] Overwrote source CBU formulas in {src_path}")


def _optional_root_ttl_path(hash_value: str, entity_label: str, *, data_dir: str) -> str:
    try:
        return _resolve_root_ttl_path(hash_value, entity_label, data_dir=data_dir)
    except (FileNotFoundError, ValueError):
        return ""


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
        root_ttl_path = _optional_root_ttl_path(
            hash_value,
            actual_entity_label,
            data_dir=DATA_DIR,
        )

        g = Graph()
        root = Graph()
        try:
            if runtime_path_exists(src_path):
                g.parse(data=read_runtime_text(src_path), format="turtle")
        except Exception:
            g = Graph()
        try:
            if root_ttl_path and runtime_path_exists(root_ttl_path):
                root.parse(data=read_runtime_text(root_ttl_path), format="turtle")
        except Exception:
            root = Graph()

        ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
        ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")

        mop_subject = _select_primary_mop(g, ONTOMOPS)
        if mop_subject is not None:
            for _, _, o in g.triples((mop_subject, ONTOMOPS.hasCCDCNumber, None)):
                ccdc_number = str(o)
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
            write_runtime_text(out_fn, json.dumps(data, ensure_ascii=False, indent=2))
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
                for _, _, cf in root.triples((cbu, ONTOMOPS.hasCBUFormula, None)):
                    s = str(cf).strip()
                    if s and s not in formulas:
                        formulas.append(s)
                for _, _, cf in g.triples((cbu, ONTOMOPS.hasCBUFormula, None)):
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
            debug_file = os.path.join(debug_dir, f"{_safe_name(actual_entity_label)}_integration_debug.md")
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

        heur_m, heur_o = _try_heuristic_iris(
            candidates,
            metal_formula=m_formula,
            organic_formula=o_formula,
        )
        sel_m, sel_o = heur_m, heur_o
        need_llm = bool((o_formula and not sel_o) or (m_formula and not sel_m))
        if need_llm:
            try:
                from src.agents.mops.cbu_derivation.utils.iri_selection import llm_select_cbu_iris
                llm_m, llm_o = llm_select_cbu_iris(
                    entity=actual_entity_label,
                    mop_labels=mop_labels,
                    mop_ccdc=mop_ccdc,
                    candidates=candidates,
                    metal_formula=m_formula,
                    organic_formula=o_formula,
                    hash_value=hash_value,
                )
                sel_m = sel_m or llm_m
                sel_o = sel_o or llm_o
            except Exception as e:
                try:
                    debug_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "selection", "debug")
                    os.makedirs(debug_dir, exist_ok=True)
                    error_file = os.path.join(debug_dir, f"{_safe_name(actual_entity_label)}_integration_error.md")
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

        if not sel_m and m_formula:
            from src.agents.mops.cbu_derivation.utils.iri_selection import _generate_cbu_iri
            sel_m = _generate_cbu_iri(actual_entity_label, m_formula, "metal", hash_value)
            print(f"[INTEGRATION] Generated metal IRI for {actual_entity_label}: {sel_m}")
        if not sel_o and o_formula:
            from src.agents.mops.cbu_derivation.utils.iri_selection import _generate_cbu_iri
            sel_o = _generate_cbu_iri(actual_entity_label, o_formula, "organic", hash_value)
            print(f"[INTEGRATION] Generated organic IRI for {actual_entity_label}: {sel_o}")

        sel_m = _sanitize_iri(sel_m or "")
        sel_o = _sanitize_iri(sel_o or "")

        if not sel_m and not sel_o:
            print(
                f"[INTEGRATION] Skipping {actual_entity_label}: no CBU IRI could be "
                f"resolved (metal='{m_formula}', organic='{o_formula}')."
            )
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
        write_runtime_text(out_fn, json.dumps(data, ensure_ascii=False, indent=2))

        # Write TTL using the LLM-selected IRIs from JSON
        # Generate TTL if we have valid IRIs, even if CCDC is missing
        metal_iri = data["metal_cbu"].get("iri", "").strip()
        organic_iri = data["organic_cbu"].get("iri", "").strip()
        if metal_iri or organic_iri:
            try:
                # Read derived MOP formula from agent_mop_formula.py (if available)
                # This will override the MOP formula in the TTL with the derived one
                mop_formula_override = _read_derived_mop_formula(hash_value, safe_entity_name)
                _write_integrated_ttl(
                    hash_value,
                    actual_entity_label,
                    ttl_filename,
                    data["metal_cbu"],
                    data["organic_cbu"],
                    integrated_dir,
                    data_dir=DATA_DIR,
                    mop_formula_override=mop_formula_override,
                )
                print(f"[INTEGRATION] Generated TTL for {actual_entity_label} with formula override: '{mop_formula_override}'")
            except Exception as e:
                print(f"[INTEGRATION] Failed to generate TTL for {actual_entity_label}: {e}")

    return results


def integrate_all() -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = {}
    for hv in _list_hashes():
        out[hv] = integrate_hash(hv)
    return out


def _write_integrated_ttl(
    hash_value: str,
    entity_label: str,
    ttl_filename: str,
    metal_cbu: Dict[str, str],
    organic_cbu: Dict[str, str],
    out_dir: str,
    *,
    data_dir: str,
    mop_formula_override: str = "",
) -> None:
    """Copy the ontomops_extension TTL for entity, keep core MOP node and ONLY the specified CBUs with given IRIs and labels."""
    try:
        ttl_dir = os.path.join(data_dir, hash_value, "ontomops_output")
        # Use the actual filename from mapping
        src_path = os.path.join(ttl_dir, ttl_filename)
        if not runtime_path_exists(src_path):
            print(f"[INTEGRATION] Source TTL not found: {src_path}")
            return
        g = Graph()
        g.parse(data=read_runtime_text(src_path), format="turtle")
        print(f"[INTEGRATION] Parsed source TTL with {len(g)} triples")
        ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
        ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
        g.bind("ontomops", ONTOMOPS)
        g.bind("rdfs", RDFS)
        g.bind("ns1", ONTOSYN)
    except Exception as e:
        print(f"[INTEGRATION] Failed to load source TTL {src_path}: {e}")
        return

    root_ttl_path = _optional_root_ttl_path(
        hash_value,
        entity_label,
        data_dir=data_dir,
    )
    root = Graph()
    try:
        if root_ttl_path and runtime_path_exists(root_ttl_path):
            root.parse(data=read_runtime_text(root_ttl_path), format="turtle")
            print(f"[INTEGRATION] Parsed root TTL with {len(root)} triples")
        else:
            print(f"[INTEGRATION] Root TTL not found: {root_ttl_path}")
    except Exception as e:
        print(f"[INTEGRATION] Failed to parse root TTL {root_ttl_path}: {e}")
        root = Graph()

    mop_subject = _select_primary_mop(g, ONTOMOPS)
    if mop_subject is None:
        print("[INTEGRATION] No MOP subjects found, skipping TTL creation")
        return
    print(f"[INTEGRATION] Selected MOP: {mop_subject}")

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

        # Derived formula wins. If derivation left this side empty, keep the source formula.
        if lbl:
            outg.add((cbu_ref, RDFS.label, Literal(lbl)))
            outg.add((cbu_ref, ONTOMOPS.hasCBUFormula, Literal(lbl)))
        else:
            source_formula = ""
            for _, _, obj in g.triples((cbu_ref, ONTOMOPS.hasCBUFormula, None)):
                source_formula = str(obj).strip()
                if source_formula:
                    break
            if source_formula:
                outg.add((cbu_ref, RDFS.label, Literal(source_formula)))
                outg.add((cbu_ref, ONTOMOPS.hasCBUFormula, Literal(source_formula)))
            elif lbl is not None:
                outg.add((cbu_ref, RDFS.label, Literal(lbl)))

        # For generated CBUs, also add as ChemicalInput type
        if is_generated:
            ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
            outg.add((cbu_ref, RDF.type, ONTOSYN.ChemicalInput))
            print(f"[INTEGRATION] Created new CBU with IRI: {iri_str}")

    # Use safe name for output filename to match existing format
    safe_entity_name = _safe_name(entity_label)
    out_path = os.path.join(out_dir, f"{safe_entity_name}.ttl")
    try:
        payload = outg.serialize(format="turtle")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        write_runtime_text(out_path, payload)
        print(f"[INTEGRATION] Successfully created TTL: {out_path} with {len(outg)} triples")
        _overwrite_source_cbu_formulas(src_path, [(iri, formula) for iri, formula, _generated in selected_cbus])
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
