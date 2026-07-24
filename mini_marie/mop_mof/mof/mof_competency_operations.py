"""
Competency-question operations for OntoMOFs (human-made questions in data/CompetencyQs.md).

Uses hasNames for MOF identity (UiO-66, ZIF-8, HKUST-1, etc.) and corpus-wide filters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.mop_mof.mof.mof_operations import (
    MOFS_PREFIX,
    RESULT_LIMIT,
    _escape_literal,
    execute_sparql,
    format_results_as_tsv,
)

COMPETENCY_QUERIES_DIR = Path(__file__).resolve().parent / "queries" / "competency"
COMPETENCY_RESULT_LIMIT = 10
COMPETENCY_COUNT_LIMIT = 1
DEFAULT_ONLINE_PROBE_LIMIT = 10
DEFAULT_OFFLINE_CAP = 500_000


def _limit_suffix(limit: Optional[int] = None) -> str:
    """SPARQL LIMIT clause; empty string means uncapped (full cache warm only)."""
    if limit is None:
        return ""
    return f"\nLIMIT {int(limit)}"


def _result_limit(limit: Optional[int] = None) -> int:
    if limit is not None:
        return int(limit)
    return COMPETENCY_RESULT_LIMIT


def _name_filter_exact(name: str, var: str = "?name") -> str:
    n = _escape_literal(name.strip().lower())
    return f'LCASE(STR({var})) = "{n}"'


def _name_filter_contains(name: str, var: str = "?name") -> str:
    n = _escape_literal(name.strip().lower())
    return f'CONTAINS(LCASE(STR({var})), "{n}")'


def _metal_filter(metal: str) -> str:
    m = _escape_literal(metal.strip().lower())
    return f"""(
    (BOUND(?metal) && CONTAINS(LCASE(STR(?metal)), "{m}")) ||
    (BOUND(?node) && CONTAINS(LCASE(STR(?node)), "{m}"))
  )"""


def get_pld_stats_by_mof_name(mof_name: str) -> List[Dict[str, Any]]:
    """Average and variance of PLD for MOFs matching hasNames (Q1)."""
    nf = _name_filter_exact(mof_name)
    query = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (AVG(?pld_val) AS ?avgPLD)
       ((AVG(?pld_sq) - (AVG(?pld_val) * AVG(?pld_val))) AS ?variance)
       (COUNT(?pld_val) AS ?n)
WHERE {{
  ?mof mofs:hasNames ?name ;
       mofs:hasPLD ?pld .
  BIND(xsd:float(?pld) AS ?pld_val)
  BIND(?pld_val * ?pld_val AS ?pld_sq)
  FILTER({nf})
  FILTER(?pld_val > 0)
}}
"""
    return execute_sparql(query)


