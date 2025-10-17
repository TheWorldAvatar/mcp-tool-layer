import os
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
    p = os.path.join(DATA_DIR, hv, "mcp_run_ontomops", f"extraction_{safe_name(entity_label)}.txt")
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()


def load_entity_ttl_content(hash_or_doi: str, entity_label: str) -> str:
    hv = resolve_identifier_to_hash(hash_or_doi)
    safe = safe_name(entity_label)
    hash_dir = os.path.join(DATA_DIR, hv)
    # prefer ontomops_output
    ontomops_dir = os.path.join(hash_dir, "ontomops_output")
    if os.path.isdir(ontomops_dir):
        try:
            for fname in os.listdir(ontomops_dir):
                if not fname.endswith('.ttl'):
                    continue
                if safe in fname or safe.replace('_','-') in fname or safe.lower() in fname.lower():
                    p = os.path.join(ontomops_dir, fname)
                    with open(p, 'r', encoding='utf-8') as f:
                        return f.read()
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


