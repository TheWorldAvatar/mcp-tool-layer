from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
import json


# ------------------------------
# Helper data models
# ------------------------------


@dataclass
class ChemicalEntry:
    """Represents a chemical entry with a list of names, without an amount.

    Used for lists like removed species and drying agents.
    """

    chemicalName: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chemicalName": self.chemicalName,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChemicalEntry":
        return cls(chemicalName=list(data.get("chemicalName", [])))


@dataclass
class ChemicalEntryWithAmount:
    """Represents a chemical entry with names and an amount.

    Used for lists like added chemicals, solvents, and washing solvents.
    """

    chemicalName: List[str]
    chemicalAmount: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chemicalName": self.chemicalName,
            "chemicalAmount": self.chemicalAmount,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChemicalEntryWithAmount":
        return cls(
            chemicalName=list(data.get("chemicalName", [])),
            chemicalAmount=data.get("chemicalAmount", ""),
        )


# ------------------------------
# Individual step models
# ------------------------------


@dataclass
class Add:
    usedVesselName: str
    usedVesselType: str
    addedChemical: List[ChemicalEntryWithAmount]
    stepNumber: int
    stir: bool
    isLayered: bool
    atmosphere: str
    duration: str
    targetPH: float
    comment: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "addedChemical": [c.to_dict() for c in self.addedChemical],
            "stepNumber": self.stepNumber,
            "stir": self.stir,
            "isLayered": self.isLayered,
            "atmosphere": self.atmosphere,
            "duration": self.duration,
            "targetPH": self.targetPH,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Add":
        return cls(
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            addedChemical=[ChemicalEntryWithAmount.from_dict(x) for x in data.get("addedChemical", [])],
            stepNumber=int(data["stepNumber"]),
            stir=bool(data["stir"]),
            isLayered=bool(data["isLayered"]),
            atmosphere=data["atmosphere"],
            duration=data["duration"],
            targetPH=float(data["targetPH"]),
            comment=data.get("comment", ""),
        )


@dataclass
class HeatChill:
    duration: str
    usedDevice: str
    targetTemperature: str
    heatingCoolingRate: str
    comment: str
    underVacuum: bool
    usedVesselType: str
    usedVesselName: str
    sealedVessel: bool
    stir: bool
    stepNumber: int
    atmosphere: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedDevice": self.usedDevice,
            "targetTemperature": self.targetTemperature,
            "heatingCoolingRate": self.heatingCoolingRate,
            "comment": self.comment,
            "underVacuum": self.underVacuum,
            "usedVesselType": self.usedVesselType,
            "usedVesselName": self.usedVesselName,
            "sealedVessel": self.sealedVessel,
            "stir": self.stir,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeatChill":
        return cls(
            duration=data["duration"],
            usedDevice=data["usedDevice"],
            targetTemperature=data["targetTemperature"],
            heatingCoolingRate=data["heatingCoolingRate"],
            comment=data.get("comment", ""),
            underVacuum=bool(data["underVacuum"]),
            usedVesselType=data["usedVesselType"],
            usedVesselName=data["usedVesselName"],
            sealedVessel=bool(data["sealedVessel"]),
            stir=bool(data["stir"]),
            stepNumber=int(data["stepNumber"]),
            atmosphere=data["atmosphere"],
        )


@dataclass
class Dry:
    duration: str
    usedVesselName: str
    usedVesselType: str
    pressure: str
    temperature: str
    stepNumber: int
    atmosphere: str
    dryingAgent: List[ChemicalEntry]
    comment: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "dryingAgent": [c.to_dict() for c in self.dryingAgent],
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dry":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            pressure=data["pressure"],
            temperature=data["temperature"],
            stepNumber=int(data["stepNumber"]),
            atmosphere=data["atmosphere"],
            dryingAgent=[ChemicalEntry.from_dict(x) for x in data.get("dryingAgent", [])],
            comment=data.get("comment", ""),
        )


@dataclass
class Filter:
    washingSolvent: List[ChemicalEntryWithAmount]
    vacuumFiltration: bool
    numberOfFiltrations: int
    usedVesselName: str
    usedVesselType: str
    stepNumber: int
    comment: str
    atmosphere: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "washingSolvent": [c.to_dict() for c in self.washingSolvent],
            "vacuumFiltration": self.vacuumFiltration,
            "numberOfFiltrations": self.numberOfFiltrations,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "stepNumber": self.stepNumber,
            "comment": self.comment,
            "atmosphere": self.atmosphere,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Filter":
        return cls(
            washingSolvent=[ChemicalEntryWithAmount.from_dict(x) for x in data.get("washingSolvent", [])],
            vacuumFiltration=bool(data["vacuumFiltration"]),
            numberOfFiltrations=int(data["numberOfFiltrations"]),
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            stepNumber=int(data["stepNumber"]),
            comment=data.get("comment", ""),
            atmosphere=data["atmosphere"],
        )


