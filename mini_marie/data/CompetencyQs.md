# Competency Questions

## 1. What is the average and variance pore limiting diameter of UiO-66?

```sparql
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT
  (AVG(?pld_val) AS ?avgPLD)
  (AVG(?pld_sq) AS ?meanSquare)
  ((AVG(?pld_sq) - (AVG(?pld_val) * AVG(?pld_val))) AS ?variance)
WHERE {
  ?mof mofs:hasNames "UiO-66";
       mofs:hasPLD ?pld .
  BIND(xsd:float(?pld) AS ?pld_val)
  BIND(?pld_val * ?pld_val AS ?pld_sq)
}
```

## 2. Which MOFs contain Zn and their sources?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?mof ?Sourcedb
WHERE {
  ?mof mofs:hasSourcedb ?Sourcedb .

  OPTIONAL { ?mof mofs:hasMetal ?metal . }
  OPTIONAL { ?mof mofs:hasNodeSmile ?node . }

  FILTER(
    (BOUND(?metal) && CONTAINS(LCASE(STR(?metal)), "zn")) ||
    (BOUND(?node) && CONTAINS(LCASE(STR(?node)), "zn"))
  )
}
```

## 3. How many and which experimental MOFs contain Cu?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {
  ?mof mofs:isExperimental true .

  OPTIONAL { ?mof mofs:hasMetal ?metal . }
  OPTIONAL { ?mof mofs:hasNodeSmile ?node . }

  FILTER(
    (BOUND(?metal) && CONTAINS(LCASE(?metal), "cu")) ||
    (BOUND(?node) && CONTAINS(?node, "Cu"))
  )
}
```

## 4. What are the synthesis routes for UiO-66?

```sparql
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT
  ?MOF
  ?refcode
  ?method
  ?solvent
  ?tempK
  ?tempC
  ?timeVal
  ?timeUnit
  ?yield
  ?doi
  ?metadata
WHERE {
  ?MOF a mofs:MetalOrganicFramework .

  OPTIONAL { ?MOF mofs:hasCsdRefcode ?refcode . }
  OPTIONAL { ?MOF mofs:hasReferenceDOI ?doi . }
  OPTIONAL { ?MOF mofs:hasMetadata ?metadata . }
  OPTIONAL { ?MOF mofs:hasMethod ?method . }
  OPTIONAL { ?MOF mofs:hasSolvents ?solvent . }
  OPTIONAL { ?MOF mofs:hasTime ?timeVal . }
  OPTIONAL { ?MOF mofs:hasTimeUnit ?timeUnit . }
  OPTIONAL { ?MOF mofs:hasYield ?yield . }

  OPTIONAL {
    ?MOF mofs:hasTemperature ?tempK .
    ?MOF mofs:hasTemperatureUnit "Kelvin"^^xsd:string .
  }

  OPTIONAL {
    ?MOF mofs:hasTemperature ?tempC .
    ?MOF mofs:hasTemperatureUnit "Celsius"^^xsd:string .
  }

  #FILTER: UiO-66 OR RUBTAK only
  FILTER (
    CONTAINS(LCASE(COALESCE(STR(?metadata), "")), "uio-66") ||
    CONTAINS(LCASE(COALESCE(STR(?refcode), "")), "rubtak") ||
    CONTAINS(LCASE(COALESCE(STR(?doi),"")),"uio-66")
  )
}
ORDER BY ?refcode
```

## 5. What is the space group of DUT-67??

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?csd ?name ?sg ?source 
WHERE {
  ?mof  mofs:hasSourcedb ?source;
        mofs:hasNames ?name;
        mofs:hasSpaceGroupNumber ?sg;
  		mofs:hasCsdRefcode ?csd.
  FILTER(LCASE(?name) = "dut-67")
}
```

## 6. What is the topology of ZIF-8?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?topo ?source
		(IF(COUNT(DISTINCT ?source) = 1, 1, COUNT(DISTINCT ?source)) AS ?sourceCount)
WHERE {
  ?mof mofs:hasSourcedb ?source;
       mofs:hasNames ?name .
  FILTER(LCASE(?name) = "zif-8")
   ?mof   mofs:hasRCSRSym ?topo .
}
GROUP BY ?topo ?source
```

## 7. Which MOFs have the same topology as ZIF-8?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?mof ?source
WHERE {
  ?zif mofs:hasNames "ZIF-8";
       mofs:hasRCSRSym ?topo .
  ?mof  mofs:hasSourcedb ?source;
        mofs:hasRCSRSym ?topo .
  FILTER(?mof != ?zif)
}
```

## 8. What organic linker is used in HKUST-1?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?linker ?source
WHERE {
  ?mof mofs:hasLinkerSmile ?linker ;
       mofs:hasSourcedb ?source;
       mofs:hasNames ?name .
  FILTER(LCASE(?name) = "hkust-1")
}
```

## 9. How many hypothetical MOFs share the same topology as MIL-53?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {
  ?mil mofs:hasNames ?name ;
       mofs:hasRCSRSym ?topo .

  FILTER(LCASE(?name) = "mil-53")

  ?mof mofs:hasRCSRSym ?topo ;
       mofs:isExperimental false .
}
```

## 10. How many synthesized MOFs have a pcu topology?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT (COUNT(DISTINCT ?mof) AS ?count)
WHERE {
  ?mof mofs:isExperimental true ;
       mofs:hasRCSRSym "pcu" .
}
```

## 11. What is the largest pore diameter recorded for HKUST-1?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT (MAX(?lcd) AS ?maxLCD)
WHERE {
  ?mof mofs:hasNames ?name ;
        mofs:hasLCD ?lcd .
  FILTER(
    LCASE(?name) = "hkust-1" ||
    CONTAINS(LCASE(?name), "cu-btc")
  )
}
```

## 12. Which MOFs have a surface area of of ≥2500m2 g−1?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?gsa ?source
WHERE {
  ?mof  mofs:hasSourcedb ?source;
        mofs:hasGSA ?gsa .
  FILTER(?gsa >= 2500)
}
ORDER BY DESC(?gsa)
```

