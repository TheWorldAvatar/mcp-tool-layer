from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import json
from datetime import datetime


@dataclass
class ChemicalInput:
    """Represents an input chemical in a synthesis step."""
    chemical: List[Dict[str, Any]]  # List of chemical details
    supplierName: str
    purity: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "chemical": self.chemical,
            "supplierName": self.supplierName,
            "purity": self.purity
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChemicalInput':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class ChemicalOutput:
    """Represents an output chemical from a synthesis step."""
    chemicalFormula: str
    names: List[str]
    yield_amount: str  # Using yield_amount to avoid Python keyword conflict
    CCDCNumber: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "chemicalFormula": self.chemicalFormula,
            "names": self.names,
            "yield": self.yield_amount,  # Map back to "yield" for JSON
            "CCDCNumber": self.CCDCNumber
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChemicalOutput':
        """Create instance from dictionary."""
        # Handle the yield field mapping
        yield_data = data.get("yield", "")
        return cls(
            chemicalFormula=data["chemicalFormula"],
            names=data["names"],
            yield_amount=yield_data,
            CCDCNumber=data["CCDCNumber"]
        )


@dataclass
class SynthesisStep:
    """Represents a single step in a synthesis procedure."""
    inputChemicals: List[ChemicalInput]
    outputChemical: List[ChemicalOutput]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "inputChemicals": [chem.to_dict() for chem in self.inputChemicals],
            "outputChemical": [chem.to_dict() for chem in self.outputChemical]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SynthesisStep':
        """Create instance from dictionary."""
        input_chems = [ChemicalInput.from_dict(chem) for chem in data["inputChemicals"]]
        output_chems = [ChemicalOutput.from_dict(chem) for chem in data["outputChemical"]]
        return cls(inputChemicals=input_chems, outputChemical=output_chems)


@dataclass
class SynthesisProcedure:
    """Represents a complete synthesis procedure."""
    procedureName: str
    steps: List[SynthesisStep]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "procedureName": self.procedureName,
            "steps": [step.to_dict() for step in self.steps]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SynthesisProcedure':
        """Create instance from dictionary."""
        steps = [SynthesisStep.from_dict(step) for step in data["steps"]]
        return cls(procedureName=data["procedureName"], steps=steps)


@dataclass
class Chemical:
    """
    Main class for chemical synthesis procedures.
    Can be directly constructed with all data at once.
    """
    synthesisProcedures: List[SynthesisProcedure]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the entire chemical object to dictionary representation."""
        return {
            "synthesisProcedures": [proc.to_dict() for proc in self.synthesisProcedures]
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize the chemical object to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save_to_file(self, filepath: str, indent: int = 2) -> None:
        """Save the chemical object to a JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Chemical':
        """Create a Chemical instance from dictionary data."""
        if "synthesisProcedures" in data:
            procedures = [SynthesisProcedure.from_dict(proc) for proc in data["synthesisProcedures"]]
            return cls(synthesisProcedures=procedures)
        return cls(synthesisProcedures=[])
    
    @classmethod
    def from_json(cls, json_string: str) -> 'Chemical':
        """Create a Chemical instance from JSON string."""
        data = json.loads(json_string)
        return cls.from_dict(data)
    
    @classmethod
    def from_file(cls, filepath: str) -> 'Chemical':
        """Create a Chemical instance from a JSON file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def __len__(self) -> int:
        """Return the number of synthesis procedures."""
        return len(self.synthesisProcedures)
    
    def __iter__(self):
        """Iterate over synthesis procedures."""
        return iter(self.synthesisProcedures)
    
    def __str__(self) -> str:
        """String representation of the Chemical object."""
        return f"Chemical(synthesisProcedures={len(self.synthesisProcedures)})"
    
    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"Chemical(synthesisProcedures={self.synthesisProcedures})"
    
    
if __name__ == "__main__":
    chemical = Chemical(
        synthesisProcedures=[
            SynthesisProcedure(
                procedureName="Synthesis 1",
                steps=[
                    SynthesisStep(
                        inputChemicals=[
                            ChemicalInput(
                                chemical=[
                                    {
                                        "name": "Chemical 1",
                                        "formula": "C1H1",
                                        "CAS": "123-45-6"
                                    }
                                ],
                                supplierName="Supplier 1",
                                purity="99%"
                            )
                        ],
                        outputChemical=[
                            ChemicalOutput(
                                chemicalFormula="C1H1",
                                names=["Chemical 1"],
                                yield_amount="100%",
                                CCDCNumber="123456"
                            )
                        ]
                    )
                ]
            )
        ]
    )

    print(chemical.to_json())