@dataclass
class Sonicate:
    duration: str
    usedVesselName: str
    usedVesselType: str
    stepNumber: int
    atmosphere: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Sonicate":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            stepNumber=int(data["stepNumber"]),
            atmosphere=data["atmosphere"],
        )


@dataclass
class Stir:
    duration: str
    usedVesselName: str
    usedVesselType: str
    stepNumber: int
    atmosphere: str
    temperature: str
    wait: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "temperature": self.temperature,
            "wait": self.wait,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Stir":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            stepNumber=int(data["stepNumber"]),
            atmosphere=data["atmosphere"],
            temperature=data["temperature"],
            wait=bool(data["wait"]),
        )


@dataclass
class Crystallization:
    usedVesselName: str
    usedVesselType: str
    targetTemperature: str
    stepNumber: int
    duration: str
    atmosphere: str
    comment: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "targetTemperature": self.targetTemperature,
            "stepNumber": self.stepNumber,
            "duration": self.duration,
            "atmosphere": self.atmosphere,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Crystallization":
        return cls(
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            targetTemperature=data["targetTemperature"],
            stepNumber=int(data["stepNumber"]),
            duration=data["duration"],
            atmosphere=data["atmosphere"],
            comment=data.get("comment", ""),
        )


@dataclass
class Evaporate:
    duration: str
    usedVesselName: str
    usedVesselType: str
    pressure: str
    temperature: str
    stepNumber: int
    rotaryEvaporator: bool
    atmosphere: str
    removedSpecies: List[ChemicalEntry]
    targetVolume: str
    comment: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "stepNumber": self.stepNumber,
            "rotaryEvaporator": self.rotaryEvaporator,
            "atmosphere": self.atmosphere,
            "removedSpecies": [c.to_dict() for c in self.removedSpecies],
            "targetVolume": self.targetVolume,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Evaporate":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            pressure=data["pressure"],
            temperature=data["temperature"],
            stepNumber=int(data["stepNumber"]),
            rotaryEvaporator=bool(data["rotaryEvaporator"]),
            atmosphere=data["atmosphere"],
            removedSpecies=[ChemicalEntry.from_dict(x) for x in data.get("removedSpecies", [])],
            targetVolume=data["targetVolume"],
            comment=data.get("comment", ""),
        )


@dataclass
class Dissolve:
    duration: str
    usedVesselName: str
    usedVesselType: str
    solvent: List[ChemicalEntryWithAmount]
    stepNumber: int
    atmosphere: str
    comment: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "solvent": [c.to_dict() for c in self.solvent],
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dissolve":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            solvent=[ChemicalEntryWithAmount.from_dict(x) for x in data.get("solvent", [])],
            stepNumber=int(data["stepNumber"]),
            atmosphere=data["atmosphere"],
            comment=data.get("comment", ""),
        )


@dataclass
class Separate:
    duration: str
    usedVesselName: str
    usedVesselType: str
    solvent: List[ChemicalEntryWithAmount]
    stepNumber: int
    separationType: str
    atmosphere: str
    comment: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "solvent": [c.to_dict() for c in self.solvent],
            "stepNumber": self.stepNumber,
            "separationType": self.separationType,
            "atmosphere": self.atmosphere,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Separate":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            solvent=[ChemicalEntryWithAmount.from_dict(x) for x in data.get("solvent", [])],
            stepNumber=int(data["stepNumber"]),
            separationType=data["separationType"],
            atmosphere=data["atmosphere"],
            comment=data.get("comment", ""),
        )


@dataclass
class Transfer:
    duration: str
    usedVesselName: str
    usedVesselType: str
    targetVesselName: str
    targetVesselType: str
    stepNumber: int
    isLayered: bool
    transferedAmount: str
    comment: str
    atmosphere: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "targetVesselName": self.targetVesselName,
            "targetVesselType": self.targetVesselType,
            "stepNumber": self.stepNumber,
            "isLayered": self.isLayered,
            "transferedAmount": self.transferedAmount,
            "comment": self.comment,
            "atmosphere": self.atmosphere,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Transfer":
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            targetVesselName=data["targetVesselName"],
            targetVesselType=data["targetVesselType"],
            stepNumber=int(data["stepNumber"]),
            isLayered=bool(data["isLayered"]),
            transferedAmount=data["transferedAmount"],
            comment=data.get("comment", ""),
            atmosphere=data["atmosphere"],
        )