## 13. What’s a publication that described MIL-101?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?doi ?source
WHERE {
  ?mof mofs:hasSourcedb ?source ;
       mofs:hasNames ?name;
       mofs:hasReferenceDOI ?doi .
  
  FILTER(CONTAINS(LCASE(?name), "mil-101"))
}
```

## 14. What is the total accessible surface area of MIL-53?

```sparql
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?name ?source ?asa
WHERE {
  ?mof mofs:hasSourcedb ?source ;
       mofs:hasNames ?name ;
       mofs:hasASA ?asa .

  FILTER(CONTAINS(LCASE(?name), "mil-53"))
}
```

## 15. What is the average density of DUT-67's?

```sparql
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT
  (AVG(?density) AS ?avgDensity)
  (MIN(?density) AS ?minDensity)
  (MAX(?density) AS ?maxDensity)
  (COUNT(?density) AS ?n)
WHERE {
  ?mof mofs:hasNames ?name;
       mofs:hasDensity ?density .
  FILTER(LCASE(?name) = "dut-67")
}
```

## 16. Which MOFs have a largest cavity diameter of less than 2nm?

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?lcd ?source
WHERE {
  ?mof  mofs:hasSourcedb ?source ;
  mofs:hasLCD ?lcd .
  FILTER(?lcd < 20)
  FILTER(?lcd > 0)
}
ORDER BY ?lcd
```

## 17. What is the most common solvent used in MOF synthesis other than DMF or DMA?

```sparql
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?solvent (COUNT(?solvent) AS ?count)
WHERE {
  ?mof mofs:hasSolvents ?solvent .
  FILTER(
    !CONTAINS(?solvent, "DMF") &&
    !CONTAINS(?solvent, "DMA") &&
    !CONTAINS(?solvent, "HF")
  )
}
GROUP BY ?solvent
ORDER BY DESC(?count)
```

# David’s Competency Questions

## 1. Which MOFs contain Zr and -COOH linker?

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?mof ?node ?linker ?sourcedb
WHERE {
  ?mof a ontomofs_vkg:MetalOrganicFramework ;
       ontomofs_vkg:hasNodeSmile ?node ;
       ontomofs_vkg:hasLinkerSmile ?linker ;
       ontomofs_vkg:hasSourcedb ?sourcedb .
  FILTER (
    CONTAINS(LCASE(?node), "zr") &&
    (CONTAINS(LCASE(?node), "n") || CONTAINS(LCASE(?linker), "c(=o)o"))
  )
}
ORDER BY ?mof
```

## 2. Which MOFs share the same topology ('pcu') but differ in metal node or linker substitution?

```sparql
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?MOF ?metal ?linker ?topology
WHERE {
?MOF a mofs:MetalOrganicFramework ;
    mofs:hasNodeSmile ?metal;
    mofs:hasLinkerSmile ?linker;
    mofs:hasRCSRSym ?topology .

# find MOFs sharing a given topology (example: pcu)
FILTER(CONTAINS(?topology, "pcu"))
}
```

## 3. Under what synthesis conditions (solvent, temperature, modulator, time) has a given MOF been reported? (e.g. ZIF-8)

### 2 queries:

1. To get all the refcodes for ZIF-8:

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?refcode
WHERE {
?mof mofs:hasNames ?name ;
    mofs:hasCsdRefcode ?refcode .

FILTER(LCASE(STR(?name)) = "zif-8")
}
ORDER BY ?refcode
```

1. Query for synthesis information using the refcodes:

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb ?refcode
    ?metadata ?solvents ?temperature ?temp_unit
    ?time_value ?time_unit ?method ?yield ?doi
WHERE {
VALUES ?refcode {
    "EWIDUK"   "FAWCEN"   "FAWCEN01" "FAWCEN02" "FAWCEN03"
    "NIFREC"   "NIFSED"   "NIFSUT"
    "OFERUN"   "OFERUN03" "OFERUN07" "OFERUN18" "OFERUN20"
    "TUDHUW"   "TUDJAE"   "TUDJEI"   "TUDJIM"
    "TUDJOS"   "TUDJUY"   "TUDKAF"   "TUDKEJ"
}

{
    # Park_Syn — search metadata for any of the refcodes or ZIF-8 name
    ?mof ontomofs_vkg:hasSourcedb "Park_Syn" ;
        ontomofs_vkg:hasMetadata ?metadata .
    FILTER(
    CONTAINS(LCASE(STR(?metadata)), "zif-8")  ||
    CONTAINS(LCASE(STR(?metadata)), "zif8")   ||
    CONTAINS(LCASE(STR(?metadata)), LCASE(?refcode))
    )
}
UNION
{
    # CSD — join on refcode
    ?mof ontomofs_vkg:hasSourcedb  "CSD MOF Collection" ;
        ontomofs_vkg:hasCsdRefcode ?refcode .
}
UNION
{
    # SynMOF — join on refcode
    ?mof ontomofs_vkg:hasSourcedb "SynMOF" ;
        ontomofs_vkg:hasCsdRefcode ?refcode .
}

OPTIONAL { ?mof ontomofs_vkg:hasSourcedb       ?sourcedb    }
OPTIONAL { ?mof ontomofs_vkg:hasCsdRefcode      ?refcode     }
OPTIONAL { ?mof ontomofs_vkg:hasSolvents        ?solvents    }
OPTIONAL { ?mof ontomofs_vkg:hasTemperature     ?temperature }
OPTIONAL { ?mof ontomofs_vkg:hasTemperatureUnit ?temp_unit   }
OPTIONAL { ?mof ontomofs_vkg:hasTime            ?time_value  }
OPTIONAL { ?mof ontomofs_vkg:hasTimeUnit        ?time_unit   }
OPTIONAL { ?mof ontomofs_vkg:hasMethod          ?method      }
OPTIONAL { ?mof ontomofs_vkg:hasYield           ?yield       }
OPTIONAL { ?mof ontomofs_vkg:hasReferenceDOI    ?doi         }
OPTIONAL { ?mof ontomofs_vkg:hasMetadata        ?metadata    }
}
ORDER BY ?sourcedb ?refcode
```

## 4. Which MOFs can be synthesised under aqueous or low-temperature conditions? (Specifically no DMF,DMA and between 20-100 degrees C)

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?mof ?temperature ?temperatureUnit ?solvents ?method ?sourcedb
WHERE {
?mof a ontomofs_vkg:MetalOrganicFramework ;
    ontomofs_vkg:hasSourcedb ?sourcedb ;
    ontomofs_vkg:hasSolvents ?solvents ;
    ontomofs_vkg:hasTemperature ?temperature .

FILTER (?sourcedb IN ("Park_Syn", "SynMOF", "CSD MOF Collection"))

OPTIONAL { ?mof ontomofs_vkg:hasTemperatureUnit ?temperatureUnit }
OPTIONAL { ?mof ontomofs_vkg:hasMethod ?method }

FILTER (
    (
    # Temperature window: 20°C–100°C
    (STR(?temperatureUnit) = "Kelvin" && ?temperature > 293 && ?temperature < 373) ||
    (STR(?temperatureUnit) = "Celsius" && ?temperature > 20 && ?temperature < 100)
    )
    &&
    # Aqueous
    (
    CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "water") ||
    CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "h2o")
    )
    &&
    # Exclude DMF/DMA
    !CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "dmf") &&
    !CONTAINS(LCASE(STR(COALESCE(?solvents, ""))), "dma")
)
}
ORDER BY ?temperature
```

