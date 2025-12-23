"""
MOPs Knowledge Graph Operations

Core functions for querying the MOPs knowledge graph using SPARQL.
All functions are standalone and can be tested independently.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
from rdflib import Graph, Namespace
import logging
import json
import threading
import time
import difflib

# Define namespaces
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOSPECIES = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")

logger = logging.getLogger(__name__)

# Global knowledge graph (loaded once)
_kg = None  # type: Optional[Graph]

# Local fuzzy index cache (built from KG, persisted to disk under /data which is gitignored)
_label_index: Optional[Dict[str, Any]] = None
_label_index_lock = threading.Lock()

def load_knowledge_graph(data_path: str) -> Graph:
    """
    Load all merged TTL files into a single knowledge graph.
    
    Args:
        data_path: Path to merged_tll directory
        
    Returns:
        RDF Graph with all loaded data
        
    Example:
        >>> graph = load_knowledge_graph("evaluation/data/merged_tll")
        >>> print(f"Loaded {len(graph)} triples")
    """
    graph = Graph()
    merged_dir = Path(data_path)
    
    if not merged_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {merged_dir}")
    
    ttl_count = 0
    for hash_dir in sorted(merged_dir.iterdir()):
        if hash_dir.is_dir():
            ttl_file = hash_dir / f"{hash_dir.name}.ttl"
            if ttl_file.exists():
                try:
                    graph.parse(str(ttl_file), format="turtle")
                    ttl_count += 1
                except Exception as e:
                    logger.warning(f"Failed to load {ttl_file}: {e}")
    
    logger.info(f"Loaded {ttl_count} TTL files with {len(graph)} triples")
    return graph

def ensure_kg_loaded() -> Graph:
    """Ensure knowledge graph is loaded (lazy loading)."""
    global _kg  # pylint: disable=global-statement
    if _kg is None:
        # Default path relative to repo root
        repo_root = Path(__file__).resolve().parents[2]
        data_path = repo_root / "evaluation" / "data" / "merged_tll"
        logger.info(f"Loading knowledge graph from: {data_path}")
        _kg = load_knowledge_graph(str(data_path))
    return _kg


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cache_dir() -> Path:
    # /data is gitignored; this keeps generated indices out of the repo
    d = _repo_root() / "data" / "mini_marie_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _cache_dir() / "kg_label_index.json"


def _merged_ttl_dir() -> Path:
    return _repo_root() / "evaluation" / "data" / "merged_tll"


def _latest_mtime_in_dir(p: Path) -> float:
    """
    Best-effort latest mtime of *.ttl files under merged_tll/*/*.ttl.
    """
    try:
        latest = 0.0
        if not p.exists():
            return latest
        for sub in p.iterdir():
            if not sub.is_dir():
                continue
            ttl = sub / f"{sub.name}.ttl"
            if ttl.exists():
                latest = max(latest, ttl.stat().st_mtime)
        return latest
    except Exception:
        return 0.0


def _build_label_index() -> Dict[str, Any]:
    """
    Build a local label index from the in-memory KG.
    This runs full list queries once (no LIMIT), then persists to disk.
    """
    kg = ensure_kg_loaded()

    # Full lists (no LIMIT)
    synth_q = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?label WHERE {
      ?s a ontosyn:ChemicalSynthesis .
      ?s rdfs:label ?label .
    } ORDER BY ?label
    """

    mop_q = """
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?label WHERE {
      ?m a ontomops:MetalOrganicPolyhedron .
      ?m rdfs:label ?label .
    } ORDER BY ?label
    """

    chem_q = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?label WHERE {
      ?s a ontosyn:ChemicalSynthesis .
      ?s ontosyn:hasChemicalInput ?c .
      ?c rdfs:label ?label .
    } ORDER BY ?label
    """

    # IR materials list (full, no LIMIT)
    ir_mat_q = """
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    SELECT DISTINCT ?materialName WHERE {
      ?species a ontospecies:Species .
      ?species ontospecies:hasInfraredSpectroscopyData ?ir .
      ?ir ontospecies:usesMaterial ?mat .
      ?mat ontospecies:hasMaterialName ?materialName .
      FILTER(STRLEN(STR(?materialName)) > 0)
    } ORDER BY ?materialName
    """

    def _labels(query: str) -> List[str]:
        out: List[str] = []
        for row in kg.query(query):
            try:
                v = str(row["label"]) if "label" in row.labels else str(row[0])
            except Exception:
                v = str(row[0]) if row else ""
            v = (v or "").strip()
            if v:
                out.append(v)
        # de-dupe preserve order
        seen: set[str] = set()
        dedup: List[str] = []
        for s in out:
            if s not in seen:
                seen.add(s)
                dedup.append(s)
        return dedup

    def _strings(query: str, var: str) -> List[str]:
        out: List[str] = []
        for row in kg.query(query):
            v = str(getattr(row, var)) if getattr(row, var, None) is not None else None
            if v:
                v = v.strip()
                if v:
                    out.append(v)
        seen: set[str] = set()
        dedup: List[str] = []
        for s in out:
            if s not in seen:
                seen.add(s)
                dedup.append(s)
        return dedup

    syntheses = _labels(synth_q)
    mops = _labels(mop_q)
    chemicals = _labels(chem_q)
    ir_materials = _strings(ir_mat_q, "materialName")

    idx = {
        "meta": {
            "built_at_unix": time.time(),
            "source_merged_ttl_latest_mtime": _latest_mtime_in_dir(_merged_ttl_dir()),
            "triples": len(kg),
        },
        "syntheses": syntheses,
        "mops": mops,
        "chemicals": chemicals,
        "ir_materials": ir_materials,
    }
    return idx


def warm_label_index(force: bool = False) -> Dict[str, Any]:
    """
    Ensure the local label index is loaded in memory (and written to disk if rebuilt).

    Invalidation strategy:
    - If cache file missing -> build
    - If merged_tll latest mtime > cache meta mtime -> rebuild
    - If force=True -> rebuild
    """
    global _label_index  # pylint: disable=global-statement
    with _label_index_lock:
        if _label_index is not None and not force:
            return _label_index

        cache_file = _index_path()
        source_mtime = _latest_mtime_in_dir(_merged_ttl_dir())

        if not force and cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_mtime = float((data.get("meta") or {}).get("source_merged_ttl_latest_mtime") or 0.0)
                if cached_mtime >= source_mtime and data.get("syntheses") and data.get("mops"):
                    _label_index = data
                    return _label_index
            except Exception:
                # fall through to rebuild
                pass

        # Rebuild
        idx = _build_label_index()
        try:
            cache_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write label index cache: {e}")
        _label_index = idx
        return _label_index


def _fuzzy_rank(query: str, candidates: List[str], limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
    """
    Rank candidates by similarity using stdlib difflib (no extra deps).
    Returns list of {match, score}.
    """
    q = (query or "").strip()
    if not q:
        return []

    scored: List[tuple[float, str]] = []
    for c in candidates:
        # SequenceMatcher is robust and fast enough for a few thousand candidates
        s = difflib.SequenceMatcher(None, q.lower(), (c or "").lower()).ratio()
        if s >= cutoff:
            scored.append((s, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for s, c in scored[:limit]:
        out.append({"match": c, "score": round(s, 4)})
    return out


def fuzzy_lookup_synthesis_name(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
    idx = warm_label_index(force=False)
    return _fuzzy_rank(query, idx.get("syntheses") or [], limit=limit, cutoff=cutoff)


def fuzzy_lookup_mop_name(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
    idx = warm_label_index(force=False)
    return _fuzzy_rank(query, idx.get("mops") or [], limit=limit, cutoff=cutoff)


def fuzzy_lookup_chemical_name(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
    idx = warm_label_index(force=False)
    return _fuzzy_rank(query, idx.get("chemicals") or [], limit=limit, cutoff=cutoff)


def fuzzy_lookup_ir_material(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
    idx = warm_label_index(force=False)
    return _fuzzy_rank(query, idx.get("ir_materials") or [], limit=limit, cutoff=cutoff)

def execute_sparql(query: str) -> List[Dict[str, Any]]:
    """
    Execute a SPARQL query and return results as list of dictionaries.
    
    Args:
        query: SPARQL query string
        
    Returns:
        List of dictionaries with query results
        
    Example:
        >>> query = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 5"
        >>> results = execute_sparql(query)
        >>> print(results)
    """
    kg = ensure_kg_loaded()
    
    results = []
    for row in kg.query(query):
        result_dict = {}
        for var in row.labels:
            value = row[var]
            if value is not None:
                result_dict[var] = str(value)
            else:
                result_dict[var] = None
        results.append(result_dict)
    
    return results

def format_results_as_tsv(results: List[Dict[str, Any]]) -> str:
    """
    Format results as TSV string for MCP tool output.
    
    Args:
        results: List of result dictionaries
        
    Returns:
        TSV-formatted string
    """
    if not results:
        return "No results found"
    
    # Get headers from first result
    headers = list(results[0].keys())
    lines = ["\t".join(headers)]
    
    for result in results:
        row = [str(result.get(h, "")) for h in headers]
        lines.append("\t".join(row))
    
    return "\n".join(lines)


# ============================================================================
# Query helpers
# ============================================================================

OrderOption = Literal["asc", "desc", "none"]


def _order_by_clause(
    order: Optional[str],
    var: str,
) -> str:
    """
    Build an optional SPARQL ORDER BY clause.

    Args:
        order: "asc", "desc", "none"/None
        var: SPARQL variable string like "?durationValue"
    """
    if order is None:
        return ""
    o = str(order).strip().lower()
    if o in ("", "none", "null"):
        return ""
    if o == "asc":
        return f"ORDER BY {var}"
    if o == "desc":
        return f"ORDER BY DESC({var})"
    raise ValueError(f"Invalid order '{order}'. Expected one of: asc, desc, none")

# ============================================================================
# Lookup Functions - Find entities by name/ID
# ============================================================================

def lookup_synthesis_iri(name: str) -> List[Dict[str, Any]]:
    """
    Find synthesis IRI by its label/name.
    
    Args:
        name: Synthesis name (e.g., "VMOP-17", "UMC-1")
        
    Returns:
        List with synthesis IRI and label
        
    Example:
        >>> results = lookup_synthesis_iri("VMOP-17")
        >>> print(results[0]['synthesis'])
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT ?synthesis ?synthesisLabel
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{name}" .
        BIND("{name}" as ?synthesisLabel)
    }}
    LIMIT 5
    """
    return execute_sparql(query)

def lookup_mop_iri(name: str) -> List[Dict[str, Any]]:
    """
    Find MOP IRI by its label/name.
    
    Args:
        name: MOP name (e.g., "CIAC-105", "Cage ZrT-1")
        
    Returns:
        List with MOP IRI, label, and CCDC number
        
    Example:
        >>> results = lookup_mop_iri("CIAC-105")
        >>> print(results[0]['ccdcNumber'])
    """
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT ?mop ?mopLabel ?ccdcNumber
    WHERE {{
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label "{name}" .
        BIND("{name}" as ?mopLabel)
        OPTIONAL {{ ?mop ontomops:hasCCDCNumber ?ccdcNumber }}
    }}
    LIMIT 5
    """
    return execute_sparql(query)

def lookup_by_ccdc(ccdc_number: str) -> List[Dict[str, Any]]:
    """
    Find MOPs by CCDC number.
    
    Args:
        ccdc_number: CCDC crystallographic database number
        
    Returns:
        List of MOPs with matching CCDC number
        
    Example:
        >>> results = lookup_by_ccdc("869988")
        >>> print(results[0]['mopLabel'])
    """
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?mop ?mopLabel ?mopFormula
    WHERE {{
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label ?mopLabel .
        ?mop ontomops:hasCCDCNumber "{ccdc_number}" .
        OPTIONAL {{ ?mop ontomops:hasMOPFormula ?mopFormula }}
    }}
    """
    return execute_sparql(query)

# ============================================================================
# General Query Functions
# ============================================================================

def get_all_mops(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get all MOPs with their CCDC numbers and formulas.
    
    Args:
        limit: Maximum number of MOPs to return
        
    Returns:
        List of MOPs with labels, CCDC numbers, and formulas
        
    Example:
        >>> mops = get_all_mops(limit=10)
        >>> for mop in mops:
        ...     print(f"{mop['mopLabel']}: CCDC {mop.get('ccdcNumber', 'N/A')}")
    """
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?mopLabel ?ccdcNumber ?mopFormula
    WHERE {{
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label ?mopLabel .
        OPTIONAL {{ ?mop ontomops:hasCCDCNumber ?ccdcNumber }}
        OPTIONAL {{ ?mop ontomops:hasMOPFormula ?mopFormula }}
    }}
    ORDER BY ?mopLabel
    LIMIT {limit}
    """
    return execute_sparql(query)

def get_kg_statistics() -> Dict[str, int]:
    """
    Get overall knowledge graph statistics.
    
    Returns:
        Dictionary with counts of MOPs, syntheses, steps, CBUs
        
    Example:
        >>> stats = get_kg_statistics()
        >>> print(f"Total MOPs: {stats['total_mops']}")
        >>> print(f"Total Syntheses: {stats['total_syntheses']}")
    """
    queries = {
        "total_mops": """
            SELECT (COUNT(DISTINCT ?mop) as ?count)
            WHERE {
                ?mop a <https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron> .
                ?mop <http://www.w3.org/2000/01/rdf-schema#label> ?label .
            }
        """,
        "total_syntheses": """
            SELECT (COUNT(DISTINCT ?s) as ?count)
            WHERE { ?s a <https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis> . }
        """,
        "total_synthesis_steps": """
            SELECT (COUNT(DISTINCT ?step) as ?count)
            WHERE { ?s <https://www.theworldavatar.com/kg/OntoSyn/hasSynthesisStep> ?step . }
        """,
        "total_cbus": """
            SELECT (COUNT(DISTINCT ?cbu) as ?count)
            WHERE { ?mop <https://www.theworldavatar.com/kg/ontomops/hasChemicalBuildingUnit> ?cbu . }
        """
    }
    
    stats = {}
    for key, query in queries.items():
        results = execute_sparql(query)
        stats[key] = int(results[0]['count']) if results and results[0]['count'] else 0
    
    return stats

# ============================================================================
# Synthesis Query Functions
# ============================================================================

def get_synthesis_recipe(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get the complete recipe (chemical inputs) for a synthesis.
    
    Args:
        synthesis_name: Name of synthesis procedure
        
    Returns:
        List of chemicals with amounts, formulas, suppliers
        
    Example:
        >>> recipe = get_synthesis_recipe("VMOP-17")
        >>> for chem in recipe:
        ...     print(f"{chem['chemicalLabel']}: {chem.get('amount', 'N/A')}")
    """
    # NOTE:
    # Some corpora encode "recipe" only at step-level (e.g., Add/Dissolve/Wash),
    # not at synthesis-level via ontosyn:hasChemicalInput. We therefore treat
    # "recipe" as "all chemical inputs used in the synthesis", aggregating:
    # - synthesis-level: ontosyn:hasChemicalInput
    # - step-level: ontosyn:hasAddedChemicalInput, ontosyn:hasSolventDissolve,
    #              ontosyn:hasWashingSolvent, ontosyn:hasWashingChemical
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?role ?stepLabel ?chemicalLabel ?amount ?formula ?purity ?supplier
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .

        {{
            ?synthesis ontosyn:hasChemicalInput ?chemical .
            BIND("synthesis_input" as ?role)
            BIND("" as ?stepLabel)
        }}
        UNION
        {{
            ?synthesis ontosyn:hasSynthesisStep ?step .
            ?step rdfs:label ?stepLabel .
            ?step ontosyn:hasAddedChemicalInput ?chemical .
            BIND("added_chemical" as ?role)
        }}
        UNION
        {{
            ?synthesis ontosyn:hasSynthesisStep ?step .
            ?step rdfs:label ?stepLabel .
            ?step ontosyn:hasSolventDissolve ?chemical .
            BIND("solvent" as ?role)
        }}
        UNION
        {{
            ?synthesis ontosyn:hasSynthesisStep ?step .
            ?step rdfs:label ?stepLabel .
            ?step ontosyn:hasWashingSolvent ?chemical .
            BIND("washing_solvent" as ?role)
        }}
        UNION
        {{
            ?synthesis ontosyn:hasSynthesisStep ?step .
            ?step rdfs:label ?stepLabel .
            ?step ontosyn:hasWashingChemical ?chemical .
            BIND("washing_chemical" as ?role)
        }}

        OPTIONAL {{ ?chemical rdfs:label ?chemicalLabel }}
        OPTIONAL {{ ?chemical ontosyn:hasAmount ?amount }}
        OPTIONAL {{ ?chemical ontosyn:hasChemicalFormula ?formula }}
        OPTIONAL {{ ?chemical ontosyn:hasPurity ?purity }}
        OPTIONAL {{
            ?chemical ontosyn:isSuppliedBy ?sup .
            ?sup rdfs:label ?supplier
        }}
    }}
    ORDER BY ?role ?stepLabel ?chemicalLabel
    """
    return execute_sparql(query)


def get_syntheses_producing_mop(mop_name: str) -> List[Dict[str, Any]]:
    """
    Bridge query: find syntheses that produce a given MOP (by label).
    Handles both direct output-as-MOP and output-as-ChemicalOutput represented by a MOP.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?synthesisLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label ?synthesisLabel .
      ?synthesis ontosyn:hasChemicalOutput ?out .

      {{
        ?out a ontomops:MetalOrganicPolyhedron .
        ?out rdfs:label "{mop_name}" .
      }}
      UNION
      {{
        ?out ontosyn:isRepresentedBy ?mop .
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label "{mop_name}" .
      }}
    }}
    ORDER BY ?synthesisLabel
    """
    return execute_sparql(query)


def find_mops_by_cbu_formula_contains(
    substring: str,
    limit: int = 100,
    order: Literal["asc", "desc", "none"] = "asc",
) -> List[Dict[str, Any]]:
    """
    Search MOPs by whether any CBU formula contains a substring (e.g., 'Zr').
    This is a pragmatic bridge for questions like "MOPs where Zr is used in metal CBU".
    """
    order_clause = _order_by_clause(order, "?mopLabel")
    sub = (substring or "").strip()
    if not sub:
        raise ValueError("substring cannot be empty")
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?mopLabel ?ccdcNumber ?cbuFormula
    WHERE {{
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label ?mopLabel .
        OPTIONAL {{ ?mop ontomops:hasCCDCNumber ?ccdcNumber }}
        ?mop ontomops:hasChemicalBuildingUnit ?cbu .
        OPTIONAL {{ ?cbu ontomops:hasCBUFormula ?cbuFormula }}
        FILTER(BOUND(?cbuFormula) && CONTAINS(LCASE(STR(?cbuFormula)), LCASE("{sub}")))
    }}
    {order_clause}
    LIMIT {limit}
    """
    return execute_sparql(query)

def get_synthesis_steps(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get all synthesis steps for a procedure.
    
    Args:
        synthesis_name: Name of synthesis procedure
        
    Returns:
        List of steps with labels and types
        
    Example:
        >>> steps = get_synthesis_steps("UMC-1")
        >>> for i, step in enumerate(steps, 1):
        ...     print(f"Step {i}: {step['stepLabel']} ({step['stepType']})")
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    
    SELECT DISTINCT ?stepLabel ?stepType
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        ?step rdf:type ?stepType .
        FILTER(STRSTARTS(STR(?stepType), "https://www.theworldavatar.com/kg/OntoSyn/"))
    }}
    ORDER BY ?stepLabel
    """
    
    results = execute_sparql(query)
    # Clean up step types to show only the class name
    for result in results:
        if 'stepType' in result and result['stepType']:
            result['stepType'] = result['stepType'].split('/')[-1]
    
    return results

def get_synthesis_temperatures(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get temperature conditions for a synthesis.
    
    Args:
        synthesis_name: Name of synthesis procedure
        
    Returns:
        List of steps with temperature values and units
        
    Example:
        >>> temps = get_synthesis_temperatures("VMOP-17")
        >>> for temp in temps:
        ...     print(f"{temp['stepLabel']}: {temp['tempValue']} {temp.get('tempUnit', '°C')}")
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?stepLabel ?tempValue ?tempUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        {{
            ?step ontosyn:hasTargetTemperature ?temp .
        }} UNION {{
            ?step ontosyn:hasCrystallizationTargetTemperature ?temp .
        }}
        ?temp om-2:hasNumericalValue ?tempValue .
        OPTIONAL {{ ?temp om-2:hasUnit ?tempUnit }}
    }}
    ORDER BY ?tempValue
    """
    
    results = execute_sparql(query)
    # Clean up units
    for result in results:
        if 'tempUnit' in result and result['tempUnit']:
            result['tempUnit'] = result['tempUnit'].split('/')[-1]
    
    return results


def get_synthesis_temperatures_ordered(
    synthesis_name: str,
    order: OrderOption = "asc",
) -> List[Dict[str, Any]]:
    """
    Same as get_synthesis_temperatures but with optional ordering control.
    """
    order_clause = _order_by_clause(order, "?tempValue")
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?tempValue ?tempUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        {{
            ?step ontosyn:hasTargetTemperature ?temp .
        }} UNION {{
            ?step ontosyn:hasCrystallizationTargetTemperature ?temp .
        }}
        ?temp om-2:hasNumericalValue ?tempValue .
        OPTIONAL {{ ?temp om-2:hasUnit ?tempUnit }}
    }}
    {order_clause}
    """
    results = execute_sparql(query)
    for result in results:
        if 'tempUnit' in result and result['tempUnit']:
            result['tempUnit'] = result['tempUnit'].split('/')[-1]
    return results

def get_synthesis_durations(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get duration conditions for a synthesis.
    
    Args:
        synthesis_name: Name of synthesis procedure
        
    Returns:
        List of steps with duration values and units
        
    Example:
        >>> durations = get_synthesis_durations("UMC-2")
        >>> for dur in durations:
        ...     print(f"{dur['stepLabel']}: {dur['durationValue']} {dur.get('durationUnit', 'units')}")
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?stepLabel ?durationValue ?durationUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        ?step ontosyn:hasStepDuration ?duration .
        ?duration om-2:hasNumericalValue ?durationValue .
        OPTIONAL {{ ?duration om-2:hasUnit ?durationUnit }}
    }}
    ORDER BY ?durationValue
    """
    
    results = execute_sparql(query)
    # Clean up units
    for result in results:
        if 'durationUnit' in result and result['durationUnit']:
            result['durationUnit'] = result['durationUnit'].split('/')[-1]
    
    return results


def get_synthesis_durations_ordered(
    synthesis_name: str,
    order: OrderOption = "asc",
) -> List[Dict[str, Any]]:
    """
    Same as get_synthesis_durations but with optional ordering control.
    """
    order_clause = _order_by_clause(order, "?durationValue")
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?durationValue ?durationUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        ?step ontosyn:hasStepDuration ?duration .
        ?duration om-2:hasNumericalValue ?durationValue .
        OPTIONAL {{ ?duration om-2:hasUnit ?durationUnit }}
    }}
    {order_clause}
    """
    results = execute_sparql(query)
    for result in results:
        if 'durationUnit' in result and result['durationUnit']:
            result['durationUnit'] = result['durationUnit'].split('/')[-1]
    return results


# ============================================================================
# Atomic step-level query functions (inspired by conversion scripts)
# ============================================================================

def get_synthesis_step_index(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get step IRIs, labels, (optional) order, and type for a synthesis.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

    SELECT DISTINCT ?step ?stepLabel ?order ?stepType
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        OPTIONAL {{ ?step ontosyn:hasOrder ?order }}
        OPTIONAL {{
            ?step rdf:type ?stepType .
            FILTER(STRSTARTS(STR(?stepType), "https://www.theworldavatar.com/kg/OntoSyn/"))
        }}
    }}
    ORDER BY ?order ?stepLabel
    """
    results = execute_sparql(query)
    for r in results:
        if r.get("stepType"):
            r["stepType"] = r["stepType"].split("/")[-1]
    return results


def get_synthesis_step_temperatures(
    synthesis_name: str,
    temperature_kind: Literal["target", "crystallization", "any"] = "any",
    order: OrderOption = "asc",
) -> List[Dict[str, Any]]:
    """
    Get step-level temperatures with an explicit kind selector and optional ordering.

    Args:
        temperature_kind: "target", "crystallization", or "any"
        order: "asc", "desc", or "none" (orders by tempValue)
    """
    order_clause = _order_by_clause(order, "?tempValue")

    if temperature_kind not in ("target", "crystallization", "any"):
        raise ValueError("temperature_kind must be one of: target, crystallization, any")

    # Select which predicate(s) to match
    if temperature_kind == "target":
        temp_block = "?step ontosyn:hasTargetTemperature ?temp ."
        kind_bind = 'BIND("target" as ?temperatureKind)'
    elif temperature_kind == "crystallization":
        temp_block = "?step ontosyn:hasCrystallizationTargetTemperature ?temp ."
        kind_bind = 'BIND("crystallization" as ?temperatureKind)'
    else:
        temp_block = """
        {
            ?step ontosyn:hasTargetTemperature ?temp .
            BIND("target" as ?temperatureKind)
        } UNION {
            ?step ontosyn:hasCrystallizationTargetTemperature ?temp .
            BIND("crystallization" as ?temperatureKind)
        }
        """
        kind_bind = ""

    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?temperatureKind ?tempValue ?tempUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        {temp_block}
        {kind_bind}
        ?temp om-2:hasNumericalValue ?tempValue .
        OPTIONAL {{ ?temp om-2:hasUnit ?tempUnit }}
    }}
    {order_clause}
    """
    results = execute_sparql(query)
    for result in results:
        if 'tempUnit' in result and result['tempUnit']:
            result['tempUnit'] = result['tempUnit'].split('/')[-1]
    return results


def get_synthesis_step_temperature_rates(
    synthesis_name: str,
    order: OrderOption = "asc",
) -> List[Dict[str, Any]]:
    """
    Get step-level heating/cooling rates (ontosyn:hasTemperatureRate) with optional ordering by numerical value.
    """
    order_clause = _order_by_clause(order, "?rateValue")
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?rateValue ?rateUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        ?step ontosyn:hasTemperatureRate ?rate .
        ?rate om-2:hasNumericalValue ?rateValue .
        OPTIONAL {{ ?rate om-2:hasUnit ?rateUnit }}
    }}
    {order_clause}
    """
    results = execute_sparql(query)
    for result in results:
        if 'rateUnit' in result and result['rateUnit']:
            result['rateUnit'] = result['rateUnit'].split('/')[-1]
    return results


def get_synthesis_step_transferred_amounts(
    synthesis_name: str,
    order: OrderOption = "asc",
) -> List[Dict[str, Any]]:
    """
    Get step-level transferred amounts (ontosyn:hasTransferedAmount) with optional ordering by numerical value.
    """
    order_clause = _order_by_clause(order, "?amountValue")
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?amountValue ?amountUnit
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        ?step ontosyn:hasTransferedAmount ?amount .
        OPTIONAL {{ ?amount om-2:hasNumericalValue ?amountValue }}
        OPTIONAL {{ ?amount om-2:hasUnit ?amountUnit }}
    }}
    {order_clause}
    """
    results = execute_sparql(query)
    for result in results:
        if 'amountUnit' in result and result['amountUnit']:
            result['amountUnit'] = result['amountUnit'].split('/')[-1]
    return results


def get_synthesis_step_vessels(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get per-step vessel name/type/environment where available.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?vesselName ?vesselTypeLabel ?vesselEnvironment
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        OPTIONAL {{
            ?step ontosyn:hasVessel ?vessel .
            OPTIONAL {{ ?vessel rdfs:label ?vesselName }}
            OPTIONAL {{
                ?vessel ontosyn:hasVesselType ?vesselType .
                ?vesselType rdfs:label ?vesselTypeLabel .
            }}
        }}
        OPTIONAL {{
            ?step ontosyn:hasVesselEnvironment ?env .
            ?env rdfs:label ?vesselEnvironment .
        }}
    }}
    ORDER BY ?stepLabel
    """
    return execute_sparql(query)


# ============================================================================
# Characterisation (OntoSpecies) - Overview + Atomic queries
# Mirrors scripts/output_conversion_ttl_to_json/ontosynthesis_characterisation_conversion.py
# ============================================================================

def list_characterisation_species(
    limit: int = 100,
    order: Literal["asc", "desc", "none"] = "asc",
) -> List[Dict[str, Any]]:
    """
    List OntoSpecies:Species that have any characterisation-related triples.

    Returns:
        species IRI, label, optional CCDC value
    """
    order_clause = _order_by_clause(order, "?speciesLabel")
    query = f"""
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?species ?speciesLabel ?ccdcVal
    WHERE {{
        ?species a ontospecies:Species .
        OPTIONAL {{ ?species rdfs:label ?speciesLabel }}
        OPTIONAL {{
            ?species ontospecies:hasCCDCNumber ?ccdc .
            OPTIONAL {{ ?ccdc ontospecies:hasCCDCNumberValue ?ccdcVal }}
        }}
        # Any of these implies characterisation presence
        FILTER EXISTS {{
            ?species ontospecies:hasCharacterizationSession ?cs .
        }}
    }}
    {order_clause}
    LIMIT {limit}
    """
    return execute_sparql(query)


def list_syntheses_with_characterisation(
    limit: int = 100,
    order: Literal["asc", "desc", "none"] = "asc",
) -> List[Dict[str, Any]]:
    """
    List syntheses that have an OntoSpecies:Species output with a characterisation session.
    """
    order_clause = _order_by_clause(order, "?synthesisLabel")
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?synthesis ?synthesisLabel ?speciesLabel ?ccdcVal
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        OPTIONAL {{ ?synthesis rdfs:label ?synthesisLabel }}
        ?synthesis ontosyn:hasChemicalOutput ?species .
        ?species a ontospecies:Species .
        OPTIONAL {{ ?species rdfs:label ?speciesLabel }}
        OPTIONAL {{
            ?species ontospecies:hasCCDCNumber ?ccdc .
            OPTIONAL {{ ?ccdc ontospecies:hasCCDCNumberValue ?ccdcVal }}
        }}
        ?species ontospecies:hasCharacterizationSession ?cs .
    }}
    {order_clause}
    LIMIT {limit}
    """
    return execute_sparql(query)


def list_characterisation_devices(limit: int = 100) -> List[Dict[str, Any]]:
    """
    List characterisation devices found under OntoSpecies CharacterizationSession.
    """
    query = f"""
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?deviceType ?deviceName ?frequency
    WHERE {{
        ?species ontospecies:hasCharacterizationSession ?cs .
        {{
            ?cs ontospecies:hasHNMRDevice ?device .
            BIND("HNMRDevice" as ?deviceType)
            OPTIONAL {{ ?device ontospecies:hasFrequency ?frequency }}
        }} UNION {{
            ?cs ontospecies:hasElementalAnalysisDevice ?device .
            BIND("ElementalAnalysisDevice" as ?deviceType)
        }} UNION {{
            ?cs ontospecies:hasInfraredSpectroscopyDevice ?device .
            BIND("InfraredSpectroscopyDevice" as ?deviceType)
        }}
        OPTIONAL {{ ?device rdfs:label ?deviceName }}
    }}
    ORDER BY ?deviceType ?deviceName
    LIMIT {limit}
    """
    return execute_sparql(query)


def get_characterisation_for_synthesis(
    synthesis_name: str,
    order: Literal["asc", "desc", "none"] = "none",
    order_by: Literal["speciesLabel", "ccdcVal", "wpExp", "wpCalc"] = "speciesLabel",
) -> List[Dict[str, Any]]:
    """
    Atomic: characterisation summary per OntoSpecies:Species output of a synthesis.

    Includes:
    - species label, ccdc value
    - molecular formula (value/label)
    - elemental analysis exp/calc
    - IR bands + IR material name (if any)
    - HNMR label placeholder (if any)

    Ordering:
        order: asc|desc|none
        order_by: speciesLabel|ccdcVal|wpExp|wpCalc
    """
    order_var_map = {
        "speciesLabel": "?speciesLabel",
        "ccdcVal": "?ccdcVal",
        "wpExp": "?wpExp",
        "wpCalc": "?wpCalc",
    }
    order_clause = _order_by_clause(order, order_var_map[order_by])

    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT
      ?speciesLabel
      ?ccdcVal
      ?molecularFormula
      ?wpExp
      ?wpCalc
      ?irBands
      ?irMaterial
      ?hnmrLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasChemicalOutput ?species .
      ?species a ontospecies:Species .

      OPTIONAL {{ ?species rdfs:label ?speciesLabel }}

      OPTIONAL {{
        ?species ontospecies:hasCCDCNumber ?ccdc .
        OPTIONAL {{ ?ccdc ontospecies:hasCCDCNumberValue ?ccdcVal }}
      }}

      OPTIONAL {{
        ?species ontospecies:hasMolecularFormula ?f .
        OPTIONAL {{ ?f ontospecies:hasMolecularFormulaValue ?mfVal }}
        OPTIONAL {{ ?f rdfs:label ?mfLabel }}
        BIND(COALESCE(?mfVal, ?mfLabel) AS ?molecularFormula)
      }}

      OPTIONAL {{
        ?species ontospecies:hasElementalAnalysisData ?ead .
        OPTIONAL {{
          ?ead ontospecies:hasWeightPercentageExperimental ?wpe .
          OPTIONAL {{ ?wpe ontospecies:hasWeightPercentageExperimentalValue ?wpExp }}
        }}
        OPTIONAL {{
          ?ead ontospecies:hasWeightPercentageCalculated ?wpc .
          OPTIONAL {{ ?wpc ontospecies:hasWeightPercentageCalculatedValue ?wpCalc }}
        }}
      }}

      OPTIONAL {{
        ?species ontospecies:hasInfraredSpectroscopyData ?ir .
        OPTIONAL {{ ?ir ontospecies:hasBands ?irBands }}
        OPTIONAL {{
          ?ir ontospecies:usesMaterial ?mat .
          OPTIONAL {{ ?mat ontospecies:hasMaterialName ?irMaterial }}
        }}
      }}

      OPTIONAL {{
        ?species ontospecies:hasHNMRData ?n .
        OPTIONAL {{ ?n rdfs:label ?hnmrLabel }}
      }}
    }}
    {order_clause}
    """
    return execute_sparql(query)


def get_characterisation_by_ccdc(
    ccdc_number: str,
    order: Literal["asc", "desc", "none"] = "none",
) -> List[Dict[str, Any]]:
    """
    Atomic: characterisation summary for a species identified by CCDC number.
    """
    order_clause = _order_by_clause(order, "?speciesLabel")
    query = f"""
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?speciesLabel ?ccdcVal ?molecularFormula ?wpExp ?wpCalc ?irBands ?irMaterial ?hnmrLabel
    WHERE {{
      ?species a ontospecies:Species .
      ?species ontospecies:hasCCDCNumber ?ccdc .
      ?ccdc ontospecies:hasCCDCNumberValue ?ccdcVal .
      FILTER(STR(?ccdcVal) = "{ccdc_number}")

      OPTIONAL {{ ?species rdfs:label ?speciesLabel }}

      OPTIONAL {{
        ?species ontospecies:hasMolecularFormula ?f .
        OPTIONAL {{ ?f ontospecies:hasMolecularFormulaValue ?mfVal }}
        OPTIONAL {{ ?f rdfs:label ?mfLabel }}
        BIND(COALESCE(?mfVal, ?mfLabel) AS ?molecularFormula)
      }}

      OPTIONAL {{
        ?species ontospecies:hasElementalAnalysisData ?ead .
        OPTIONAL {{
          ?ead ontospecies:hasWeightPercentageExperimental ?wpe .
          OPTIONAL {{ ?wpe ontospecies:hasWeightPercentageExperimentalValue ?wpExp }}
        }}
        OPTIONAL {{
          ?ead ontospecies:hasWeightPercentageCalculated ?wpc .
          OPTIONAL {{ ?wpc ontospecies:hasWeightPercentageCalculatedValue ?wpCalc }}
        }}
      }}

      OPTIONAL {{
        ?species ontospecies:hasInfraredSpectroscopyData ?ir .
        OPTIONAL {{ ?ir ontospecies:hasBands ?irBands }}
        OPTIONAL {{
          ?ir ontospecies:usesMaterial ?mat .
          OPTIONAL {{ ?mat ontospecies:hasMaterialName ?irMaterial }}
        }}
      }}

      OPTIONAL {{
        ?species ontospecies:hasHNMRData ?n .
        OPTIONAL {{ ?n rdfs:label ?hnmrLabel }}
      }}
    }}
    {order_clause}
    """
    return execute_sparql(query)


def get_common_ir_materials(
    limit: int = 20,
    order: Literal["asc", "desc", "none"] = "desc",
) -> List[Dict[str, Any]]:
    """
    Corpus-level: most common materials used for Infrared (IR) spectroscopy.

    This follows the OntoSpecies path used in the conversion code:
      Species -> hasInfraredSpectroscopyData -> usesMaterial -> hasMaterialName

    Returns:
        materialName, usageCount
    """
    order_clause = _order_by_clause(order, "?usageCount")
    query = f"""
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>

    SELECT DISTINCT ?materialName (COUNT(DISTINCT ?species) as ?usageCount)
    WHERE {{
        ?species a ontospecies:Species .
        ?species ontospecies:hasInfraredSpectroscopyData ?ir .
        ?ir ontospecies:usesMaterial ?mat .
        ?mat ontospecies:hasMaterialName ?materialName .
        FILTER(STRLEN(STR(?materialName)) > 0)
    }}
    GROUP BY ?materialName
    {order_clause}
    LIMIT {limit}
    """
    return execute_sparql(query)


# ============================================================================
# OntoSyn missing-but-useful: provenance, yield, equipment, parameters, etc.
# ============================================================================

def get_synthesis_document_context(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get DocumentContext (section/anchor) for a synthesis if present.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?contextLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasDocumentContext ?ctx .
      OPTIONAL {{ ?ctx rdfs:label ?contextLabel }}
    }}
    """
    return execute_sparql(query)


def get_synthesis_inheritance(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get procedure inheritance links for a synthesis (both directions).
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?relation ?otherSynthesisLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .

      {{
        ?synthesis ontosyn:inheritsFromProcedure ?other .
        ?other rdfs:label ?otherSynthesisLabel .
        BIND("inherits_from" as ?relation)
      }}
      UNION
      {{
        ?child ontosyn:inheritsFromProcedure ?synthesis .
        ?child rdfs:label ?otherSynthesisLabel .
        BIND("inherited_by" as ?relation)
      }}
    }}
    ORDER BY ?relation ?otherSynthesisLabel
    """
    return execute_sparql(query)


def get_synthesis_yield(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get yield for a synthesis if present.

    Data may attach yield either:
    - on ChemicalSynthesis via ontosyn:hasYield
    - or on ChemicalOutput via ontosyn:hasYield (seen in merged TTLs)

    Returns:
      yieldValue, yieldUnit (if available)
    """
    # Accept either a synthesis label OR an output (ChemicalOutput/Species) label as the input name.
    # This matches how the conversion scripts often treat yield as belonging to ChemicalOutput.
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX qudt: <http://qudt.org/schema/qudt/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?yieldValue ?yieldUnit ?yieldLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .

      {{
        ?synthesis rdfs:label "{synthesis_name}" .
      }}
      UNION
      {{
        ?synthesis ontosyn:hasChemicalOutput ?out_match .
        ?out_match rdfs:label "{synthesis_name}" .
      }}

      {{
        ?synthesis ontosyn:hasYield ?y .
      }}
      UNION
      {{
        ?synthesis ontosyn:hasChemicalOutput ?out .
        ?out ontosyn:hasYield ?y .
      }}

      OPTIONAL {{ ?y om-2:hasNumericalValue ?yieldValue }}
      OPTIONAL {{ ?y qudt:numericValue ?yieldValue }}
      OPTIONAL {{ ?y om-2:hasUnit ?yieldUnit }}
      OPTIONAL {{ ?y rdfs:label ?yieldLabel }}
    }}
    """
    results = execute_sparql(query)

    # Drop empty placeholders (e.g. yieldLabel = "N/A" with no value)
    cleaned: List[Dict[str, Any]] = []
    for r in results:
        yv = (r.get("yieldValue") or "").strip() if r.get("yieldValue") is not None else ""
        yl = (r.get("yieldLabel") or "").strip() if r.get("yieldLabel") is not None else ""
        if yv:
            cleaned.append(r)
        elif yl and yl.upper() not in ("N/A", "NA"):
            cleaned.append(r)
    results = cleaned

    for r in results:
        if r.get("yieldUnit"):
            r["yieldUnit"] = r["yieldUnit"].split("/")[-1]
    return results


def get_synthesis_equipment(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get process equipment used in a synthesis:
    - ChemicalSynthesis ontosyn:hasEquipment
    - SynthesisStep ontosyn:usesEquipment
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?scope ?stepLabel ?equipmentLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .

      {{
        ?synthesis ontosyn:hasEquipment ?eq .
        OPTIONAL {{ ?eq rdfs:label ?equipmentLabel }}
        BIND("synthesis" as ?scope)
        BIND("" as ?stepLabel)
      }}
      UNION
      {{
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?stepLabel .
        ?step ontosyn:usesEquipment ?eq .
        OPTIONAL {{ ?eq rdfs:label ?equipmentLabel }}
        BIND("step" as ?scope)
      }}
    }}
    ORDER BY ?scope ?stepLabel ?equipmentLabel
    """
    return execute_sparql(query)


def get_synthesis_step_parameters(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get free-text key/value parameters stored on steps via ontosyn:hasParameter.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?parameter
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasSynthesisStep ?step .
      ?step rdfs:label ?stepLabel .
      ?step ontosyn:hasParameter ?parameter .
    }}
    ORDER BY ?stepLabel
    """
    return execute_sparql(query)


def get_synthesis_step_vessel_environments(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get atmosphere/vessel environment per step, when explicitly stated.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?environment
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasSynthesisStep ?step .
      ?step rdfs:label ?stepLabel .
      ?step ontosyn:hasVesselEnvironment ?env .
      OPTIONAL {{ ?env rdfs:label ?environment }}
    }}
    ORDER BY ?stepLabel
    """
    return execute_sparql(query)


def get_synthesis_drying_conditions(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get Dry step conditions: temperature/pressure + drying agent when present.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?tempValue ?tempUnit ?pressureValue ?pressureUnit ?dryingAgentLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasSynthesisStep ?step .
      ?step rdfs:label ?stepLabel .
      ?step a ontosyn:Dry .

      OPTIONAL {{
        ?step ontosyn:hasDryingTemperature ?t .
        ?t om-2:hasNumericalValue ?tempValue .
        OPTIONAL {{ ?t om-2:hasUnit ?tempUnit }}
      }}
      OPTIONAL {{
        ?step ontosyn:hasDryingPressure ?p .
        ?p om-2:hasNumericalValue ?pressureValue .
        OPTIONAL {{ ?p om-2:hasUnit ?pressureUnit }}
      }}
      OPTIONAL {{
        ?step ontosyn:hasDryingAgent ?a .
        OPTIONAL {{ ?a rdfs:label ?dryingAgentLabel }}
      }}
    }}
    ORDER BY ?stepLabel
    """
    results = execute_sparql(query)
    for r in results:
        for k in ("tempUnit", "pressureUnit"):
            if r.get(k):
                r[k] = r[k].split("/")[-1]
    return results


def get_synthesis_evaporation_conditions(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get Evaporate step conditions: temperature/pressure, target volume, removed species.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?tempValue ?tempUnit ?pressureValue ?pressureUnit ?targetVolValue ?targetVolUnit ?removedLabel
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasSynthesisStep ?step .
      ?step rdfs:label ?stepLabel .
      ?step a ontosyn:Evaporate .

      OPTIONAL {{
        ?step ontosyn:hasEvaporationTemperature ?t .
        ?t om-2:hasNumericalValue ?tempValue .
        OPTIONAL {{ ?t om-2:hasUnit ?tempUnit }}
      }}
      OPTIONAL {{
        ?step ontosyn:hasEvaporationPressure ?p .
        ?p om-2:hasNumericalValue ?pressureValue .
        OPTIONAL {{ ?p om-2:hasUnit ?pressureUnit }}
      }}
      OPTIONAL {{
        ?step ontosyn:isEvaporatedToVolume ?v .
        ?v om-2:hasNumericalValue ?targetVolValue .
        OPTIONAL {{ ?v om-2:hasUnit ?targetVolUnit }}
      }}
      OPTIONAL {{
        ?step ontosyn:removesSpecies ?rm .
        OPTIONAL {{ ?rm rdfs:label ?removedLabel }}
      }}
    }}
    ORDER BY ?stepLabel
    """
    results = execute_sparql(query)
    for r in results:
        for k in ("tempUnit", "pressureUnit", "targetVolUnit"):
            if r.get(k):
                r[k] = r[k].split("/")[-1]
    return results


def get_synthesis_separation_solvents(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get Separate step solvents (extraction/phase separation media) if present.
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?stepLabel ?solventLabel ?amount
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasSynthesisStep ?step .
      ?step rdfs:label ?stepLabel .
      ?step a ontosyn:Separate .
      ?step ontosyn:hasSeparationSolvent ?solv .
      OPTIONAL {{ ?solv rdfs:label ?solventLabel }}
      OPTIONAL {{ ?solv ontosyn:hasAmount ?amount }}
    }}
    ORDER BY ?stepLabel ?solventLabel
    """
    return execute_sparql(query)


def get_hnmr_for_synthesis(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Atomic: HNMR details for species outputs of a synthesis (if present).
    Uses OntoSpecies HNMRData structure (hasShifts, usesSolvent, hasTemperature).
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?speciesLabel ?shifts ?solventName ?temperature
    WHERE {{
      ?synthesis a ontosyn:ChemicalSynthesis .
      ?synthesis rdfs:label "{synthesis_name}" .
      ?synthesis ontosyn:hasChemicalOutput ?species .
      ?species a ontospecies:Species .
      OPTIONAL {{ ?species rdfs:label ?speciesLabel }}

      ?species ontospecies:hasHNMRData ?nmr .
      OPTIONAL {{ ?nmr ontospecies:hasShifts ?shifts }}
      OPTIONAL {{ ?nmr ontospecies:hasTemperature ?temperature }}
      OPTIONAL {{
        ?nmr ontospecies:usesSolvent ?solv .
        OPTIONAL {{ ?solv ontospecies:hasSolventName ?solventName }}
      }}
    }}
    ORDER BY ?speciesLabel
    """
    return execute_sparql(query)


def get_common_hnmr_solvents(limit: int = 20, order: Literal["asc", "desc", "none"] = "desc") -> List[Dict[str, Any]]:
    """
    Corpus-level: most common solvents used for HNMR measurements.
    """
    order_clause = _order_by_clause(order, "?usageCount")
    query = f"""
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>

    SELECT DISTINCT ?solventName (COUNT(DISTINCT ?species) as ?usageCount)
    WHERE {{
      ?species a ontospecies:Species .
      ?species ontospecies:hasHNMRData ?nmr .
      ?nmr ontospecies:usesSolvent ?solv .
      ?solv ontospecies:hasSolventName ?solventName .
      FILTER(STRLEN(STR(?solventName)) > 0)
    }}
    GROUP BY ?solventName
    {order_clause}
    LIMIT {limit}
    """
    return execute_sparql(query)

def get_synthesis_products(synthesis_name: str) -> List[Dict[str, Any]]:
    """
    Get MOP products from a synthesis (Synthesis → Output → MOP).
    
    Args:
        synthesis_name: Name of synthesis procedure
        
    Returns:
        List of MOP products with CCDC numbers and formulas
        
    Example:
        >>> products = get_synthesis_products("VMOP-17")
        >>> for prod in products:
        ...     print(f"{prod['mopLabel']}: CCDC {prod.get('ccdcNumber', 'N/A')}")
    """
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?mopLabel ?ccdcNumber ?mopFormula
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label "{synthesis_name}" .
        ?synthesis ontosyn:hasChemicalOutput ?output .
        ?output ontosyn:isRepresentedBy ?mop .
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label ?mopLabel .
        OPTIONAL {{ ?mop ontomops:hasCCDCNumber ?ccdcNumber }}
        OPTIONAL {{ ?mop ontomops:hasMOPFormula ?mopFormula }}
    }}
    """
    return execute_sparql(query)

# ============================================================================
# MOP Query Functions
# ============================================================================

def get_mop_building_units(mop_name: str) -> List[Dict[str, Any]]:
    """
    Get chemical building units for a specific MOP.
    
    Args:
        mop_name: Name of the MOP
        
    Returns:
        List of CBUs with formulas, names, and alternative names
        
    Example:
        >>> cbus = get_mop_building_units("CIAC-105")
        >>> for cbu in cbus:
        ...     print(f"{cbu.get('cbuFormula', 'N/A')}: {cbu.get('cbuName', 'N/A')}")
    """
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?cbuFormula ?cbuName ?altName
    WHERE {{
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label "{mop_name}" .
        ?mop ontomops:hasChemicalBuildingUnit ?cbu .
        OPTIONAL {{ ?cbu ontomops:hasCBUFormula ?cbuFormula }}
        OPTIONAL {{ ?cbu rdfs:label ?cbuName }}
        OPTIONAL {{ ?cbu ontosyn:hasAlternativeNames ?altName }}
    }}
    """
    return execute_sparql(query)

# ============================================================================
# Corpus-Wide Query Functions
# ============================================================================

def get_common_chemicals(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get most commonly used chemicals across all syntheses.
    
    Args:
        limit: Maximum number of chemicals to return
        
    Returns:
        List of chemicals with usage counts
        
    Example:
        >>> chemicals = get_common_chemicals(limit=10)
        >>> for chem in chemicals:
        ...     print(f"{chem['chemicalLabel']}: used {chem['usageCount']} times")
    """
    query = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?chemicalLabel (COUNT(?synthesis) as ?usageCount)
    WHERE {{
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis ontosyn:hasChemicalInput ?chemical .
        ?chemical rdfs:label ?chemicalLabel .
    }}
    GROUP BY ?chemicalLabel
    ORDER BY DESC(?usageCount)
    LIMIT {limit}
    """
    return execute_sparql(query)

# ============================================================================
# Example Usage / Testing
# ============================================================================

if __name__ == "__main__":
    import sys
    
    print("="*80)
    print("MOPs Knowledge Graph Operations - Quick Test")
    print("="*80)
    
    # Load KG
    try:
        repo_root = Path(__file__).resolve().parents[2]
        data_path = repo_root / "evaluation" / "data" / "merged_tll"
        
        if not data_path.exists():
            print(f"❌ Data path not found: {data_path}")
            print("Please run: python scripts/merge_and_conversion_main.py")
            sys.exit(1)
        
        print(f"\n📚 Loading knowledge graph from: {data_path}")
        # Use ensure_kg_loaded to properly load the graph
        kg = ensure_kg_loaded()
        print(f"✓ Loaded {len(kg)} triples")
        
        # Test 1: Statistics
        print("\n" + "="*80)
        print("TEST 1: Get Statistics")
        print("="*80)
        stats = get_kg_statistics()
        for key, value in stats.items():
            print(f"  {key}: {value}")
        
        # Test 2: List MOPs
        print("\n" + "="*80)
        print("TEST 2: List MOPs (first 5)")
        print("="*80)
        mops = get_all_mops(limit=5)
        for mop in mops:
            print(f"  {mop['mopLabel']}: CCDC {mop.get('ccdcNumber', 'N/A')}")
        
        # Test 3: Lookup synthesis
        print("\n" + "="*80)
        print("TEST 3: Lookup Synthesis (UMC-1)")
        print("="*80)
        results = lookup_synthesis_iri("UMC-1")
        if results:
            print(f"  Found: {results[0].get('synthesisLabel', 'N/A')}")
            print(f"  IRI: {results[0].get('synthesis', 'N/A')[:80]}...")
        else:
            print("  Not found")
        
        # Test 4: Get synthesis recipe
        print("\n" + "="*80)
        print("TEST 4: Get Synthesis Recipe (UMC-1)")
        print("="*80)
        recipe = get_synthesis_recipe("UMC-1")
        for i, chem in enumerate(recipe[:3], 1):
            print(f"  {i}. {chem.get('chemicalLabel', 'N/A')}: {chem.get('amount', 'N/A')}")
        if len(recipe) > 3:
            print(f"  ... and {len(recipe) - 3} more chemicals")
        
        # Test 5: Get common chemicals
        print("\n" + "="*80)
        print("TEST 5: Common Chemicals (top 5)")
        print("="*80)
        chemicals = get_common_chemicals(limit=5)
        for chem in chemicals:
            print(f"  {chem['chemicalLabel']}: used {chem['usageCount']} times")
        
        print("\n" + "="*80)
        print("✅ All tests completed successfully!")
        print("="*80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