# ------------------------------
# Wrapper for a single step (any of the above)
# ------------------------------


StepVariant = Union[
    Add,
    HeatChill,
    Dry,
    Filter,
    Sonicate,
    Stir,
    Crystallization,
    Evaporate,
    Dissolve,
    Separate,
    Transfer,
]


_STEP_KEY_BY_TYPE: Dict[type, str] = {
    Add: "Add",
    HeatChill: "HeatChill",
    Dry: "Dry",
    Filter: "Filter",
    Sonicate: "Sonicate",
    Stir: "Stir",
    Crystallization: "Crystallization",
    Evaporate: "Evaporate",
    Dissolve: "Dissolve",
    Separate: "Separate",
    Transfer: "Transfer",
}


@dataclass
class Step:
    """A wrapper for a single synthesis step variant.

    Serialized as a single-key object like {"Add": { ... }} or {"Filter": { ... }}.
    """

    value: StepVariant

    def to_dict(self) -> Dict[str, Any]:
        key = _STEP_KEY_BY_TYPE[type(self.value)]
        return {key: self.value.to_dict()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Step":
        if not isinstance(data, dict) or len(data) != 1:
            raise ValueError("Each step must be a dict with exactly one key indicating the step type.")

        step_type, payload = next(iter(data.items()))
        if step_type == "Add":
            return cls(Add.from_dict(payload))
        if step_type == "HeatChill":
            return cls(HeatChill.from_dict(payload))
        if step_type == "Dry":
            return cls(Dry.from_dict(payload))
        if step_type == "Filter":
            return cls(Filter.from_dict(payload))
        if step_type == "Sonicate":
            return cls(Sonicate.from_dict(payload))
        if step_type == "Stir":
            return cls(Stir.from_dict(payload))
        if step_type == "Crystallization":
            return cls(Crystallization.from_dict(payload))
        if step_type == "Evaporate":
            return cls(Evaporate.from_dict(payload))
        if step_type == "Dissolve":
            return cls(Dissolve.from_dict(payload))
        if step_type == "Separate":
            return cls(Separate.from_dict(payload))
        if step_type == "Transfer":
            return cls(Transfer.from_dict(payload))

        raise ValueError(f"Unknown step type: {step_type}")


# ------------------------------
# Synthesis containers
# ------------------------------


@dataclass
class ProductSynthesis:
    productNames: List[str]
    productCCDCNumber: str
    steps: List[Step]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "productNames": self.productNames,
            "productCCDCNumber": self.productCCDCNumber,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProductSynthesis":
        return cls(
            productNames=list(data.get("productNames", [])),
            productCCDCNumber=data.get("productCCDCNumber", ""),
            steps=[Step.from_dict(x) for x in data.get("steps", [])],
        )


@dataclass
class SynthesisDocument:
    """Root document for synthesis steps.

    Serialized with a top-level key "Synthesis": [ ... ].
    """

    synthesis: List[ProductSynthesis]

    def to_dict(self) -> Dict[str, Any]:
        return {"Synthesis": [s.to_dict() for s in self.synthesis]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_to_file(self, filepath: str, indent: int = 2) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SynthesisDocument":
        items = [ProductSynthesis.from_dict(x) for x in data.get("Synthesis", [])]
        return cls(synthesis=items)

    @classmethod
    def from_json(cls, json_string: str) -> "SynthesisDocument":
        data = json.loads(json_string)
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, filepath: str) -> "SynthesisDocument":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def __len__(self) -> int:
        return len(self.synthesis)

    def __iter__(self):
        return iter(self.synthesis)

    def __str__(self) -> str:
        return f"SynthesisDocument(Synthesis={len(self.synthesis)})"

    def __repr__(self) -> str:
        return f"SynthesisDocument(Synthesis={self.synthesis})"


if __name__ == "__main__":
    # Minimal example showing construction and serialization
    example = SynthesisDocument(
        synthesis=[
            ProductSynthesis(
                productNames=["Example Product"],
                productCCDCNumber="CCDC 0000000",
                steps=[
                    Step(
                        Add(
                            usedVesselName="vessel 1",
                            usedVesselType="round bottom flask",
                            addedChemical=[
                                ChemicalEntryWithAmount(
                                    chemicalName=["water"], chemicalAmount="10 mL"
                                )
                            ],
                            stepNumber=1,
                            stir=True,
                            isLayered=False,
                            atmosphere="Air",
                            duration="5 min",
                            targetPH=7.0,
                            comment="",
                        )
                    )
                ],
            )
        ]
    )
    print(example.to_json())


