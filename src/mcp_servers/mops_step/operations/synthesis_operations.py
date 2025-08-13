"""
Pure, testable operations for building Synthesis Step objects.

These helpers are imported by the MCP server and can be unit-tested without
invoking MCP directly.
"""

from typing import List, Optional, Dict, Any

from models.Step import (
    SynthesisDocument, ProductSynthesis, Step,
    Add, HeatChill, Dry, Filter, Sonicate, Stir,
    Crystallization, Evaporate, Dissolve, Separate, Transfer,
    ChemicalEntry, ChemicalEntryWithAmount,
)


# ----------------------------
# Parsing helpers
# ----------------------------
def parse_chem_entries_with_amount(items: List[dict]) -> List[ChemicalEntryWithAmount]:
    result: List[ChemicalEntryWithAmount] = []
    for item in items or []:
        names = item.get("chemicalName", [])
        amount = item.get("chemicalAmount", "")
        result.append(ChemicalEntryWithAmount(chemicalName=list(names), chemicalAmount=str(amount)))
    return result


def parse_chem_entries(items: List[dict]) -> List[ChemicalEntry]:
    result: List[ChemicalEntry] = []
    for item in items or []:
        names = item.get("chemicalName", [])
        result.append(ChemicalEntry(chemicalName=list(names)))
    return result


def find_product(doc: SynthesisDocument, ccdc_number: str) -> Optional[ProductSynthesis]:
    for p in doc.synthesis:
        if p.productCCDCNumber == ccdc_number:
            return p
    return None


# ----------------------------
# Document/product helpers
# ----------------------------
def create_document() -> SynthesisDocument:
    return SynthesisDocument(synthesis=[])


def add_product(doc: SynthesisDocument, product_names: List[str], product_ccdc_number: str) -> ProductSynthesis:
    prod = ProductSynthesis(productNames=list(product_names), productCCDCNumber=product_ccdc_number, steps=[])
    doc.synthesis.append(prod)
    return prod


def append_step_to_product(doc: SynthesisDocument, product_ccdc_number: str, step: Step) -> None:
    prod = find_product(doc, product_ccdc_number)
    if not prod:
        raise ValueError(f"Product with CCDC {product_ccdc_number} not found")
    prod.steps.append(step)


# ----------------------------
# Builders for each step type
# ----------------------------
def build_step_add(
    used_vessel_name: str,
    used_vessel_type: str,
    added_chemicals: List[dict],
    step_number: int,
    stir: bool,
    is_layered: bool,
    atmosphere: str,
    duration: str,
    target_ph: float,
    comment: str,
) -> Step:
    return Step(
        Add(
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            addedChemical=parse_chem_entries_with_amount(added_chemicals),
            stepNumber=int(step_number),
            stir=bool(stir),
            isLayered=bool(is_layered),
            atmosphere=atmosphere,
            duration=duration,
            targetPH=float(target_ph),
            comment=comment or "",
        )
    )


def build_step_heat_chill(
    duration: str,
    used_device: str,
    target_temperature: str,
    heating_cooling_rate: str,
    comment: str,
    under_vacuum: bool,
    used_vessel_type: str,
    used_vessel_name: str,
    sealed_vessel: bool,
    stir: bool,
    step_number: int,
    atmosphere: str,
) -> Step:
    return Step(
        HeatChill(
            duration=duration,
            usedDevice=used_device,
            targetTemperature=target_temperature,
            heatingCoolingRate=heating_cooling_rate,
            comment=comment or "",
            underVacuum=bool(under_vacuum),
            usedVesselType=used_vessel_type,
            usedVesselName=used_vessel_name,
            sealedVessel=bool(sealed_vessel),
            stir=bool(stir),
            stepNumber=int(step_number),
            atmosphere=atmosphere,
        )
    )


def build_step_dry(
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    pressure: str,
    temperature: str,
    step_number: int,
    atmosphere: str,
    drying_agents: List[dict],
    comment: str,
) -> Step:
    return Step(
        Dry(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            pressure=pressure,
            temperature=temperature,
            stepNumber=int(step_number),
            atmosphere=atmosphere,
            dryingAgent=parse_chem_entries(drying_agents),
            comment=comment or "",
        )
    )


def build_step_filter(
    washing_solvent: List[dict],
    vacuum_filtration: bool,
    number_of_filtrations: int,
    used_vessel_name: str,
    used_vessel_type: str,
    step_number: int,
    comment: str,
    atmosphere: str,
) -> Step:
    return Step(
        Filter(
            washingSolvent=parse_chem_entries_with_amount(washing_solvent),
            vacuumFiltration=bool(vacuum_filtration),
            numberOfFiltrations=int(number_of_filtrations),
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            stepNumber=int(step_number),
            comment=comment or "",
            atmosphere=atmosphere,
        )
    )


def build_step_sonicate(
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    step_number: int,
    atmosphere: str,
) -> Step:
    return Step(
        Sonicate(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            stepNumber=int(step_number),
            atmosphere=atmosphere,
        )
    )


def build_step_stir(
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    step_number: int,
    atmosphere: str,
    temperature: str,
    wait: bool,
) -> Step:
    return Step(
        Stir(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            stepNumber=int(step_number),
            atmosphere=atmosphere,
            temperature=temperature,
            wait=bool(wait),
        )
    )


def build_step_crystallization(
    used_vessel_name: str,
    used_vessel_type: str,
    target_temperature: str,
    step_number: int,
    duration: str,
    atmosphere: str,
    comment: str,
) -> Step:
    return Step(
        Crystallization(
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            targetTemperature=target_temperature,
            stepNumber=int(step_number),
            duration=duration,
            atmosphere=atmosphere,
            comment=comment or "",
        )
    )


def build_step_evaporate(
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    pressure: str,
    temperature: str,
    step_number: int,
    rotary_evaporator: bool,
    atmosphere: str,
    removed_species: List[dict],
    target_volume: str,
    comment: str,
) -> Step:
    return Step(
        Evaporate(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            pressure=pressure,
            temperature=temperature,
            stepNumber=int(step_number),
            rotaryEvaporator=bool(rotary_evaporator),
            atmosphere=atmosphere,
            removedSpecies=parse_chem_entries(removed_species),
            targetVolume=target_volume,
            comment=comment or "",
        )
    )


def build_step_dissolve(
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    solvent: List[dict],
    step_number: int,
    atmosphere: str,
    comment: str,
) -> Step:
    return Step(
        Dissolve(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            solvent=parse_chem_entries_with_amount(solvent),
            stepNumber=int(step_number),
            atmosphere=atmosphere,
            comment=comment or "",
        )
    )


def build_step_separate(
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    solvent: List[dict],
    step_number: int,
    separation_type: str,
    atmosphere: str,
    comment: str,
) -> Step:
    return Step(
        Separate(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            solvent=parse_chem_entries_with_amount(solvent),
            stepNumber=int(step_number),
            separationType=separation_type,
            atmosphere=atmosphere,
            comment=comment or "",
        )
    )


# ----------------------------
# Summary
# ----------------------------
def summarize_document(doc: SynthesisDocument) -> str:
    lines: List[str] = [f"Total products: {len(doc.synthesis)}", ""]
    for i, prod in enumerate(doc.synthesis):
        lines.append(f"Product {i+1}: CCDC {prod.productCCDCNumber} | Names: {', '.join(prod.productNames)}")
        lines.append(f"  Steps: {len(prod.steps)}")
        for j, step in enumerate(prod.steps):
            step_type = next(iter(step.to_dict().keys()))
            lines.append(f"    Step {j+1}: {step_type}")
        lines.append("")
    return "\n".join(lines)


 