## 5. Which MOFs remain stable in water, acidic environments, or under high humidity?

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?mof ?name ?waterStabilityPred ?burtchLabel
                ?acidStability ?baseStability ?details ?sourcedb
WHERE {
?mof a ontomofs_vkg:MetalOrganicFramework ;
    ontomofs_vkg:hasSourcedb ?sourcedb .
OPTIONAL { ?mof ontomofs_vkg:hasNames ?name }
OPTIONAL { ?mof ontomofs_vkg:hasPredictedWaterStability ?waterStabilityPred }
OPTIONAL { ?mof ontomofs_vkg:hasBurtchLabel ?burtchLabel }
OPTIONAL { ?mof ontomofs_vkg:hasExperimentalAcidStability ?acidStability }
OPTIONAL { ?mof ontomofs_vkg:hasExperimentalBaseStability ?baseStability }
OPTIONAL { ?mof ontomofs_vkg:hasExperimentalStabilityInformation ?details }
FILTER (
    (?waterStabilityPred > 0.5) ||
    (?burtchLabel >= 3) ||
    CONTAINS(LCASE(COALESCE(?acidStability, "")), "stable") ||
    CONTAINS(LCASE(COALESCE(?details, "")), "water") ||
    CONTAINS(LCASE(COALESCE(?details, "")), "humid")
)
}
ORDER BY DESC(?waterStabilityPred)
```

## 6. Which MOFs have experimentally validated thermal stability above a given temperature?

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT ?mof ?name ?refcode ?thermalStability ?thermalUnit ?doi ?sourcedb
WHERE {
?mof a ontomofs_vkg:MetalOrganicFramework ;
    ontomofs_vkg:hasExperimentalThermalStability ?thermalStability ;
    ontomofs_vkg:hasSourcedb ?sourcedb .
OPTIONAL { ?mof ontomofs_vkg:hasCsdRefcode ?refcode }
OPTIONAL { ?mof ontomofs_vkg:hasExperimentalThermalStabilityUnit ?thermalUnit }
OPTIONAL { ?mof ontomofs_vkg:hasReferenceDOI ?doi }
FILTER (?thermalStability > 400)
}
ORDER BY DESC(?thermalStability)
```

## 7. Which MOFs show high uptake or selectivity for a given gas pair (e.g., CO₂/N₂, CO₂/H₂O)?

### Predicted Values (from ARC_MOF or TOBASSCO)

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?node ?linker
    ?postcombUptake ?precombUptake ?ngasUptake ?landfillUptake ?methaneUptake
    ?source
WHERE {
?mof a mofs:MetalOrganicFramework ;
    mofs:hasNodeSmile ?node ;
    mofs:hasLinkerSmile ?linker ;
    mofs:hasSourcedb ?source .
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2N2P09T298mmolg ?postcombUptake }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2H2P40T313mmolg ?precombUptake }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2CH4P10T298mmolg ?ngasUptake }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2CH4P7_6T338mmolg ?landfillUptake }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CH4P65T298mmolg ?methaneUptake }
FILTER(
    (BOUND(?postcombUptake) && ?postcombUptake > 1.0) ||
    (BOUND(?precombUptake) && ?precombUptake > 5.0)
)}
ORDER BY DESC(?postcombUptake)
```

## 8. Which MOFs have predicted vs experimentally measured adsorption properties, and how do they compare?

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?name ?mofid
    # NIST adsorption
    ?exp_gas ?exp_temp ?exp_pressure ?exp_uptake
    # ARC-MOF predicted
    ?post_uptake ?post_wc ?post_sel
    ?ng_uptake   ?ng_wc   ?ng_sel
    ?ch4_uptake  ?ch4_wc
    # Tobassco predicted
    ?co2_p15 ?co2_p10 ?wc_vacuum ?wc_temp ?tob_sel
WHERE {
# CoRE 2025 as the bridge — must have both a name and a mofid
?core ontomofs_vkg:hasSourcedb  "CoRE MOF 2025" ;
        ontomofs_vkg:hasNames     ?name           ;
        ontomofs_vkg:hasMofidV1   ?mofid          .

# NIST joined on name
?nist ontomofs_vkg:hasSourcedb                "Nist Experimental Isotherms" ;
        ontomofs_vkg:hasNames                   ?name         ;
        ontomofs_vkg:hasExpAdsorptionAdsorbate   ?exp_gas      ;
        ontomofs_vkg:hasExpAdsorptionTemperature ?exp_temp     ;
        ontomofs_vkg:hasExpAdsorptionPressure    ?exp_pressure ;
        ontomofs_vkg:hasExpAdsorptionUptake      ?exp_uptake   .

# ARC-MOF joined on mofid — optional so tobassco-only matches still appear
OPTIONAL {
    ?arc ontomofs_vkg:hasSourcedb "ARC_MOF 2025" ;
        ontomofs_vkg:hasMofidV1  ?mofid         .
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionBinaryUptake_CO2N2P09T298mmolg    ?post_uptake }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionWorkingCapacity_CO2N2P09T298mmolg ?post_wc     }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionSelectivity_CO2N2P09T298          ?post_sel    }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionBinaryUptake_CO2CH4P10T298mmolg   ?ng_uptake   }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionWorkingCapacity_CO2CH4P10T298mmolg ?ng_wc      }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionSelectivity_CO2CH4P10T298         ?ng_sel      }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionBinaryUptake_CH4P65T298mmolg      ?ch4_uptake  }
    OPTIONAL { ?arc ontomofs_vkg:hasPredAdsorptionWorkingCapacity_CH4P65T298mmolg   ?ch4_wc      }
}

# Tobassco joined on mofid — optional so arc-only matches still appear
OPTIONAL {
    ?tob ontomofs_vkg:hasSourcedb "Tobassco" ;
        ontomofs_vkg:hasMofidV1  ?mofid     .
    OPTIONAL { ?tob ontomofs_vkg:hasPredAdsorptionUptake_CO2P15T298mmolg            ?co2_p15   }
    OPTIONAL { ?tob ontomofs_vkg:hasPredAdsorptionUptake_CO2P10T363mmolg            ?co2_p10   }
    OPTIONAL { ?tob ontomofs_vkg:hasPredAdsorptionWorkingCapacity_CO2VacuumSwingmmolg ?wc_vacuum }
    OPTIONAL { ?tob ontomofs_vkg:hasPredAdsorptionWorkingCapacity_CO2TempSwingmmolg  ?wc_temp   }
    OPTIONAL { ?tob ontomofs_vkg:hasPredAdsorptionSelectivity_CO2N2                 ?tob_sel   }
}

# Require at least one predicted adsorption value to exist
FILTER (
    BOUND(?post_uptake) || BOUND(?ng_uptake) || BOUND(?ch4_uptake) ||
    BOUND(?co2_p15)     || BOUND(?wc_vacuum)
)
}
```

