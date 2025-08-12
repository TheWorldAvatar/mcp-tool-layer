from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
import os


@dataclass
class ChemicalInfo:
    """Represents chemical information for synthesis steps."""
    chemicalName: List[str]
    chemicalAmount: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "chemicalName": self.chemicalName,
            "chemicalAmount": self.chemicalAmount
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChemicalInfo':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class AddStep:
    """Represents an Add synthesis step."""
    usedVesselName: str
    usedVesselType: str
    addedChemical: List[ChemicalInfo]
    stepNumber: int
    atmosphere: str
    duration: str
    stir: bool
    targetPH: Optional[float]
    isLayered: bool
    comment: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "addedChemical": [chem.to_dict() for chem in self.addedChemical],
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "duration": self.duration,
            "stir": self.stir,
            "targetPH": self.targetPH,
            "isLayered": self.isLayered,
            "comment": self.comment
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AddStep':
        """Create instance from dictionary."""
        return cls(
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            addedChemical=[ChemicalInfo.from_dict(chem) for chem in data["addedChemical"]],
            stepNumber=data["stepNumber"],
            atmosphere=data["atmosphere"],
            duration=data["duration"],
            stir=data["stir"],
            targetPH=data.get("targetPH"),
            isLayered=data["isLayered"],
            comment=data["comment"]
        )


@dataclass
class HeatChillStep:
    """Represents a HeatChill synthesis step."""
    duration: str
    usedDevice: str
    targetTemperature: str
    heatingCoolingRate: str
    underVacuum: bool
    usedVesselName: str
    usedVesselType: str
    sealedVessel: bool
    stepNumber: int
    comment: str
    atmosphere: str
    stir: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedDevice": self.usedDevice,
            "targetTemperature": self.targetTemperature,
            "heatingCoolingRate": self.heatingCoolingRate,
            "underVacuum": self.underVacuum,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "sealedVessel": self.sealedVessel,
            "stepNumber": self.stepNumber,
            "comment": self.comment,
            "atmosphere": self.atmosphere,
            "stir": self.stir
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HeatChillStep':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class FilterStep:
    """Represents a Filter synthesis step."""
    washingSolvent: List[ChemicalInfo]
    vacuumFiltration: bool
    numberOfFiltrations: int
    usedVesselName: str
    usedVesselType: str
    stepNumber: int
    comment: str
    atmosphere: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "washingSolvent": [solvent.to_dict() for solvent in self.washingSolvent],
            "vacuumFiltration": self.vacuumFiltration,
            "numberOfFiltrations": self.numberOfFiltrations,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "stepNumber": self.stepNumber,
            "comment": self.comment,
            "atmosphere": self.atmosphere
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FilterStep':
        """Create instance from dictionary."""
        return cls(
            washingSolvent=[ChemicalInfo.from_dict(solvent) for solvent in data["washingSolvent"]],
            vacuumFiltration=data["vacuumFiltration"],
            numberOfFiltrations=data["numberOfFiltrations"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            stepNumber=data["stepNumber"],
            comment=data["comment"],
            atmosphere=data["atmosphere"]
        )


@dataclass
class StirStep:
    """Represents a Stir synthesis step."""
    duration: str
    usedVesselName: str
    usedVesselType: str
    stepNumber: int
    atmosphere: str
    temperature: str
    wait: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "temperature": self.temperature,
            "wait": self.wait
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StirStep':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class CrystallizationStep:
    """Represents a Crystallization synthesis step."""
    usedVesselName: str
    usedVesselType: str
    targetTemperature: str
    stepNumber: int
    duration: str
    atmosphere: str
    comment: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "targetTemperature": self.targetTemperature,
            "stepNumber": self.stepNumber,
            "duration": self.duration,
            "atmosphere": self.atmosphere,
            "comment": self.comment
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CrystallizationStep':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class DryStep:
    """Represents a Dry synthesis step."""
    duration: str
    usedVesselName: str
    usedVesselType: str
    pressure: str
    temperature: str
    stepNumber: int
    atmosphere: str
    dryingAgent: List[ChemicalInfo]
    comment: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "dryingAgent": [agent.to_dict() for agent in self.dryingAgent],
            "comment": self.comment
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DryStep':
        """Create instance from dictionary."""
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            pressure=data["pressure"],
            temperature=data["temperature"],
            stepNumber=data["stepNumber"],
            atmosphere=data["atmosphere"],
            dryingAgent=[ChemicalInfo.from_dict(agent) for agent in data["dryingAgent"]],
            comment=data["comment"]
        )


