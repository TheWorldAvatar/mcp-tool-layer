"""Quick probe of key competency question patterns."""

from mini_marie.mop_mof.mof.mof_operations import MOFS_PREFIX, execute_sparql

P = MOFS_PREFIX


def q(label: str, sparql: str, timeout: int = 60) -> None:
    try:
        rows = execute_sparql(sparql, timeout=timeout)
        print(f"{label}: OK {len(rows)} rows", rows[:2] if rows else "")
    except Exception as e:
        print(f"{label}: FAIL {e!s:.300}")


q(
    "hasNames top",
    f"""PREFIX m: <{P}>
SELECT ?name (COUNT(?mof) AS ?c)
WHERE {{ ?mof m:hasNames ?name . }}
GROUP BY ?name ORDER BY DESC(?c) LIMIT 5""",
)

q(
    "Q1 UiO-66 PLD",
    f"""PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> PREFIX m: <{P}>
SELECT (AVG(?pld_val) AS ?avgPLD)
       ((AVG(?pld_sq) - (AVG(?pld_val)*AVG(?pld_val))) AS ?variance)
WHERE {{
  ?mof m:hasNames "UiO-66"; m:hasPLD ?pld .
  BIND(xsd:float(?pld) AS ?pld_val)
  BIND(?pld_val*?pld_val AS ?pld_sq)
  FILTER(?pld_val > 0)
}}""",
)

q(
    "Q5 DUT-67 sg",
    f"""PREFIX m: <{P}>
SELECT ?name ?sg ?source
WHERE {{
  ?mof m:hasNames ?name; m:hasSpaceGroupNumber ?sg; m:hasSourcedb ?source .
  FILTER(LCASE(STR(?name)) = "dut-67")
}} LIMIT 5""",
)

q(
    "Q6 ZIF-8 topo",
    f"""PREFIX m: <{P}>
SELECT ?topo ?source
WHERE {{
  ?mof m:hasNames ?name; m:hasSourcedb ?source; m:hasRCSRSym ?topo .
  FILTER(LCASE(STR(?name)) = "zif-8")
}} LIMIT 5""",
)

q(
    "Q10 exp pcu count",
    f"""PREFIX m: <{P}>
SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {{ ?mof m:isExperimental true ; m:hasRCSRSym "pcu" . }}""",
)

q(
    "Q2 Zn sources",
    f"""PREFIX m: <{P}>
SELECT ?sourcedb (COUNT(?mof) AS ?c)
WHERE {{
  ?mof m:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof m:hasMetal ?metal }}
  OPTIONAL {{ ?mof m:hasNodeSmile ?node }}
  FILTER(
    (BOUND(?metal) && CONTAINS(LCASE(STR(?metal)), "zn")) ||
    (BOUND(?node) && CONTAINS(LCASE(STR(?node)), "zn"))
  )
}}
GROUP BY ?sourcedb ORDER BY DESC(?c) LIMIT 10""",
    timeout=120,
)

q(
    "Q12 GSA>=2500",
    f"""PREFIX m: <{P}>
SELECT ?mof ?gsa ?source
WHERE {{ ?mof m:hasSourcedb ?source; m:hasGSA ?gsa . FILTER(?gsa >= 2500) }}
ORDER BY DESC(?gsa) LIMIT 5""",
)

q(
    "Q8 HKUST linker",
    f"""PREFIX m: <{P}>
SELECT ?linker ?source
WHERE {{
  ?mof m:hasLinkerSmile ?linker; m:hasSourcedb ?source; m:hasNames ?name .
  FILTER(LCASE(STR(?name)) = "hkust-1")
}} LIMIT 3""",
)
