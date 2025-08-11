from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
import os


@dataclass
class CBUProcedure:
    """Represents a CBU procedure with exactly two chemical building units per MOP."""
    mopCCDCNumber: str
    cbuFormula1: str
    cbuSpeciesNames1: List[str]
    cbuFormula2: str
    cbuSpeciesNames2: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "mopCCDCNumber": self.mopCCDCNumber,
            "cbuFormula1": self.cbuFormula1,
            "cbuSpeciesNames1": self.cbuSpeciesNames1,
            "cbuFormula2": self.cbuFormula2,
            "cbuSpeciesNames2": self.cbuSpeciesNames2
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CBUProcedure':
        """Create instance from dictionary."""
        return cls(**data)


class CBU:
    """
    Main class for managing CBU (Chemical Building Unit) procedures.
    Each MOP has exactly two CBUs.
    """
    
    def __init__(self):
        self.synthesisProcedures: List[CBUProcedure] = []
    
    def add_procedure(self, procedure: CBUProcedure) -> None:
        """Add a CBU procedure to the collection."""
        self.synthesisProcedures.append(procedure)
    
    def remove_procedure(self, ccdc_number: str) -> bool:
        """Remove a procedure by CCDC number. Returns True if found and removed."""
        for i, proc in enumerate(self.synthesisProcedures):
            if proc.mopCCDCNumber == ccdc_number:
                del self.synthesisProcedures[i]
                return True
        return False
    
    def get_procedure(self, ccdc_number: str) -> Optional[CBUProcedure]:
        """Get a procedure by CCDC number."""
        for proc in self.synthesisProcedures:
            if proc.mopCCDCNumber == ccdc_number:
                return proc
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the entire CBU object to dictionary representation."""
        return {
            "synthesisProcedures": [proc.to_dict() for proc in self.synthesisProcedures]
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize the CBU object to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save_to_file(self, filepath: str, indent: int = 2) -> None:
        """Save the CBU object to a JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CBU':
        """Create a CBU instance from dictionary data."""
        cbu = cls()
        if "synthesisProcedures" in data:
            procedures = [CBUProcedure.from_dict(proc) for proc in data["synthesisProcedures"]]
            cbu.synthesisProcedures = procedures
        return cbu
    
    @classmethod
    def from_json(cls, json_string: str) -> 'CBU':
        """Create a CBU instance from JSON string."""
        data = json.loads(json_string)
        return cls.from_dict(data)
    
    @classmethod
    def from_file(cls, filepath: str) -> 'CBU':
        """Create a CBU instance from a JSON file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def validate_schema(self) -> bool:
        """Validate that the object conforms to the defined schema."""
        try:
            if not hasattr(self, 'synthesisProcedures'):
                return False
            
            for procedure in self.synthesisProcedures:
                required_fields = ['mopCCDCNumber', 'cbuFormula1', 'cbuSpeciesNames1', 'cbuFormula2', 'cbuSpeciesNames2']
                if not all(hasattr(procedure, field) for field in required_fields):
                    return False
                
                # Ensure species names are lists
                if not isinstance(procedure.cbuSpeciesNames1, list) or not isinstance(procedure.cbuSpeciesNames2, list):
                    return False
            
            return True
        except Exception:
            return False
    
    def __len__(self) -> int:
        """Return the number of CBU procedures."""
        return len(self.synthesisProcedures)
    
    def __iter__(self):
        """Iterate over CBU procedures."""
        return iter(self.synthesisProcedures)
    
    def __str__(self) -> str:
        """String representation of the CBU object."""
        return f"CBU(synthesisProcedures={len(self.synthesisProcedures)})"
    
    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"CBU(synthesisProcedures={self.synthesisProcedures})"


def cbu_schema():
    """
    Returns a JSON schema defining the structure for CBU data.
    
    The schema enforces a strict structure to ensure consistency in stored data.
    """
    schema = {
        "type": "json_schema",                                      # Indicates that this is a JSON schema definition
        "json_schema": {
            "name": "chemicalSynthesis",                            # Name of the schema
            "schema": {
                "type": "object",                                   # Root element is an object
                "properties": {
                    "synthesisProcedures": {                        # Key representing synthesis procedures
                        "type": "array",                            # Must be an array
                        "items": {
                            "type": "object",                       # Each item in the array is an object
                            "properties": {
                                "mopCCDCNumber": {"type": "string"},            # Unique identifier (better than MOP name)
                                "cbuFormula1": {"type": "string"},              # First chemical building unit formula
                                "cbuSpeciesNames1": {
                                    "type": "array",                            # List of species names
                                    "items": {"type": "string"}                 # Each species name is a string
                                },
                                "cbuFormula2": {"type": "string"},              # Second chemical building unit formula
                                "cbuSpeciesNames2": { 
                                    "type": "array",                            # List of species names
                                    "items": {"type": "string"}                 # Each species name is a string
                                }
                            },
                            "required": [                                       # Fields that must be present in each object
                                "mopCCDCNumber", "cbuFormula1", 
                                "cbuSpeciesNames1", "cbuFormula2", 
                                "cbuSpeciesNames2"
                            ],
                            "additionalProperties": False                       # Prevents unspecified properties
                        }
                    }
                },
                "required": ["synthesisProcedures"],                        # Top-level required field
                "additionalProperties": False                                # Disallows extra fields at the root level
            },
            "strict": True                                              # Ensures strict validation
        }
    }
    return schema


if __name__ == "__main__":
    # Example usage
    cbu = CBU()
    
    # Add a CBU procedure
    procedure = CBUProcedure(
        mopCCDCNumber="CCDC 2359340",
        cbuFormula1="V6O6(OCH3)9(SO4)",
        cbuSpeciesNames1=["{V6S}", "V6 cluster"],
        cbuFormula2="NDBDC",
        cbuSpeciesNames2=["4,4'-(naphthalene-1,4-diyl)dibenzoic acid", "H2NDBDC"]
    )
    
    cbu.add_procedure(procedure)
    
    # Add another procedure
    procedure2 = CBUProcedure(
        mopCCDCNumber="CCDC 2359341",
        cbuFormula1="V6O6(OCH3)9(SO4)",
        cbuSpeciesNames1=["{V6S}", "V6 cluster"],
        cbuFormula2="ADBDC",
        cbuSpeciesNames2=["4,4'-(anthracene-9,10-diyl)dibenzoic acid", "H2ADBDC"]
    )
    
    cbu.add_procedure(procedure2)
    
    print(cbu.to_json())
    print(f"Total CBU procedures: {len(cbu)}")