"""Generate OntoMOPs SHACL for the OntoLogX extension layer."""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_PATH = HERE / "resources" / "ontomops_shacl.ttl"

_SHAPES = """@prefix ontomops: <https://www.theworldavatar.com/kg/ontomops/> .
@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

# OntoMOPs SHACL for OntoLogX extension (inherit OntoSyn main graph).
# closedByTypes is omitted so a ChemicalOutput may also carry other types.

ontomops:MetalOrganicPolyhedronShape a sh:NodeShape ;
  sh:targetClass ontomops:MetalOrganicPolyhedron ;
  sh:property [
    sh:path rdfs:label ;
    sh:datatype xsd:string ;
    sh:maxCount 1
  ] ;
  sh:property [
    sh:path ontomops:hasCCDCNumber ;
    sh:datatype xsd:string ;
    sh:maxCount 1
  ] ;
  sh:property [
    sh:path ontomops:hasMOPFormula ;
    sh:datatype xsd:string ;
    sh:maxCount 1
  ] ;
  sh:property [
    sh:path ontomops:hasChemicalBuildingUnit ;
    sh:class ontomops:ChemicalBuildingUnit ;
    sh:nodeKind sh:IRI
  ] .

ontomops:ChemicalBuildingUnitShape a sh:NodeShape ;
  sh:targetClass ontomops:ChemicalBuildingUnit ;
  sh:property [
    sh:path rdfs:label ;
    sh:datatype xsd:string ;
    sh:maxCount 1
  ] ;
  sh:property [
    sh:path ontomops:hasCBUFormula ;
    sh:datatype xsd:string ;
    sh:maxCount 1
  ] .

ontomops:MopRepresentationRequiredShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "ChemicalSynthesis output must be isRepresentedBy a MetalOrganicPolyhedron" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
      SELECT $this
      WHERE {
        FILTER NOT EXISTS {
          $this ontosyn:hasChemicalOutput ?out .
          ?out ontosyn:isRepresentedBy ?mop .
          ?mop a ontomops:MetalOrganicPolyhedron .
        }
      }
    '''
  ] .
"""


def generate() -> str:
    return _SHAPES


def write_shapes(path: Path | None = None) -> Path:
    dest = path or OUTPUT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(generate(), encoding="utf-8")
    return dest


def main() -> None:
    dest = write_shapes()
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