def get_mofs_by_metal(
    metal: str,
    *,
    count_only: bool = False,
    experimental_only: bool = False,
    list_sources: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """MOFs containing a metal (hasMetal or hasNodeSmile); Q2/Q3."""
    exp = "?mof mofs:isExperimental true .\n  " if experimental_only else ""
    if count_only and not list_sources:
        query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {{
  {exp}?mof a mofs:MetalOrganicFramework .
  OPTIONAL {{ ?mof mofs:hasMetal ?metal }}
  OPTIONAL {{ ?mof mofs:hasNodeSmile ?node }}
  FILTER({_metal_filter(metal)})
}}
"""
        return execute_sparql(query, timeout=120)

    if list_sources:
        query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?sourcedb (COUNT(DISTINCT ?mof) AS ?count)
WHERE {{
  {exp}?mof mofs:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof mofs:hasMetal ?metal }}
  OPTIONAL {{ ?mof mofs:hasNodeSmile ?node }}
  FILTER({_metal_filter(metal)})
}}
GROUP BY ?sourcedb
ORDER BY DESC(?count)
{_limit_suffix(limit)}
"""
        return execute_sparql(query, timeout=120)

    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?mof ?sourcedb
WHERE {{
  {exp}?mof mofs:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof mofs:hasMetal ?metal }}
  OPTIONAL {{ ?mof mofs:hasNodeSmile ?node }}
  FILTER({_metal_filter(metal)})
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_synthesis_by_mof_name(
    mof_name: str,
    name_mode: str = "contains",
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Synthesis routes (method, solvent, temperature, yield, DOI) for a named MOF (Q4)."""
    nf = _name_filter_contains(mof_name) if name_mode == "contains" else _name_filter_exact(mof_name)
    query = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?name ?sourcedb ?refcode ?method ?solvent ?temp ?temp_unit ?time_val ?yield ?doi
WHERE {{
  ?MOF a mofs:MetalOrganicFramework ;
       mofs:hasNames ?name .
  OPTIONAL {{ ?MOF mofs:hasSourcedb ?sourcedb }}
  OPTIONAL {{ ?MOF mofs:hasCsdRefcode ?refcode }}
  OPTIONAL {{ ?MOF mofs:hasMethod ?method }}
  OPTIONAL {{ ?MOF mofs:hasSolvents ?solvent }}
  OPTIONAL {{ ?MOF mofs:hasTime ?time_val }}
  OPTIONAL {{ ?MOF mofs:hasYield ?yield }}
  OPTIONAL {{ ?MOF mofs:hasReferenceDOI ?doi }}
  OPTIONAL {{ ?MOF mofs:hasTemperature ?temp ; mofs:hasTemperatureUnit ?temp_unit }}
  FILTER({nf})
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_mof_identity_by_name(mof_name: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Topology, space group, refcode, sources for a MOF name (Q5/Q6)."""
    nf = _name_filter_exact(mof_name)
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?name ?sourcedb ?topology ?space_group ?refcode ?mofid
WHERE {{
  ?mof mofs:hasNames ?name .
  OPTIONAL {{ ?mof mofs:hasSourcedb ?sourcedb }}
  OPTIONAL {{ ?mof mofs:hasRCSRSym ?topology }}
  OPTIONAL {{ ?mof mofs:hasSpaceGroupNumber ?space_group }}
  OPTIONAL {{ ?mof mofs:hasCsdRefcode ?refcode }}
  OPTIONAL {{ ?mof mofs:hasMofidV1 ?mofid }}
  FILTER({nf})
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_linkers_by_mof_name(mof_name: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Linker SMILES for a named MOF (Q8)."""
    nf = _name_filter_exact(mof_name)
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?linker ?source
WHERE {{
  ?mof mofs:hasLinkerSmile ?linker ;
       mofs:hasSourcedb ?source ;
       mofs:hasNames ?name .
  FILTER({nf})
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_publications_by_mof_name(mof_name: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Reference DOIs for MOFs matching name (Q13)."""
    nf = _name_filter_contains(mof_name)
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?doi ?source ?name
WHERE {{
  ?mof mofs:hasSourcedb ?source ;
       mofs:hasNames ?name ;
       mofs:hasReferenceDOI ?doi .
  FILTER({nf})
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_mofs_with_same_topology_as(
    reference_name: str,
    *,
    count_only: bool = False,
    include_reference: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """MOFs sharing topology with a reference MOF (Q7). Two-step to avoid join timeout."""
    identity = get_mof_identity_by_name(reference_name)
    topos = sorted({str(r["topology"]) for r in identity if r.get("topology")})
    if not topos:
        return []
    topo = _escape_literal(topos[0].lower())
    nf = _name_filter_exact(reference_name)

    if count_only:
        excl = "" if include_reference else f"""
  FILTER NOT EXISTS {{
    ?mof mofs:hasNames ?refname .
    FILTER({nf.replace("?name", "?refname")})
  }}"""
        query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {{
  ?mof mofs:hasRCSRSym ?topology .
  FILTER(LCASE(STR(?topology)) = "{topo}")
  {excl}
}}
"""
        rows = execute_sparql(query, timeout=180)
        if rows:
            rows[0]["topology"] = topos[0]
        return rows

    excl = "" if include_reference else f"""
  FILTER NOT EXISTS {{
    ?mof mofs:hasNames ?refname .
    FILTER({nf.replace("?name", "?refname")})
  }}"""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mof ?source ?topology
WHERE {{
  ?mof mofs:hasSourcedb ?source ; mofs:hasRCSRSym ?topology .
  FILTER(LCASE(STR(?topology)) = "{topo}")
  {excl}
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=90)


def count_hypothetical_same_topology_as(reference_name: str) -> List[Dict[str, Any]]:
    """Count non-experimental MOFs sharing topology with named MOF (Q9)."""
    ref = _escape_literal(reference_name.strip().lower())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {{
  ?mil mofs:hasNames ?name ; mofs:hasRCSRSym ?topo .
  FILTER(LCASE(STR(?name)) = "{ref}")
  ?mof mofs:hasRCSRSym ?topo ; mofs:isExperimental false .
}}
"""
    return execute_sparql(query)


def count_experimental_by_topology(topology: str) -> List[Dict[str, Any]]:
    """Count experimental MOFs with given RCSR topology (Q10)."""
    topo = _escape_literal(topology.strip().lower())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {{
  ?mof mofs:isExperimental true ;
       mofs:hasRCSRSym ?topo .
  FILTER(LCASE(STR(?topo)) = "{topo}")
}}
"""
    return execute_sparql(query)


def get_pore_metrics_by_mof_name(mof_name: str) -> List[Dict[str, Any]]:
    """Max LCD and aggregate PLD/density for a MOF name (Q11/Q15)."""
    key = mof_name.strip().lower()
    if key == "hkust-1":
        name_filter = '(LCASE(STR(?name)) = "hkust-1" || CONTAINS(LCASE(STR(?name)), "cu-btc"))'
    else:
        name_filter = _name_filter_exact(mof_name)
    query = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (MAX(?lcd) AS ?maxLCD)
       (AVG(?pld_val) AS ?avgPLD)
       (AVG(?density) AS ?avgDensity)
       (COUNT(?mof) AS ?n)
WHERE {{
  ?mof mofs:hasNames ?name .
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
  OPTIONAL {{ ?mof mofs:hasPLD ?pld . BIND(xsd:float(?pld) AS ?pld_val) }}
  OPTIONAL {{ ?mof mofs:hasDensity ?density }}
  FILTER({name_filter})
}}
"""
    return execute_sparql(query)


def get_asa_by_mof_name(mof_name: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Accessible surface area values per source (Q14)."""
    nf = _name_filter_contains(mof_name)
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?name ?source ?asa
WHERE {{
  ?mof mofs:hasSourcedb ?source ;
       mofs:hasNames ?name ;
       mofs:hasASA ?asa .
  FILTER({nf})
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_mofs_by_gsa_min(min_gsa: float = 2500, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """MOFs with geometric surface area >= threshold (Q12)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mof ?gsa ?source
WHERE {{
  ?mof mofs:hasSourcedb ?source ; mofs:hasGSA ?gsa .
  FILTER(?gsa >= {float(min_gsa)})
}}
ORDER BY DESC(?gsa)
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_mofs_by_lcd_max(max_lcd_angstrom: float = 20, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """MOFs with LCD below threshold in Angstrom (Q16; doc uses <20 for ~2nm)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mof ?lcd ?source
WHERE {{
  ?mof mofs:hasSourcedb ?source ; mofs:hasLCD ?lcd .
  FILTER(?lcd < {float(max_lcd_angstrom)} && ?lcd > 0)
}}
ORDER BY ?lcd
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_top_synthesis_solvents(
    exclude: str = "DMF,DMA,HF",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Most common synthesis solvents excluding DMF/DMA/HF (Q17)."""
    filters = []
    for token in exclude.split(","):
        t = token.strip().upper()
        if t:
            filters.append(f'!CONTAINS(?solvent, "{t}")')
    filt = " && ".join(filters) if filters else "true"
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?solvent (COUNT(?solvent) AS ?count)
WHERE {{
  ?mof mofs:hasSolvents ?solvent .
  FILTER({filt})
}}
GROUP BY ?solvent
ORDER BY DESC(?count)
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_mofs_by_metal_and_linker(
    metal_fragment: str = "zr",
    linker_fragment: str = "c(=o)o",
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """MOFs with Zr node and carboxylate-like linker (David Q1)."""
    node_f = _escape_literal(metal_fragment.strip().lower())
    link_f = _escape_literal(linker_fragment.strip().lower())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?mof ?node ?linker ?sourcedb
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasNodeSmile ?node ;
       mofs:hasLinkerSmile ?linker ;
       mofs:hasSourcedb ?sourcedb .
  FILTER (
    CONTAINS(LCASE(STR(?node)), "{node_f}") &&
    CONTAINS(LCASE(STR(?linker)), "{link_f}")
  )
}}
ORDER BY ?mof
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_mofs_by_topology_all_sources(topology: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """MOFs with topology across all source DBs (David Q2)."""
    topo = _escape_literal(topology.strip().lower())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?metal ?linker ?topology ?sourcedb
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof mofs:hasMofidV1 ?mofid }}
  OPTIONAL {{ ?mof mofs:hasNodeSmile ?metal }}
  OPTIONAL {{ ?mof mofs:hasLinkerSmile ?linker }}
  OPTIONAL {{ ?mof mofs:hasRCSRSym ?topology }}
  FILTER(CONTAINS(LCASE(STR(?topology)), "{topo}"))
}}
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_refcodes_by_mof_name(mof_name: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """CSD refcodes for a MOF name (David Q3 step 1)."""
    nf = _name_filter_exact(mof_name)
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?refcode ?sourcedb
WHERE {{
  ?mof mofs:hasNames ?name ; mofs:hasCsdRefcode ?refcode .
  OPTIONAL {{ ?mof mofs:hasSourcedb ?sourcedb }}
  FILTER({nf})
}}
ORDER BY ?refcode
{_limit_suffix(limit)}
"""
    return execute_sparql(query)


def get_water_stable_mofs(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """MOFs with predicted/experimental water stability signals (David Q5)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?mof ?name ?water_pred ?burtch ?sourcedb
WHERE {{
  ?mof a mofs:MetalOrganicFramework ; mofs:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof mofs:hasNames ?name }}
  OPTIONAL {{ ?mof mofs:hasPredictedWaterStability ?water_pred }}
  OPTIONAL {{ ?mof mofs:hasBurtchLabel ?burtch }}
  FILTER (
    (BOUND(?water_pred) && ?water_pred > 0.5) ||
    (BOUND(?burtch) && ?burtch >= 3)
  )
}}
ORDER BY DESC(?water_pred)
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_thermal_stable_mofs(
    min_thermal: float = 400,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Experimental thermal stability above threshold (David Q6)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?mof ?refcode ?thermal ?sourcedb ?doi
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasExperimentalThermalStability ?thermal ;
       mofs:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof mofs:hasCsdRefcode ?refcode }}
  OPTIONAL {{ ?mof mofs:hasReferenceDOI ?doi }}
  FILTER(?thermal > {float(min_thermal)})
}}
ORDER BY DESC(?thermal)
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_aqueous_low_temp_syntheses(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Aqueous synthesis without DMF/DMA, 20-100 C (David Q4)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?mof ?temperature ?temperatureUnit ?solvents ?method ?sourcedb
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasSourcedb ?sourcedb ;
       mofs:hasSolvents ?solvents ;
       mofs:hasTemperature ?temperature .
  FILTER (?sourcedb IN ("Park_Syn", "SynMOF", "CSD MOF Collection"))
  OPTIONAL {{ ?mof mofs:hasTemperatureUnit ?temperatureUnit }}
  OPTIONAL {{ ?mof mofs:hasMethod ?method }}
  FILTER (
    (
      (STR(?temperatureUnit) = "Kelvin" && ?temperature > 293 && ?temperature < 373) ||
      (STR(?temperatureUnit) = "Celsius" && ?temperature > 20 && ?temperature < 100)
    ) &&
    (
      CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "water") ||
      CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "h2o")
    ) &&
    !CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "dmf") &&
    !CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "dma")
  )
}}
ORDER BY ?temperature
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def get_nist_exp_adsorption_rows(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """NIST experimental isotherm rows (More Questions #3 — adsorption pool)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?nist_mof ?name (LCASE(STR(?name)) AS ?name_lc)
       ?uptake ?temperature ?pressure
WHERE {{
  ?nist_mof mofs:hasSourcedb "Nist Experimental Isotherms" ;
            mofs:hasNames ?name ;
            mofs:hasExpAdsorptionUptake ?uptake ;
            mofs:hasExpAdsorptionTemperature ?temperature ;
            mofs:hasExpAdsorptionPressure ?pressure .
}}
ORDER BY ?name_lc ?nist_mof
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=600)


def get_core_name_chemistry_rows(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """CoRE 2019/2025 name + chemistry rows for name join (More Questions #3)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?core_mof ?core_name (LCASE(STR(?core_name)) AS ?name_lc)
       ?core_source ?refcode ?mofid ?metal ?node ?linker
WHERE {{
  ?core_mof mofs:hasNames ?core_name ;
            mofs:hasSourcedb ?core_source .
  FILTER(?core_source IN ("CoRE MOF 2019", "CoRE MOF 2025"))
  OPTIONAL {{ ?core_mof mofs:hasCsdRefcode ?refcode . }}
  OPTIONAL {{ ?core_mof mofs:hasMofidV1 ?mofid . }}
  OPTIONAL {{ ?core_mof mofs:hasMetal ?metal . }}
  OPTIONAL {{ ?core_mof mofs:hasNodeSmile ?node . }}
  OPTIONAL {{ ?core_mof mofs:hasLinkerSmile ?linker . }}
}}
ORDER BY ?name_lc ?core_mof
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=600)


def get_tobassco_func_groups_by_mofid(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Tobassco MOFid → functional group (More Questions #3 optional join)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT DISTINCT ?mofid ?func_group
WHERE {{
  ?tob_mof mofs:hasSourcedb "Tobassco" ;
           mofs:hasMofidV1 ?mofid ;
           mofs:hasFunctionalGroup ?func_group .
}}
ORDER BY ?mofid
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=600)


def get_high_binary_gas_uptake_mofs(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """High CO2/N2 or CO2/H2 binary uptake from ARC_MOF (David Q7)."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?postcomb ?precomb ?source
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb ?source .
  OPTIONAL {{ ?mof mofs:hasPredAdsorptionBinaryUptake_CO2N2P09T298mmolg ?postcomb }}
  OPTIONAL {{ ?mof mofs:hasPredAdsorptionBinaryUptake_CO2H2P40T313mmolg ?precomb }}
  FILTER(
    (BOUND(?postcomb) && ?postcomb > 1.0) ||
    (BOUND(?precomb) && ?precomb > 5.0)
  )
}}
ORDER BY DESC(?postcomb)
{_limit_suffix(limit)}
"""
    return execute_sparql(query, timeout=120)


def run_competency_query_file(name: str, **params: str) -> List[Dict[str, Any]]:
    """Run a stored competency SPARQL template from queries/competency/{name}.sparql."""
    path = COMPETENCY_QUERIES_DIR / f"{name}.sparql"
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    for key, val in params.items():
        text = text.replace("{{" + key + "}}", _escape_literal(str(val)))
    if "LIMIT" not in text.upper():
        text = text.rstrip() + f"\nLIMIT {COMPETENCY_RESULT_LIMIT}\n"
    return execute_sparql(text, timeout=180)