## 9. What pore metrics (PLD, LCD, surface area) correspond to specific adsorption behaviours?

### Experimental

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?nist_mof
?name
?uptake
?temperature
?pressure
?core_source
?refcode
?mofid
?pld
?lcd
?vf
WHERE {

# 1. Experimental adsorption from NIST

?nist_mof mofs:hasSourcedb "Nist Experimental Isotherms" ;
            mofs:hasNames ?name ;
            mofs:hasExpAdsorptionUptake ?uptake ;
            mofs:hasExpAdsorptionTemperature ?temperature ;
            mofs:hasExpAdsorptionPressure ?pressure .

# 2. Match into CORE datasets via name

?core_mof mofs:hasNames ?core_name ;
            mofs:hasSourcedb ?core_source .

FILTER(
    ?core_source IN ("CoRE MOF 2019", "CoRE MOF 2025")
)

FILTER(
    LCASE(STR(?core_name)) = LCASE(STR(?name))
)

# 3. Pull identifiers from CORE

OPTIONAL { ?core_mof mofs:hasCsdRefcode ?refcode . }
OPTIONAL { ?core_mof mofs:hasMofidV1 ?mofid . }

# 4. Chemistry from CORE

OPTIONAL { ?core_mof mofs:hasPLD ?pld . }
OPTIONAL { ?core_mof mofs:hasLCD ?lcd . }
OPTIONAL { ?core_mof mofs:hasVF ?vf . }
}
ORDER BY ?name
```

### Simulated

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?mof
?mofid
?value
?PLD
?LCD
?VF
WHERE {
# Vary this for different databases/Adsorption Conditions
?mof mofs:hasSourcedb "Tobassco" ;
    mofs:hasMofidV1 ?mofid ;
    mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?value .

OPTIONAL { ?mof mofs:hasMetal ?metal }
OPTIONAL { ?mof mofs:hasNodeSmile ?nodes }
OPTIONAL { ?mof mofs:hasLinkerSmile ?linkers }
OPTIONAL { ?mof mofs:hasFunctionalGroup ?func_group }
}
```

## 10. For a given MOF, what information is available across literature and databases (structure, synthesis, properties, applications)?

Doesn't run

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb
    # Identity
    ?refcode ?name ?mofid ?mofkey ?formula ?empirical_formula

    # Literature
    ?doi ?year

    # Structure
    ?crystal_system ?topology ?space_group ?catenation ?dimension
    ?pld ?lcd ?lfpd ?density ?uc_volume
    ?gsa ?ngsa ?vsa ?nvsa ?gpv ?ngpv ?vf ?nvf
    ?cell_a ?cell_b ?cell_c ?cell_alpha ?cell_beta ?cell_gamma
    ?nodes ?linkers ?metal ?oms ?func_group

    # Electronic
    ?band_gap ?total_energy

    # Stability — predicted
    ?pred_thermal ?pred_solvent ?pred_water ?kh_class

    # Stability — experimental
    ?exp_thermal ?temp_unit ?burtch_label
    ?acid ?base ?exposure_time ?stability_method

    # Synthesis
    ?solvents ?temperature ?synth_temp_unit
    ?time_value ?time_unit ?method ?yield ?metadata

    # Adsorption — predicted
    ?co2_p15 ?co2_p10 ?co2_p70 ?wc_vacuum ?wc_temp ?tob_sel
    ?post_uptake ?post_wc ?post_sel
    ?ng_uptake ?ng_wc ?ng_sel
    ?lf_uptake ?lf_wc ?lf_sel
    ?ch4_uptake ?ch4_wc

    # Adsorption — experimental
    ?exp_gas ?exp_temp ?exp_pressure ?exp_uptake ?exp_uptake_units

    # Biocompatibility
    ?bio_label ?exp_bio_label

    # Geometric (QMOF)
    ?unit_cell_formula