@dataclass
class EvaporateStep:
    """Represents an Evaporate synthesis step."""
    duration: str
    usedVesselName: str
    usedVesselType: str
    pressure: str
    temperature: str
    stepNumber: int
    rotaryEvaporator: bool
    atmosphere: str
    removedSpecies: List[ChemicalInfo]
    targetVolume: str
    comment: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "stepNumber": self.stepNumber,
            "rotaryEvaporator": self.rotaryEvaporator,
            "atmosphere": self.atmosphere,
            "removedSpecies": [species.to_dict() for species in self.removedSpecies],
            "targetVolume": self.targetVolume,
            "comment": self.comment
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EvaporateStep':
        """Create instance from dictionary."""
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            pressure=data["pressure"],
            temperature=data["temperature"],
            stepNumber=data["stepNumber"],
            rotaryEvaporator=data["rotaryEvaporator"],
            atmosphere=data["atmosphere"],
            removedSpecies=[ChemicalInfo.from_dict(species) for species in data["removedSpecies"]],
            targetVolume=data["targetVolume"],
            comment=data["comment"]
        )


@dataclass
class DissolveStep:
    """Represents a Dissolve synthesis step."""
    duration: str
    usedVesselName: str
    usedVesselType: str
    solvent: List[ChemicalInfo]
    stepNumber: int
    atmosphere: str
    comment: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "solvent": [sol.to_dict() for sol in self.solvent],
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere,
            "comment": self.comment
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DissolveStep':
        """Create instance from dictionary."""
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            solvent=[ChemicalInfo.from_dict(sol) for sol in data["solvent"]],
            stepNumber=data["stepNumber"],
            atmosphere=data["atmosphere"],
            comment=data["comment"]
        )


@dataclass
class SeparateStep:
    """Represents a Separate synthesis step."""
    duration: str
    usedVesselName: str
    usedVesselType: str
    solvent: List[ChemicalInfo]
    stepNumber: int
    separationType: str
    atmosphere: str
    comment: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "solvent": [sol.to_dict() for sol in self.solvent],
            "stepNumber": self.stepNumber,
            "separationType": self.separationType,
            "atmosphere": self.atmosphere,
            "comment": self.comment
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SeparateStep':
        """Create instance from dictionary."""
        return cls(
            duration=data["duration"],
            usedVesselName=data["usedVesselName"],
            usedVesselType=data["usedVesselType"],
            solvent=[ChemicalInfo.from_dict(sol) for sol in data["solvent"]],
            stepNumber=data["stepNumber"],
            separationType=data["separationType"],
            atmosphere=data["atmosphere"],
            comment=data["comment"]
        )


@dataclass
class TransferStep:
    """Represents a Transfer synthesis step."""
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
        """Convert to dictionary representation."""
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
            "atmosphere": self.atmosphere
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TransferStep':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class SonicateStep:
    """Represents a Sonicate synthesis step."""
    duration: str
    usedVesselName: str
    usedVesselType: str
    stepNumber: int
    atmosphere: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "duration": self.duration,
            "usedVesselName": self.usedVesselName,
            "usedVesselType": self.usedVesselType,
            "stepNumber": self.stepNumber,
            "atmosphere": self.atmosphere
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SonicateStep':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class SynthesisProduct:
    """Represents a synthesis product with its steps."""
    productNames: List[str]
    productCCDCNumber: str
    steps: List[Dict[str, Any]]  # This will contain the various step types
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "productNames": self.productNames,
            "productCCDCNumber": self.productCCDCNumber,
            "steps": self.steps
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SynthesisProduct':
        """Create instance from dictionary."""
        return cls(
            productNames=data["productNames"],
            productCCDCNumber=data["productCCDCNumber"],
            steps=data["steps"]
        )


