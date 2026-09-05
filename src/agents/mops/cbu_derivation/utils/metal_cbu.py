import json
import os
import re
import hashlib
import unicodedata
from typing import List, Dict
from rdflib import Graph, Namespace
from rdflib.namespace import RDF, RDFS
from models.locations import DATA_DIR, DATA_CCDC_DIR
from src.agents.mops.cbu_derivation.utils.io_utils import resolve_identifier_to_hash
from src.agents.mops.cbu_derivation.utils.cbu_sparql import extract_ccdc_from_ttl
from src.pipelines.utils.runtime_paths import (
    first_existing_runtime_path,
    list_runtime_files,
    read_runtime_text,
    resolve_extension_artifact,
    runtime_path_exists,
)
from src.pipelines.utils.top_entity_identity import entity_artifact_name


def safe_name(label: str) -> str:
    """Apply the same capped filename policy as extension extraction/KG."""
    return entity_artifact_name(label)


def _normalize_label(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("_", " ").replace("-", " ").split())


def _score_ontomops_ttl(path: str, entity_label: str) -> tuple[int, int]:
    try:
        g = Graph()
        g.parse(data=read_runtime_text(path), format="turtle")
    except Exception:
        return (-1, -1)

    ontosyn = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    ontomops = Namespace("https://www.theworldavatar.com/kg/ontomops/")

    label_matches = 0
    for synth in g.subjects(RDF.type, ontosyn.ChemicalSynthesis):
        labels = [str(v) for v in g.objects(synth, RDFS.label)]
        if any(_normalize_label(v) == _normalize_label(entity_label) for v in labels):
            label_matches += 1
    if label_matches == 0:
        for synth, _, lbl in g.triples((None, RDFS.label, None)):
            if "ChemicalSynthesis/" in str(synth) and _normalize_label(str(lbl)) == _normalize_label(entity_label):
                label_matches += 1

    mop_facts = 0
    for subj in g.subjects(RDF.type, ontomops.MetalOrganicPolyhedron):
        mop_facts += 3 + len(list(g.triples((subj, None, None))))
    for pred in (ontomops.hasCCDCNumber, ontomops.hasMOPFormula, ontomops.hasChemicalBuildingUnit):
        mop_facts += sum(1 for _ in g.triples((None, pred, None)))

    return (label_matches, mop_facts)