WHERE {
# Anchor — match MOF-5 by name, refcode, or known CSD refcode SAHYOG
{
    ?mof mofs:hasNames ?name .
    FILTER(
    CONTAINS(LCASE(STR(?name)), "mof-5") ||
    CONTAINS(LCASE(STR(?name)), "irmof-1")
    )
}
UNION
{
    ?mof mofs:hasCsdRefcode ?refcode .
    FILTER(?refcode = "SAHYOG")
}
UNION
{
    # Park_Syn — search metadata
    ?mof mofs:hasSourcedb "Park_Syn" ;
        mofs:hasMetadata ?metadata .
    FILTER(
    CONTAINS(LCASE(STR(?metadata)), "mof-5")   ||
    CONTAINS(LCASE(STR(?metadata)), "irmof-1") ||
    CONTAINS(LCASE(STR(?metadata)), "sahyog")
    )
}

OPTIONAL { ?mof mofs:hasSourcedb          ?sourcedb        }

# Identity
OPTIONAL { ?mof mofs:hasCsdRefcode        ?refcode         }
OPTIONAL { ?mof mofs:hasNames             ?name            }
OPTIONAL { ?mof mofs:hasMofidV1           ?mofid           }
OPTIONAL { ?mof mofs:hasMofKey            ?mofkey          }
OPTIONAL { ?mof mofs:hasUnitCellFormula   ?formula         }
OPTIONAL { ?mof mofs:hasEmpiricalFormula  ?empirical_formula }

# Literature
OPTIONAL { ?mof mofs:hasReferenceDOI      ?doi             }
OPTIONAL { ?mof mofs:hasYearPublished     ?year            }

# Structure
OPTIONAL { ?mof mofs:hasCrystalSystem     ?crystal_system  }
OPTIONAL { ?mof mofs:hasRCSRSym           ?topology        }
OPTIONAL { ?mof mofs:hasSpaceGroupNumber  ?space_group     }
OPTIONAL { ?mof mofs:hasCatenation        ?catenation      }
OPTIONAL { ?mof mofs:hasDimension         ?dimension       }
OPTIONAL { ?mof mofs:hasPLD               ?pld             }
OPTIONAL { ?mof mofs:hasLCD               ?lcd             }
OPTIONAL { ?mof mofs:hasLFPD              ?lfpd            }
OPTIONAL { ?mof mofs:hasDensity           ?density         }
OPTIONAL { ?mof mofs:hasUnitCellVolume    ?uc_volume       }
OPTIONAL { ?mof mofs:hasGSA               ?gsa             }
OPTIONAL { ?mof mofs:hasNGSA              ?ngsa            }
OPTIONAL { ?mof mofs:hasVSA               ?vsa             }
OPTIONAL { ?mof mofs:hasNVSA              ?nvsa            }
OPTIONAL { ?mof mofs:hasGPV               ?gpv             }
OPTIONAL { ?mof mofs:hasNGPV              ?ngpv            }
OPTIONAL { ?mof mofs:hasVF                ?vf              }
OPTIONAL { ?mof mofs:hasNVF               ?nvf             }
OPTIONAL { ?mof mofs:hasCellParametersA   ?cell_a          }
OPTIONAL { ?mof mofs:hasCellParametersB   ?cell_b          }
OPTIONAL { ?mof mofs:hasCellParametersC   ?cell_c          }
OPTIONAL { ?mof mofs:hasCellParametersAlpha ?cell_alpha    }
OPTIONAL { ?mof mofs:hasCellParametersBeta  ?cell_beta     }
OPTIONAL { ?mof mofs:hasCellParametersGamma ?cell_gamma    }
OPTIONAL { ?mof mofs:hasNodeSmile         ?nodes           }
OPTIONAL { ?mof mofs:hasLinkerSmile       ?linkers         }
OPTIONAL { ?mof mofs:hasMetal             ?metal           }
OPTIONAL { ?mof mofs:hasOpenMetalSite     ?oms             }
OPTIONAL { ?mof mofs:hasFunctionalGroup   ?func_group      }

# Electronic
OPTIONAL { ?mof mofs:hasBandGap           ?band_gap        }
OPTIONAL { ?mof mofs:hasTotalEnergy       ?total_energy    }

# Stability — predicted
OPTIONAL { ?mof mofs:hasPredictedThermalStability        ?pred_thermal  }
OPTIONAL { ?mof mofs:hasPredictedSolventRemovalStability ?pred_solvent  }
OPTIONAL { ?mof mofs:hasPredictedWaterStability          ?pred_water    }
OPTIONAL { ?mof mofs:hasPredictedKHwater                 ?kh_class      }

# Stability — experimental
OPTIONAL { ?mof mofs:hasExperimentalThermalStability     ?exp_thermal      }
OPTIONAL { ?mof mofs:hasExperimentalThermalStabilityUnit ?temp_unit        }
OPTIONAL { ?mof mofs:hasBurtchLabel                      ?burtch_label     }
OPTIONAL { ?mof mofs:hasExperimentalAcidStability        ?acid             }
OPTIONAL { ?mof mofs:hasExperimentalBaseStability        ?base             }
OPTIONAL { ?mof mofs:hasExposureTime                     ?exposure_time    }
OPTIONAL { ?mof mofs:hasExperimentalStabilityMethod      ?stability_method }

# Synthesis
OPTIONAL { ?mof mofs:hasSolvents         ?solvents        }
OPTIONAL { ?mof mofs:hasTemperature      ?temperature     }
OPTIONAL { ?mof mofs:hasTemperatureUnit  ?synth_temp_unit }
OPTIONAL { ?mof mofs:hasTime             ?time_value      }
OPTIONAL { ?mof mofs:hasTimeUnit         ?time_unit       }
OPTIONAL { ?mof mofs:hasMethod           ?method          }
OPTIONAL { ?mof mofs:hasYield            ?yield           }
OPTIONAL { ?mof mofs:hasMetadata         ?metadata        }

# Adsorption — predicted (Tobassco)
OPTIONAL { ?mof mofs:hasPredAdsorptionUptake_CO2P15T298mmolg              ?co2_p15    }
OPTIONAL { ?mof mofs:hasPredAdsorptionUptake_CO2P10T363mmolg              ?co2_p10    }
OPTIONAL { ?mof mofs:hasPredAdsorptionUptake_CO2P70T413mmolg              ?co2_p70    }
OPTIONAL { ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2VacuumSwingmmolg ?wc_vacuum  }
OPTIONAL { ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2TempSwingmmolg   ?wc_temp    }
OPTIONAL { ?mof mofs:hasPredAdsorptionSelectivity_CO2N2                   ?tob_sel    }

# Adsorption — predicted (ARC-MOF)
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2N2P09T298mmolg      ?post_uptake }
OPTIONAL { ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2N2P09T298mmolg   ?post_wc     }
OPTIONAL { ?mof mofs:hasPredAdsorptionSelectivity_CO2N2P09T298            ?post_sel    }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2CH4P10T298mmolg     ?ng_uptake   }
OPTIONAL { ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2CH4P10T298mmolg  ?ng_wc       }
OPTIONAL { ?mof mofs:hasPredAdsorptionSelectivity_CO2CH4P10T298           ?ng_sel      }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CO2CH4P7_6T338mmolg    ?lf_uptake   }
OPTIONAL { ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2CH4P7_6T338mmolg ?lf_wc       }
OPTIONAL { ?mof mofs:hasPredAdsorptionSelectivity_CO2CH4P7_6T338          ?lf_sel      }
OPTIONAL { ?mof mofs:hasPredAdsorptionBinaryUptake_CH4P65T298mmolg        ?ch4_uptake  }
OPTIONAL { ?mof mofs:hasPredAdsorptionWorkingCapacity_CH4P65T298mmolg     ?ch4_wc      }