class Synthesis:
    """
    Main class for managing synthesis procedures.
    """
    
    def __init__(self):
        self.Synthesis: List[SynthesisProduct] = []
    
    def add_product(self, product: SynthesisProduct) -> None:
        """Add a synthesis product to the collection."""
        self.Synthesis.append(product)
    
    def remove_product(self, index: int) -> bool:
        """Remove a product by index. Returns True if found and removed."""
        if 0 <= index < len(self.Synthesis):
            del self.Synthesis[index]
            return True
        return False
    
    def get_product(self, index: int) -> Optional[SynthesisProduct]:
        """Get a product by index."""
        if 0 <= index < len(self.Synthesis):
            return self.Synthesis[index]
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the entire synthesis object to dictionary representation."""
        return {
            "Synthesis": [product.to_dict() for product in self.Synthesis]
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize the synthesis object to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save_to_file(self, filepath: str, indent: int = 2) -> None:
        """Save the synthesis object to a JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Synthesis':
        """Create a synthesis instance from dictionary data."""
        synthesis = cls()
        if "Synthesis" in data:
            products = [SynthesisProduct.from_dict(product) for product in data["Synthesis"]]
            synthesis.Synthesis = products
        return synthesis
    
    @classmethod
    def from_json(cls, json_string: str) -> 'Synthesis':
        """Create a synthesis instance from JSON string."""
        data = json.loads(json_string)
        return cls.from_dict(data)
    
    @classmethod
    def from_file(cls, filepath: str) -> 'Synthesis':
        """Create a synthesis instance from a JSON file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def __len__(self) -> int:
        """Return the number of synthesis products."""
        return len(self.Synthesis)
    
    def __iter__(self):
        """Iterate over synthesis products."""
        return iter(self.Synthesis)
    
    def __str__(self) -> str:
        """String representation of the synthesis object."""
        return f"Synthesis(Products={len(self.Synthesis)})"
    
    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"Synthesis(Products={self.Synthesis})"


def adaptive_schema():
    """
    Generates a JSON schema defining the structure for a chemical synthesis process.
    Lists all possible synthesis step types used in the synthesis to pass it to the
    llm and get a boolean list with the step types used in a synthesis to generate 
    tailored schemas based on the currently processed synthesis procedure.
    The schema ensures that only specific boolean properties related to synthesis
    steps are allowed, and all properties are required. (OpenAi requirements)

    Returns:
        dict: A dictionary representing the JSON schema.
    """
    schema = {
        "type": "json_schema",                            # Specifies the type of schema
        "json_schema": {
            "name": "chemicalSynthesis",                  # Name of the schema
            "schema": {
                "type": "object",                         # Defines the schema as an object type
                "properties": {                           # Defines the allowed properties and their types
                    "Add": {"type": "boolean"},
                    "HeatChill": {"type": "boolean"},
                    "Dry": {"type": "boolean"},
                    "Evaporate": {"type": "boolean"},
                    "Filter": {"type": "boolean"},
                    "Sonicate": {"type": "boolean"},
                    "Stir": {"type": "boolean"},
                    "Crystallization": {"type": "boolean"},
                    "Dissolve": {"type": "boolean"},
                    "Separate": {"type": "boolean"},
                    "Transfer": {"type": "boolean"}
                },
                "required": [                             # Specifies the required properties
                    "Add", "HeatChill", "Separate", "Transfer",
                    "Dry", "Evaporate", "Crystallization",
                    "Filter", "Sonicate", "Stir", "Dissolve"
                ],
                "additionalProperties": False             # Disallows any properties not explicitly defined
            },
            "strict": True                                # Ensures strict adherence to the schema
        }
    }
    return schema                                         # Returns the defined schema


def step_schema(dynamic_prompt):
    """
    Generates a JSON schema for a chemical synthesis procedure based on the steps provided
    in the dynamic_prompt dictionary.
    
    Parameters:
    dynamic_prompt (dict): A dictionary indicating which steps are present in the synthesis.
    (Based on adaptive_schema())
    
    Returns:
    dict: A JSON schema that structures the synthesis steps.
    """
    # Predefine empty dictionaries for each possible synthesis step
    add               = {}
    heat_chill        = {}
    filt              = {}
    crystal           = {}
    stir              = {}
    soni              = {}
    evap              = {}
    dry               = {}
    dissolve          = {}
    separate          = {}
    transfer          = {}
    
    # Fill the respective dictionary if a specific step is used
    if dynamic_prompt["Add"] == True:
        add.update({"type": "object",
            "properties": {
                "Add": {
                    "type": "object",
                    "properties": {
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "addedChemical":{ "type": "array",
                                        "items":{"type":"object",
                                                "properties":{
                                                    "chemicalName": { "type": "array",
                                                    "items":{"type":"string", "description": "Name of the chemical as given in the prompt"}},
                                                    "chemicalAmount": {"type": "string", "description": "Added amount of the chemcial used in this step."}
                                                },
                                                "required": ["chemicalName", "chemicalAmount"],
                        "additionalProperties": False
                    }, 
                        "description": "If a mixture of species is added make multiple entries each with chemcial names and chemical amount."},
                        "stepNumber": {"type": "integer"},
                        "stir": {"type": "boolean", "description": "true if stired while adding, false otherwise."},
                        "isLayered": {"type": "boolean", "description": "true if added component is layered on top of content in target vessel."},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "duration": {"type": "string", "description": "Time the addition takes. E.g. Added over 5 minutes."},
                        "targetPH": {"type": "number", "description": "If the step involves acidification note target Ph."},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."}
                    },
                    "required": ["usedVesselName", "usedVesselType", "addedChemical", "stepNumber",  "atmosphere", "duration", "stir", "targetPH", "isLayered", "comment"],
                    "additionalProperties": False
                }
            },
            "required": ["Add"],
            "additionalProperties": False
            })
    
    # Repeat for other steps (e.g., HeatChill, Dry, Filter, etc.)
    if dynamic_prompt["HeatChill"]:
        heat_chill.update({"type": "object",
            "properties": {
                "HeatChill": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string", "description": "Time the vessel is heated or cooled."},
                        "usedDevice": {"type": "string", "description": "Equipment used for heating or cooling."},
                        "targetTemperature": {"type": "string", "description": "Temperature the vessel is heated to."},
                        "heatingCoolingRate": {"type": "string", "description": "Temperature gradient that is applied to heat the vessel. For constant fill in 0 and for reflux state reflux."},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."},
                        "underVacuum": {"type": "boolean", "description": "If the heating is performed under reduced pressure or vacuum."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "sealedVessel": {"type": "boolean", "description": "true if the vessel is sealed. "},
                        "stir": {"type": "boolean", "description": "true if mixture is stirred while heating. "},
                        "stepNumber": {"type": "integer"},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                    },
                    "required": ["duration", "usedDevice", "targetTemperature", "heatingCoolingRate", "underVacuum", "usedVesselName", "usedVesselType", "sealedVessel", "stepNumber", "comment", "atmosphere", "stir"],
                    "additionalProperties": False
                }
            },
            "required": ["HeatChill"],
            "additionalProperties": False})
    
    if dynamic_prompt["Dry"]:
        dry.update({
            "type": "object",
            "properties": {
                "Dry": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string", "description": "Time the chemical is dried."},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "pressure": {"type": "string", "description": "Pressure applied for drying, often: reduced Pressue, Vacum, etc. "},
                        "temperature": {"type": "string", "description": "Temperature applied for drying."},
                        "stepNumber": {"type": "integer"},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "dryingAgent":{ "type": "array", "description": "Chemical used to support drying.",
                                        "items":{"type":"object",
                                                "properties":{
                                                    "chemicalName": { "type": "array",
                                                    "items":{"type":"string", "description": "Name of the chemical as given in the prompt"}}
                                                },
                                                "required": ["chemicalName"],
                        "additionalProperties": False
                    }}, 
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."}
                    },
                    "required": ["duration", "usedVesselName", "usedVesselType", "stepNumber", "atmosphere", "pressure", "temperature", "comment", "dryingAgent"],
                    "additionalProperties": False
                }
            },
            "required": ["Dry"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Filter"]:
        filt.update({"type": "object",
            "properties": {
                "Filter": {
                    "type": "object",
                    "properties": {
                        "washingSolvent":{ "type": "array",
                                        "items":{"type":"object",
                                                "properties":{
                                                    "chemicalName": { "type": "array",
                                                    "items":{"type":"string", "description": "Name of the chemical as given in the prompt"}},
                                                    "chemicalAmount": {"type": "string", "description": "Added amount of the chemcial used in this step."}},
                                                "required": ["chemicalName", "chemicalAmount"],
                        "additionalProperties": False}, 
                        "description": "If a mixture of species is added make multiple entries each with chemcial names and chemical amount."},
                        "vacuumFiltration": {"type": "boolean", "description": "True for vacuum filtration. "},
                        "numberOfFiltrations": {"type": "integer", "description": "Number of filtrations"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "stepNumber": {"type": "integer"},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                    },
                    "required": ["washingSolvent", "numberOfFiltrations", "usedVesselName", "usedVesselType", "stepNumber", "comment", "atmosphere","vacuumFiltration"],
                    "additionalProperties": False
                }
            },
            "required": ["Filter"],
            "additionalProperties": False
        })

    if dynamic_prompt["Sonicate"]:
        soni.update({"type": "object",
            "properties": {
                "Sonicate": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "stepNumber": {"type": "integer"},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                    },
                    "required": ["duration", "usedVesselName", "usedVesselType", "stepNumber", "atmosphere"],
                    "additionalProperties": False
                }
            },
            "required": ["Sonicate"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Stir"]:
        stir.update({
            "type": "object",
            "properties": {
                "Stir": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "stepNumber": {"type": "integer"},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "temperature": {"type": "string", "description": "Temperature at which it is stirred."},
                        "wait": {"type": "boolean", "description": "True if stirringrate = 0."},
                    },
                    "required": ["usedVesselName", "usedVesselType", "duration", "stepNumber", "atmosphere", "temperature", "wait"],
                    "additionalProperties": False
                }
            },
            "required": ["Stir"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Crystallization"]:
        crystal.update({
            "type": "object",
            "properties": {
                "Crystallization": {
                    "type": "object",
                    "properties": {
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "targetTemperature": {"type": "string"},
                        "stepNumber": {"type": "integer"},
                        "duration": {"type": "string"},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."}
                    },
                    "required": ["usedVesselName", "usedVesselType", "targetTemperature", "duration", "comment", "atmosphere", "stepNumber"],
                    "additionalProperties": False
                }
            },
            "required": ["Crystallization"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Evaporate"]:
        evap.update({"type": "object",
            "properties": {
                "Evaporate": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "pressure": {"type": "string"},
                        "temperature": {"type": "string"},
                        "stepNumber": {"type": "integer"},
                        "rotaryEvaporator": {"type": "boolean", "description": "True if rotary evaporator is used."},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "removedSpecies":{ "type": "array",
                                        "items":{"type":"object",
                                                "properties":{
                                                    "chemicalName": { "type": "array",
                                                    "items":{"type":"string", "description": "Name of the chemical as given in the prompt"}}},
                                                "required": ["chemicalName"],
                        "additionalProperties": False}, 
                        "description": "Species that is removed by evaporation."},
                        "targetVolume": {"type": "string", "description": "Volume to which mixture is evaporated."},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."}
                    },
                    "required": ["duration", "usedVesselName", "usedVesselType", "atmosphere", "stepNumber", "pressure", "temperature", "removedSpecies", "rotaryEvaporator", "targetVolume",  "comment"],
                    "additionalProperties": False
                }
            },
            "required": ["Evaporate"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Dissolve"]:
        dissolve.update({"type": "object",
            "properties": {
                "Dissolve": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "solvent":{ "type": "array",
                                        "items":{"type":"object",
                                                "properties":{
                                                    "chemicalName": { "type": "array",
                                                    "items":{"type":"string", "description": "Name of the chemical as given in the prompt. Make sure to not include multiple species."}},
                                                    "chemicalAmount": {"type": "string", "description": "Amount of the chemcial used in this step."}},
                                                "required": ["chemicalName", "chemicalAmount"],
                        "additionalProperties": False}, 
                        "description": "If a mixture of species is used make multiple entries each with chemcial names and chemical amount."},
                        "stepNumber": {"type": "integer"},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."}
                    },
                    "required": ["duration", "usedVesselName", "usedVesselType", "stepNumber", "solvent", "atmosphere", "comment"],
                    "additionalProperties": False
                }
            },
            "required": ["Dissolve"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Separate"]:
        separate.update({"type": "object",
            "properties": {
                "Separate": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "solvent":{ "type": "array",
                                        "items":{"type":"object",
                                                "properties":{
                                                    "chemicalName": { "type": "array",
                                                    "items":{"type":"string", "description": "Name of the chemical as given in the prompt"}},
                                                    "chemicalAmount": {"type": "string", "description": "Amount of the chemcial used in this step."}},
                                                "required": ["chemicalName", "chemicalAmount"],
                        "additionalProperties": False}, 
                        "description": "If a mixture of species is used make multiple entries each with chemcial names and chemical amount."},
                        "stepNumber": {"type": "integer"},
                        "separationType": {"type": "string", "description": "Separation type that is performed.",
                        "enum": ["extraction", "washing", "column", "centrifuge"]},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."}
                    },
                    "required": ["duration", "usedVesselName", "usedVesselType", "atmosphere", "stepNumber", "solvent", "separationType", "comment"],
                    "additionalProperties": False
                }
            },
            "required": ["Separate"],
            "additionalProperties": False
        })
    
    if dynamic_prompt["Transfer"]:
        transfer.update({"type": "object",
            "properties": {
                "Transfer": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "string"},
                        "usedVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "usedVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "targetVesselName": {"type": "string", "description": "Generic vessel name, e.g. vessel 1."},
                        "targetVesselType": {"type": "string", "description": "One of 7 vessel types.",
                        "enum": ["Teflon-lined stainless-steel vessel", "glass vial", "quartz tube", "round bottom flask", "glass scintillation vial", "pyrex tube", "schlenk flask"]},
                        "stepNumber": {"type": "integer"},
                        "isLayered": {"type": "boolean", "description": "true if transfered component is layered on top of content in target vessel."},
                        "transferedAmount": {"type": "string", "description": "volume or mass that is transfered if given."},
                        "comment": {"type": "string", "description": "Information that does not fit any other entry."},
                        "atmosphere": {"type": "string", "description": "indicates if step is conducted under N2 or Ar atmosphere.", 
                                        "enum": ["N2", "Ar", "Air", "N/A"]},
                    },
                    "required": ["duration", "usedVesselName", "atmosphere", "usedVesselType", "targetVesselName", "isLayered", "targetVesselType", "stepNumber", "transferedAmount", "comment"],
                    "additionalProperties": False
                }
            },
            "required": ["Transfer"],
            "additionalProperties": False
        })
    
    # Schema dictionary containing all defined step schemas
    stepList = [  
        add,
        heat_chill,
        filt,
        crystal,
        stir,
        soni,
        evap,
        dry,
        dissolve, 
        transfer, 
        separate
    ]
    
    # Filter out empty dictionaries
    stepList = [step for step in stepList if step != {}]
    
    step_schema_dict = {
        "type": "json_schema",
        "json_schema": {
            "name": "synthesis",
            "schema": {
                "type": "object",
                "properties": {
                    "Synthesis": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "productNames": { "type": "array",
                                        "items":{"type":"string"}},
                                "productCCDCNumber": {"type": "string"},
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "anyOf": stepList
                                    }
                                }
                            },
                            "required": ["productNames", "productCCDCNumber", "steps"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["Synthesis"],
                "additionalProperties": False
            },
            "strict": True
        }
    }
    
    print("schema: ", step_schema_dict)
    return step_schema_dict


if __name__ == "__main__":
    # Example usage
    synthesis = Synthesis()
    
    # Create a chemical info object
    chemical = ChemicalInfo(
        chemicalName=["Copper acetate", "Cu(OAc)2"],
        chemicalAmount="0.86 g"
    )
    
    # Create an add step
    add_step = AddStep(
        usedVesselName="vessel 1",
        usedVesselType="round bottom flask",
        addedChemical=[chemical],
        stepNumber=1,
        atmosphere="Air",
        duration="5 minutes",
        stir=True,
        targetPH=None,
        isLayered=False,
        comment="Add copper acetate to the vessel"
    )
    
    # Create a synthesis product
    product = SynthesisProduct(
        productNames=["MOP-1", "Metal-Organic Polyhedra"],
        productCCDCNumber="CCDC 2359340",
        steps=[{"Add": add_step.to_dict()}]
    )
    
    synthesis.add_product(product)
    
    print(synthesis.to_json())
    print(f"Total synthesis products: {len(synthesis)}")
