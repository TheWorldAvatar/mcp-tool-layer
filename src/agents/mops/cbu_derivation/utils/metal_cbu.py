import os
import re
import hashlib
import unicodedata
from typing import List, Dict
from models.locations import DATA_DIR, DATA_CCDC_DIR
from src.agents.mops.cbu_derivation.utils.io_utils import resolve_identifier_to_hash
from src.agents.mops.cbu_derivation.utils.cbu_sparql import extract_ccdc_from_ttl


def safe_name(label: str) -> str:
    return (label or "entity").replace(" ", "_").replace("/", "_")


def load_top_level_entities(hash_or_doi: str) -> List[Dict[str, str]]:
    hv = resolve_identifier_to_hash(hash_or_doi)
    p = os.path.join(DATA_DIR, hv, "mcp_run", "iter1_top_entities.json")
    try:
        import json
        with open(p, 'r', encoding='utf-8') as f:
            ents = json.load(f) or []
        return ents
    except Exception:
        return []


def load_entity_extraction_content(hash_or_doi: str, entity_label: str) -> str:
    hv = resolve_identifier_to_hash(hash_or_doi)
    run_dir = os.path.join(DATA_DIR, hv, "mcp_run_ontomops")

    def _read(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # Primary: expected naming convention
    p = os.path.join(run_dir, f"extraction_{safe_name(entity_label)}.txt")
    if os.path.exists(p):
        return _read(p)

    # Fallback: if entity_label is a slugified/hash identifier (e.g., "synthesis-umc-1_924a6c41"),
    # resolve it back to the original label using iter1 entities + the same slug/hash logic as OntoMOPs.
    try:
        m = re.match(r"^(?P<slug>.+)_(?P<h>[0-9a-fA-F]{8})$", (entity_label or "").strip())
        if m:
            target = entity_label.strip().lower()

            def _ontomops_slug(s: str) -> str:
                t = unicodedata.normalize("NFKC", (s or "")).casefold()
                t = re.sub(r"\s+", "-", t)
                t = re.sub(r"[^a-z0-9\\-_]+", "-", t)
                t = re.sub(r"-+", "-", t).strip("-") or "entity"
                return t

            entities = load_top_level_entities(hv)
            for e in entities or []:
                lbl = (e or {}).get("label") or ""
                uri = (e or {}).get("uri") or ""
                if not lbl or not uri:
                    continue
                hh = hashlib.sha256(uri.encode()).hexdigest()[:8]
                cand = f"{_ontomops_slug(lbl)}_{hh}".lower()
                if cand == target:
                    p2 = os.path.join(run_dir, f"extraction_{safe_name(lbl)}.txt")
                    if os.path.exists(p2):
                        return _read(p2)
    except Exception:
        pass

    # Last resort: scan for a close match on safe_name normalization
    try:
        wanted = safe_name(entity_label).lower().replace("-", "_")
        for fname in os.listdir(run_dir):
            if not (fname.startswith("extraction_") and fname.endswith(".txt")):
                continue
            inner = fname[len("extraction_"):-len(".txt")].lower().replace("-", "_")
            if inner == wanted:
                return _read(os.path.join(run_dir, fname))
    except Exception:
        pass

    raise FileNotFoundError(f"Extraction file not found for entity '{entity_label}' under {run_dir}")


def load_entity_ttl_content(hash_or_doi: str, entity_label: str) -> str:
    hv = resolve_identifier_to_hash(hash_or_doi)
    safe = safe_name(entity_label)
    hash_dir = os.path.join(DATA_DIR, hv)
    # prefer ontomops_output
    ontomops_dir = os.path.join(hash_dir, "ontomops_output")
    if os.path.isdir(ontomops_dir):
        try:
            # First try to use the mapping file for exact matches
            mapping_file = os.path.join(ontomops_dir, "ontomops_output_mapping.json")
            if os.path.exists(mapping_file):
                try:
                    import json
                    with open(mapping_file, 'r', encoding='utf-8') as mf:
                        mapping = json.load(mf)
                    # Check for exact entity match
                    if entity_label in mapping:
                        ttl_filename = mapping[entity_label]
                        p = os.path.join(ontomops_dir, ttl_filename)
                        if os.path.exists(p):
                            with open(p, 'r', encoding='utf-8') as f:
                                return f.read()
                    # Check for IRI match (some mappings use IRIs as keys)
                    for key, ttl_filename in mapping.items():
                        if key.startswith('http') and entity_label in key:
                            p = os.path.join(ontomops_dir, ttl_filename)
                            if os.path.exists(p):
                                with open(p, 'r', encoding='utf-8') as f:
                                    return f.read()
                except Exception:
                    pass  # Fall back to fuzzy matching

            # Fall back to fuzzy matching if mapping doesn't work
            for fname in os.listdir(ontomops_dir):
                if not fname.endswith('.ttl'):
                    continue
                # Normalize both strings for comparison: replace both _ and space with a common character
                # This ensures "Ni12(iPr-cdc)12_cage" matches "Ni12(iPr-cdc)12 cage.ttl"
                fname_normalized = fname.replace('_', ' ').replace('-', ' ').lower()
                safe_normalized = safe.replace('_', ' ').replace('-', ' ').lower()

                if safe_normalized in fname_normalized:
                    p = os.path.join(ontomops_dir, fname)
                    with open(p, 'r', encoding='utf-8') as f:
                        return f.read()
            # Only use fallback if no specific match found
            for fname in os.listdir(ontomops_dir):
                if fname.startswith('ontomops_extension_') and fname.endswith('.ttl'):
                    p = os.path.join(ontomops_dir, fname)
                    with open(p, 'r', encoding='utf-8') as f:
                        return f.read()
        except Exception:
            pass
    # fallback legacy output_*.ttl
    candidates = [
        f"output_{safe}.ttl",
        f"output_{safe.replace('_','-')}.ttl",
        f"output_{safe.lower()}.ttl",
        f"output_{safe.lower().replace('_','-')}.ttl",
    ]
    for name in candidates:
        path = os.path.join(hash_dir, name)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
    # loose match
    target = ''.join(ch for ch in entity_label.lower() if ch.isalnum())
    try:
        for fname in os.listdir(hash_dir):
            if fname.startswith("output_") and fname.endswith(".ttl"):
                inner = fname[len("output_"):-len(".ttl")]
                norm = ''.join(ch for ch in inner.lower() if ch.isalnum())
                if target and target in norm:
                    with open(os.path.join(hash_dir, fname), 'r', encoding='utf-8') as f:
                        return f.read()
    except Exception:
        pass
    raise FileNotFoundError(f"Entity TTL not found for '{entity_label}' under {hash_dir}")


def ensure_ccdc_files(ccdc: str) -> None:
    res_p = os.path.join(DATA_CCDC_DIR, "res", f"{ccdc}.res")
    cif_p = os.path.join(DATA_CCDC_DIR, "cif", f"{ccdc}.cif")
    if os.path.exists(res_p) and os.path.exists(cif_p):
        return
    from src.mcp_servers.ccdc.operations.wsl_ccdc import get_res_cif_file_by_ccdc
    get_res_cif_file_by_ccdc(ccdc)


def extract_ccdc_from_entity_ttl(ttl_text: str) -> str:
    return extract_ccdc_from_ttl(ttl_text)