# Adsorption — experimental (NIST) joined on name
OPTIONAL {
    ?nist mofs:hasSourcedb                 "Nist Experimental Isotherms" ;
        mofs:hasNames                    ?name                          ;
        mofs:hasExpAdsorptionAdsorbate   ?exp_gas                       ;
        mofs:hasExpAdsorptionTemperature ?exp_temp                      ;
        mofs:hasExpAdsorptionPressure    ?exp_pressure                  ;
        mofs:hasExpAdsorptionUptake      ?exp_uptake                    .
    OPTIONAL { ?nist mofs:hasExpAdsorptionUptakeUnit ?exp_uptake_units }
}

# Biocompatibility
OPTIONAL { ?mof mofs:hasPredBioLabel ?bio_label     }
OPTIONAL { ?mof mofs:hasExpBioLabel  ?exp_bio_label }

# QMOF
OPTIONAL { ?mof mofs:hasUnitCellFormula ?unit_cell_formula }
}
```

# More Questions

## 1. Which mofs have both a predicted and experimental stability?

```sparql
PREFIX ontomofs_vkg: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?refcode
    # Predicted (CoRE 2025 ML)
    ?pred_thermal ?pred_solvent ?pred_water ?kh_class
    # Experimental thermal (MOFSimplify)
    ?exp_thermal_C
    # Experimental water/acid/base (WS24)
    ?burtch_label ?acid ?base ?exposure_time ?method
    # Experimental thermal from CSD (melting point)
    ?melting_point_K
WHERE {
# Anchor on CSD refcode — every stability dataset carries hasCsdRefcode
?mof ontomofs_vkg:hasCsdRefcode ?refcode .

OPTIONAL {
    ?pred ontomofs_vkg:hasCsdRefcode                      ?refcode     ;
        ontomofs_vkg:hasSourcedb                        "CoRE MOF 2025" ;
        ontomofs_vkg:hasPredictedThermalStability       ?pred_thermal ;
        ontomofs_vkg:hasPredictedSolventRemovalStability ?pred_solvent ;
        ontomofs_vkg:hasPredictedWaterStability         ?pred_water  .
    OPTIONAL { ?pred ontomofs_vkg:hasPredictedKHwater      ?kh_class    }
}

OPTIONAL {
    ?ms ontomofs_vkg:hasCsdRefcode                        ?refcode        ;
        ontomofs_vkg:hasSourcedb                          "MOFSimplify Thermal Dataset" ;
        ontomofs_vkg:hasExperimentalThermalStability      ?exp_thermal_C .
}

OPTIONAL {
    ?ws ontomofs_vkg:hasCsdRefcode                        ?refcode       ;
        ontomofs_vkg:hasSourcedb                          "WS24 Kulik Group" ;
        ontomofs_vkg:hasBurtchLabel                       ?burtch_label .
    OPTIONAL { ?ws ontomofs_vkg:hasExperimentalAcidStability    ?acid          }
    OPTIONAL { ?ws ontomofs_vkg:hasExperimentalBaseStability    ?base          }
    OPTIONAL { ?ws ontomofs_vkg:hasExposureTime                 ?exposure_time }
    OPTIONAL { ?ws ontomofs_vkg:hasExperimentalStabilityMethod  ?method        }
}

OPTIONAL {
    ?csd ontomofs_vkg:hasCsdRefcode                       ?refcode       ;
        ontomofs_vkg:hasSourcedb                        "CSD MOF Collection" ;
        ontomofs_vkg:hasExperimentalThermalStability    ?melting_point_K .
}

# Only return MOFs where at least one stability value exists
FILTER (
    BOUND(?pred_thermal) || BOUND(?exp_thermal_C) ||
    BOUND(?burtch_label) || BOUND(?melting_point_K)
)
}
ORDER BY ?refcode
```

## 2. For each MOF that has stability information, what are their nodes/metal, linker, and functional group (if that information is available)?

### Predicted Thermal

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb ?pred_thermal
    ?nodes ?linkers ?metal ?func_group
WHERE {
?mof mofs:hasPredictedThermalStability ?pred_thermal .

OPTIONAL { ?mof mofs:hasSourcedb      ?sourcedb }
OPTIONAL { ?mof mofs:hasNodeSmile     ?nodes    }
OPTIONAL { ?mof mofs:hasLinkerSmile   ?linkers  }
OPTIONAL { ?mof mofs:hasMetal         ?metal    }
OPTIONAL {
    ?mof mofs:hasMofidV1 ?mofid .
    ?tob mofs:hasSourcedb        "Tobassco" ;
        mofs:hasMofidV1         ?mofid     ;
        mofs:hasFunctionalGroup ?func_group .
}
}
```

### Predicted Water

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb ?pred_water ?kh_class
    ?nodes ?linkers ?metal ?func_group