def load_top_level_entities(hash_or_doi: str) -> List[Dict[str, str]]:
    """Load CBU entities from published OntoMOPs outputs, not Fine iter1.

    Metal derivation used to read ``mcp_run/iter1_top_entities.json``. Extension
    extract/KG and organic CBU already iterate ``ontomops_output``. A lean
    runtime without Fine ``mcp_run`` therefore skipped every metal entity.
    """
    hv = resolve_identifier_to_hash(hash_or_doi)
    doi_folder = os.path.join(DATA_DIR, hv)
    ttl_dir = os.path.join(doi_folder, "ontomops_output")
    entities: List[Dict[str, str]] = []
    seen: set[str] = set()

    filename_to_label: Dict[str, str] = {}
    mapping_file = os.path.join(ttl_dir, "ontomops_output_mapping.json")
    if runtime_path_exists(mapping_file):
        try:
            mapping = json.loads(read_runtime_text(mapping_file)) or {}
            for entity_label, filename in mapping.items():
                if str(entity_label).startswith("https://"):
                    continue
                filename_to_label[str(filename)] = str(entity_label)
        except (OSError, json.JSONDecodeError, TypeError):
            filename_to_label = {}

    if os.path.isdir(ttl_dir):
        for path in sorted(list_runtime_files(ttl_dir, suffix=".ttl")):
            name = os.path.basename(path)
            if not name.startswith("ontomops_extension_"):
                continue
            label = filename_to_label.get(
                name,
                name[len("ontomops_extension_") : -len(".ttl")],
            ).strip()
            if not label or label in seen:
                continue
            seen.add(label)
            entities.append({"label": label})
    if entities:
        return entities

    try:
        from src.pipelines.utils.published_synthesis_queue import (
            load_extension_synthesis_queue,
        )

        for item in load_extension_synthesis_queue(doi_folder) or []:
            label = str(item.get("label") or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            row: Dict[str, str] = {"label": label}
            uri = str(item.get("uri") or "").strip()
            if uri:
                row["uri"] = uri
            entities.append(row)
        if entities:
            return entities
    except Exception:
        pass

    p = os.path.join(doi_folder, "mcp_run", "iter1_top_entities.json")
    try:
        payload = json.loads(read_runtime_text(p)) or []
    except Exception:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        nested = payload.get("entities")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def load_entity_extraction_content(hash_or_doi: str, entity_label: str) -> str:
    hv = resolve_identifier_to_hash(hash_or_doi)
    doi_folder = os.path.join(DATA_DIR, hv)
    run_dir = os.path.join(doi_folder, "mcp_run_ontomops")

    _, candidates = resolve_extension_artifact(
        doi_folder,
        "mcp_run_ontomops/extraction_{entity_safe}.txt",
        entity_label,
    )
    found = first_existing_runtime_path(candidates)
    if found:
        return read_runtime_text(found)

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
                    _, more = resolve_extension_artifact(
                        doi_folder,
                        "mcp_run_ontomops/extraction_{entity_safe}.txt",
                        lbl,
                    )
                    found2 = first_existing_runtime_path(more)
                    if found2:
                        return read_runtime_text(found2)
    except Exception:
        pass

    # Last resort: scan for a close match on safe_name normalization
    try:
        wanted = safe_name(entity_label).lower().replace("-", "_")
        for path in list_runtime_files(run_dir, suffix=".txt"):
            fname = os.path.basename(path)
            if not fname.startswith("extraction_"):
                continue
            inner = fname[len("extraction_"):-len(".txt")].lower().replace("-", "_")
            if inner == wanted or inner.startswith(wanted[:40]):
                return read_runtime_text(path)
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
            ttl_files = [
                fname for fname in os.listdir(ontomops_dir)
                if fname.startswith('ontomops_extension_') and fname.endswith('.ttl')
            ]
            candidate_paths = []

            # First try to use the mapping file for exact matches
            mapping_file = os.path.join(ontomops_dir, "ontomops_output_mapping.json")
            if runtime_path_exists(mapping_file):
                try:
                    import json
                    mapping = json.loads(read_runtime_text(mapping_file))
                    # Check for exact entity match
                    if entity_label in mapping:
                        ttl_filename = mapping[entity_label]
                        p = os.path.join(ontomops_dir, ttl_filename)
                        if runtime_path_exists(p):
                            score = _score_ontomops_ttl(p, entity_label)
                            if score[1] > 0:
                                return read_runtime_text(p)
                            candidate_paths.append(p)
                    # Check for IRI match (some mappings use IRIs as keys)
                    for key, ttl_filename in mapping.items():
                        if key.startswith('http') and entity_label in key:
                            p = os.path.join(ontomops_dir, ttl_filename)
                            if runtime_path_exists(p):
                                candidate_paths.append(p)
                except Exception:
                    pass  # Fall back to fuzzy matching

            # Fall back to fuzzy matching if mapping doesn't work
            for fname in ttl_files:
                # Normalize both strings for comparison: replace both _ and space with a common character
                # This ensures "Ni12(iPr-cdc)12_cage" matches "Ni12(iPr-cdc)12 cage.ttl"
                fname_normalized = fname.replace('_', ' ').replace('-', ' ').lower()
                safe_normalized = safe.replace('_', ' ').replace('-', ' ').lower()

                if safe_normalized in fname_normalized:
                    candidate_paths.append(os.path.join(ontomops_dir, fname))
            # Only use broad fallback if no specific match found
            if not candidate_paths:
                candidate_paths.extend(os.path.join(ontomops_dir, fname) for fname in ttl_files)

            best_path = None
            best_score = (-1, -1)
            for candidate in dict.fromkeys(candidate_paths):
                if not runtime_path_exists(candidate):
                    continue
                score = _score_ontomops_ttl(candidate, entity_label)
                if score > best_score:
                    best_score = score
                    best_path = candidate
            if best_path and best_score[0] > 0:
                return read_runtime_text(best_path)
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
        if runtime_path_exists(path):
            return read_runtime_text(path)
    # loose match
    target = ''.join(ch for ch in entity_label.lower() if ch.isalnum())
    try:
        for path in list_runtime_files(hash_dir, suffix=".ttl"):
            fname = os.path.basename(path)
            if fname.startswith("output_"):
                inner = fname[len("output_"):-len(".ttl")]
                norm = ''.join(ch for ch in inner.lower() if ch.isalnum())
                if target and target in norm:
                    return read_runtime_text(path)
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


def usable_ccdc_number(ccdc: str) -> bool:
    value = str(ccdc or "").strip()
    return bool(value) and value.upper() != "N/A"


def _ccdc_lookup_aliases(label: str) -> list[str]:
    """Extra name queries besides the raw entity label."""
    text = str(label or "").strip()
    aliases: list[str] = []
    if text:
        aliases.append(text)
    for match in re.finditer(r"Cu24\([^)]+\)(?:\d+)?(?:\s+cage)?", text, flags=re.IGNORECASE):
        aliases.append(match.group(0))
    for match in re.finditer(r"Cu_[A-Za-z0-9]+-bdc(?:\s+(?:porous\s+)?cage)?", text, flags=re.IGNORECASE):
        aliases.append(match.group(0))
    seen: set[str] = set()
    ordered: list[str] = []
    for item in aliases:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def resolve_ccdc_for_derivation(label: str, ttl_text: str) -> str:
    """Use the entity TTL first, then the curated CCDC name map.

    An empty return means derivation should continue from paper/TTL
    without structure files. It is not a skip signal.
    """
    from_ttl = str(extract_ccdc_from_entity_ttl(ttl_text) or "").strip()
    if usable_ccdc_number(from_ttl):
        return from_ttl
    try:
        from src.mcp_servers.ccdc.operations.wsl_ccdc import _lookup_hardcoded_ccdc
    except Exception:
        return ""
    for query in _ccdc_lookup_aliases(label):
        hits = _lookup_hardcoded_ccdc(query) or []
        if hits:
            return str(hits[0][1]).strip()
    return ""