WHERE {
?mof mofs:hasPredictedWaterStability ?pred_water .

OPTIONAL { ?mof mofs:hasSourcedb       ?sourcedb  }
OPTIONAL { ?mof mofs:hasNodeSmile      ?nodes     }
OPTIONAL { ?mof mofs:hasLinkerSmile    ?linkers   }
OPTIONAL { ?mof mofs:hasMetal          ?metal     }
OPTIONAL { ?mof mofs:hasPredictedKHwater ?kh_class }
OPTIONAL {
    ?mof mofs:hasMofidV1 ?mofid .
    ?tob mofs:hasSourcedb        "Tobassco" ;
        mofs:hasMofidV1         ?mofid     ;
        mofs:hasFunctionalGroup ?func_group .
}
}
```

### Experimental Thermal

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb ?refcode ?exp_thermal ?temp_unit
    ?nodes ?linkers ?metal ?func_group
WHERE {
?mof mofs:hasExperimentalThermalStability ?exp_thermal .

OPTIONAL { ?mof mofs:hasSourcedb             ?sourcedb  }
OPTIONAL { ?mof mofs:hasExperimentalThermalStabilityUnit ?temp_unit }
OPTIONAL { ?mof mofs:hasCsdRefcode           ?refcode   }
OPTIONAL { ?mof mofs:hasNodeSmile            ?nodes     }
OPTIONAL { ?mof mofs:hasLinkerSmile          ?linkers   }
OPTIONAL { ?mof mofs:hasMetal                ?metal     }

# Chemistry may sit on a different node sharing the same refcode
OPTIONAL {
    ?mof mofs:hasCsdRefcode ?refcode .
    ?other mofs:hasCsdRefcode  ?refcode .
    FILTER(?other != ?mof)
    OPTIONAL { ?other mofs:hasNodeSmile     ?nodes   }
    OPTIONAL { ?other mofs:hasLinkerSmile   ?linkers }
    OPTIONAL { ?other mofs:hasMetal         ?metal   }
}

OPTIONAL {
    ?mof mofs:hasMofidV1 ?mofid .
    ?tob mofs:hasSourcedb        "Tobassco" ;
        mofs:hasMofidV1         ?mofid     ;
        mofs:hasFunctionalGroup ?func_group .
}
}
```

### Burtch Label

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb ?refcode ?burtch_label ?nodes ?linkers ?metal ?func_group ?method ?details
    
WHERE {
?mof mofs:hasBurtchLabel ?burtch_label .

OPTIONAL { ?mof mofs:hasSourcedb                          ?sourcedb }
OPTIONAL { ?mof mofs:hasCsdRefcode                        ?refcode  }
OPTIONAL { ?mof mofs:hasExperimentalStabilityMethod       ?method   }
OPTIONAL { ?mof mofs:hasExperimentalStabilityInformation  ?details  }
OPTIONAL { ?mof mofs:hasNodeSmile                         ?nodes    }
OPTIONAL { ?mof mofs:hasLinkerSmile                       ?linkers  }
OPTIONAL { ?mof mofs:hasMetal                             ?metal    }

# Chemistry may sit on a different node sharing the same refcode
OPTIONAL {
    ?mof mofs:hasCsdRefcode ?refcode .
    ?other mofs:hasCsdRefcode  ?refcode .
    FILTER(?other != ?mof)
    OPTIONAL { ?other mofs:hasNodeSmile     ?nodes   }
    OPTIONAL { ?other mofs:hasLinkerSmile   ?linkers }
    OPTIONAL { ?other mofs:hasMetal         ?metal   }
}

OPTIONAL {
    ?mof mofs:hasMofidV1 ?mofid .
    ?tob mofs:hasSourcedb        "Tobassco" ;
        mofs:hasMofidV1         ?mofid     ;
        mofs:hasFunctionalGroup ?func_group .
}
}
ORDER BY ?burtch_label
```

### Experimental Acid/Base

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT ?mof ?sourcedb ?refcode
    ?acid_stability ?base_stability ?nodes ?linkers ?metal ?func_group ?exposure_time ?method
    
WHERE {
{ ?mof mofs:hasExperimentalAcidStability ?acid_stability }
UNION
{ ?mof mofs:hasExperimentalBaseStability ?base_stability }

OPTIONAL { ?mof mofs:hasSourcedb                       ?sourcedb       }
OPTIONAL { ?mof mofs:hasCsdRefcode                     ?refcode        }
OPTIONAL { ?mof mofs:hasExperimentalAcidStability      ?acid_stability }
OPTIONAL { ?mof mofs:hasExperimentalBaseStability      ?base_stability }
OPTIONAL { ?mof mofs:hasExposureTime                   ?exposure_time  }
OPTIONAL { ?mof mofs:hasExperimentalStabilityMethod    ?method         }
OPTIONAL { ?mof mofs:hasNodeSmile                      ?nodes          }
OPTIONAL { ?mof mofs:hasLinkerSmile                    ?linkers        }
OPTIONAL { ?mof mofs:hasMetal                          ?metal          }

# Chemistry may sit on a different node sharing the same refcode
OPTIONAL {
    ?mof mofs:hasCsdRefcode ?refcode .
    ?other mofs:hasCsdRefcode  ?refcode .
    FILTER(?other != ?mof)
    OPTIONAL { ?other mofs:hasNodeSmile     ?nodes   }
    OPTIONAL { ?other mofs:hasLinkerSmile   ?linkers }
    OPTIONAL { ?other mofs:hasMetal         ?metal   }
}

OPTIONAL {
    ?mof mofs:hasMofidV1 ?mofid .
    ?tob mofs:hasSourcedb        "Tobassco" ;
        mofs:hasMofidV1         ?mofid     ;
        mofs:hasFunctionalGroup ?func_group .
}
}
```

## 3. For each MOF that has adsorption data, what are their nodes/metal, linker, and functional group (if that information is available)?

### Experimental

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?nist_mof
?name
?uptake
?temperature
?pressure
?core_source
?refcode
?mofid
?metal
?node
?linker
?func_group
WHERE {

# 1. Experimental adsorption from NIST

?nist_mof mofs:hasSourcedb "Nist Experimental Isotherms" ;
            mofs:hasNames ?name ;
            mofs:hasExpAdsorptionUptake ?uptake ;
            mofs:hasExpAdsorptionTemperature ?temperature ;
            mofs:hasExpAdsorptionPressure ?pressure .

# 2. Match into CORE datasets via name

?core_mof mofs:hasNames ?core_name ;
            mofs:hasSourcedb ?core_source .

FILTER(
    ?core_source IN ("CoRE MOF 2019", "CoRE MOF 2025")
)

FILTER(
    LCASE(STR(?core_name)) = LCASE(STR(?name))
)

# 3. Pull identifiers from CORE

OPTIONAL { ?core_mof mofs:hasCsdRefcode ?refcode . }
OPTIONAL { ?core_mof mofs:hasMofidV1 ?mofid . }


# 4. Chemistry from CORE


OPTIONAL { ?core_mof mofs:hasMetal ?metal . }
OPTIONAL { ?core_mof mofs:hasNodeSmile ?node . }
OPTIONAL { ?core_mof mofs:hasLinkerSmile ?linker . }

# 5. Functional groups from Tobassco via MOFID

OPTIONAL {
    ?tob_mof mofs:hasSourcedb "Tobassco" ;
            mofs:hasMofidV1 ?mofid ;
            mofs:hasFunctionalGroup ?func_group .
}

}
ORDER BY ?name
```

### Simulated (Tobassco database)

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?mof
?mofid
?value
?metal
?nodes
?linkers
?func_group
WHERE {

?mof mofs:hasSourcedb "Tobassco" ;
    mofs:hasMofidV1 ?mofid ;
    # One property at a time otherwise it just crashes
    mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?value .

OPTIONAL { ?mof mofs:hasMetal ?metal }
OPTIONAL { ?mof mofs:hasNodeSmile ?nodes }
OPTIONAL { ?mof mofs:hasLinkerSmile ?linkers }
OPTIONAL { ?mof mofs:hasFunctionalGroup ?func_group }
}
```

The other properties: hasPredAdsorptionUptake_CO2P10T363mmolg, hasPredAdsorptionUptake_CO2P70T413mmolg, hasPredAdsorptionBinaryUptake_CO2N2P85T298mmolg, hasPredAdsorptionBinaryUptake_CO2N2P15T298mmolg, hasPredAdsorptionWorkingCapacity_CO2VacuumSwingmmolg, hasPredAdsorptionWorkingCapacity_CO2TempSwingmmolg, hasPredAdsorptionSelectivity_CO2N2 

### Simulated (ARCMOF database)

1. Binary Uptake Part 1

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?mof
?mofid
?property
?value
?metal
?nodes
?linkers
WHERE {

?mof mofs:hasSourcedb "ARC_MOF 2025" ;
    mofs:hasMofidV1 ?mofid .

{
    ?mof mofs:hasPredAdsorptionBinaryUptake_CO2N2P09T298mmolg ?value .
    BIND("CO2N2P09T298_BinaryUptake" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionBinaryUptake_CO2H2P40T313mmolg ?value .
    BIND("CO2H2P40T313_BinaryUptake" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionBinaryUptake_CO2CH4P10T298mmolg ?value .
    BIND("CO2CH4P10T298_BinaryUptake" AS ?property)
}

OPTIONAL { ?mof mofs:hasMetal ?metal }
OPTIONAL { ?mof mofs:hasNodeSmile ?nodes }
OPTIONAL { ?mof mofs:hasLinkerSmile ?linkers }
}
```

1. Binary Uptake Part 2

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?mof
?mofid
?property
?value
?metal
?nodes
?linkers
WHERE {

?mof mofs:hasSourcedb "ARC_MOF 2025" ;
    mofs:hasMofidV1 ?mofid .

{
    ?mof mofs:hasPredAdsorptionBinaryUptake_CO2CH4P7_6T338mmolg ?value .
    BIND("CO2CH4P7_6T338_BinaryUptake" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionBinaryUptake_CH4P65T298mmolg ?value .
    BIND("CH4P65T298_BinaryUptake" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2N2P09T298mmolg ?value .
    BIND("CO2N2P09T298_WorkingCapacity" AS ?property)
}

OPTIONAL { ?mof mofs:hasMetal ?metal }
OPTIONAL { ?mof mofs:hasNodeSmile ?nodes }
OPTIONAL { ?mof mofs:hasLinkerSmile ?linkers }
}
```

1. Working Capacity

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?mof
?mofid
?property
?value
?metal
?nodes
?linkers
WHERE {

?mof mofs:hasSourcedb "ARC_MOF 2025" ;
    mofs:hasMofidV1 ?mofid .

{
    ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2H2P40T313mmolg ?value .
    BIND("CO2H2P40T313_WorkingCapacity" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2CH4P10T298mmolg ?value .
    BIND("CO2CH4P10T298_WorkingCapacity" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionWorkingCapacity_CO2CH4P7_6T338mmolg ?value .
    BIND("CO2CH4P7_6T338_WorkingCapacity" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionWorkingCapacity_CH4P65T298mmolg ?value .
    BIND("CH4P65T298_WorkingCapacity" AS ?property)
}

OPTIONAL { ?mof mofs:hasMetal ?metal }
OPTIONAL { ?mof mofs:hasNodeSmile ?nodes }
OPTIONAL { ?mof mofs:hasLinkerSmile ?linkers }
}
```

1. Selectivity

```sparql
PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>

SELECT DISTINCT
?mof
?mofid
?property
?value
?metal
?nodes
?linkers
WHERE {

?mof mofs:hasSourcedb "ARC_MOF 2025" ;
    mofs:hasMofidV1 ?mofid .

{
    ?mof mofs:hasPredAdsorptionSelectivity_CO2N2P09T298 ?value .
    BIND("CO2N2P09T298_Selectivity" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionSelectivity_CO2H2P40T313 ?value .
    BIND("CO2H2P40T313_Selectivity" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionSelectivity_CO2CH4P10T298 ?value .
    BIND("CO2CH4P10T298_Selectivity" AS ?property)
}
UNION
{
    ?mof mofs:hasPredAdsorptionSelectivity_CO2CH4P7_6T338 ?value .
    BIND("CO2CH4P7_6T338_Selectivity" AS ?property)
}

OPTIONAL { ?mof mofs:hasMetal ?metal }
OPTIONAL { ?mof mofs:hasNodeSmile ?nodes }
OPTIONAL { ?mof mofs:hasLinkerSmile ?linkers }
}
```

