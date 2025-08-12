from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
import os


@dataclass
class HNMRDevice:
    """Represents an HNMR device."""
    deviceName: str
    frequency: str
    solventNames: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "deviceName": self.deviceName,
            "frequency": self.frequency,
            "solventNames": self.solventNames
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HNMRDevice':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class ElementalAnalysisDevice:
    """Represents an Elemental Analysis device."""
    deviceName: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "deviceName": self.deviceName
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ElementalAnalysisDevice':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class InfraredSpectroscopyDevice:
    """Represents an Infrared Spectroscopy device."""
    deviceName: str
    solventNames: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "deviceName": self.deviceName,
            "solventNames": self.solventNames
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InfraredSpectroscopyDevice':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class HNMRData:
    """Represents HNMR characterisation data."""
    shifts: str
    solvent: str
    temperature: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "shifts": self.shifts,
            "solvent": self.solvent,
            "temperature": self.temperature
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HNMRData':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class ElementalAnalysisData:
    """Represents Elemental Analysis characterisation data."""
    weightPercentageCalculated: str
    weightPercentageExperimental: str
    chemicalFormula: str
    measurementDevice: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "weightPercentageCalculated": self.weightPercentageCalculated,
            "weightPercentageExperimental": self.weightPercentageExperimental,
            "chemicalFormula": self.chemicalFormula,
            "measurementDevice": self.measurementDevice
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ElementalAnalysisData':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class InfraredSpectroscopyData:
    """Represents Infrared Spectroscopy characterisation data."""
    material: str
    bands: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "material": self.material,
            "bands": self.bands
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InfraredSpectroscopyData':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class CharacterisationItem:
    """Represents characterisation data for a product."""
    productNames: List[str]
    productCCDCNumber: str
    HNMR: HNMRData
    ElementalAnalysis: ElementalAnalysisData
    InfraredSpectroscopy: InfraredSpectroscopyData
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "productNames": self.productNames,
            "productCCDCNumber": self.productCCDCNumber,
            "HNMR": self.HNMR.to_dict(),
            "ElementalAnalysis": self.ElementalAnalysis.to_dict(),
            "InfraredSpectroscopy": self.InfraredSpectroscopy.to_dict()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CharacterisationItem':
        """Create instance from dictionary."""
        return cls(
            productNames=data["productNames"],
            productCCDCNumber=data["productCCDCNumber"],
            HNMR=HNMRData.from_dict(data["HNMR"]),
            ElementalAnalysis=ElementalAnalysisData.from_dict(data["ElementalAnalysis"]),
            InfraredSpectroscopy=InfraredSpectroscopyData.from_dict(data["InfraredSpectroscopy"])
        )


@dataclass
class CharacterisationDevice:
    """Represents a characterisation device with its associated data."""
    HNMRDevice: HNMRDevice
    ElementalAnalysisDevice: ElementalAnalysisDevice
    InfraredSpectroscopyDevice: InfraredSpectroscopyDevice
    Characterisation: List[CharacterisationItem]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "HNMRDevice": self.HNMRDevice.to_dict(),
            "ElementalAnalysisDevice": self.ElementalAnalysisDevice.to_dict(),
            "InfraredSpectroscopyDevice": self.InfraredSpectroscopyDevice.to_dict(),
            "Characterisation": [item.to_dict() for item in self.Characterisation]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CharacterisationDevice':
        """Create instance from dictionary."""
        return cls(
            HNMRDevice=HNMRDevice.from_dict(data["HNMRDevice"]),
            ElementalAnalysisDevice=ElementalAnalysisDevice.from_dict(data["ElementalAnalysisDevice"]),
            InfraredSpectroscopyDevice=InfraredSpectroscopyDevice.from_dict(data["InfraredSpectroscopyDevice"]),
            Characterisation=[CharacterisationItem.from_dict(item) for item in data["Characterisation"]]
        )


class Characterisation:
    """
    Main class for managing characterisation devices and data.
    """
    
    def __init__(self):
        self.Devices: List[CharacterisationDevice] = []
    
    def add_device(self, device: CharacterisationDevice) -> None:
        """Add a characterisation device to the collection."""
        self.Devices.append(device)
    
    def remove_device(self, index: int) -> bool:
        """Remove a device by index. Returns True if found and removed."""
        if 0 <= index < len(self.Devices):
            del self.Devices[index]
            return True
        return False
    
    def get_device(self, index: int) -> Optional[CharacterisationDevice]:
        """Get a device by index."""
        if 0 <= index < len(self.Devices):
            return self.Devices[index]
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the entire characterisation object to dictionary representation."""
        return {
            "Devices": [device.to_dict() for device in self.Devices]
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize the characterisation object to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save_to_file(self, filepath: str, indent: int = 2) -> None:
        """Save the characterisation object to a JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Characterisation':
        """Create a characterisation instance from dictionary data."""
        char = cls()
        if "Devices" in data:
            devices = [CharacterisationDevice.from_dict(device) for device in data["Devices"]]
            char.Devices = devices
        return char
    
    @classmethod
    def from_json(cls, json_string: str) -> 'Characterisation':
        """Create a characterisation instance from JSON string."""
        data = json.loads(json_string)
        return cls.from_dict(data)
    
    @classmethod
    def from_file(cls, filepath: str) -> 'Characterisation':
        """Create a characterisation instance from a JSON file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def validate_schema(self) -> bool:
        """Validate that the object conforms to the defined schema."""
        try:
            if not hasattr(self, 'Devices'):
                return False
            
            for device in self.Devices:
                required_fields = ['HNMRDevice', 'ElementalAnalysisDevice', 'InfraredSpectroscopyDevice', 'Characterisation']
                if not all(hasattr(device, field) for field in required_fields):
                    return False
                
                # Validate HNMR device
                if not hasattr(device.HNMRDevice, 'deviceName') or not hasattr(device.HNMRDevice, 'frequency') or not hasattr(device.HNMRDevice, 'solventNames'):
                    return False
                
                # Validate Elemental Analysis device
                if not hasattr(device.ElementalAnalysisDevice, 'deviceName'):
                    return False
                
                # Validate Infrared Spectroscopy device
                if not hasattr(device.InfraredSpectroscopyDevice, 'deviceName') or not hasattr(device.InfraredSpectroscopyDevice, 'solventNames'):
                    return False
                
                # Validate characterisation items
                for item in device.Characterisation:
                    if not hasattr(item, 'productNames') or not hasattr(item, 'productCCDCNumber'):
                        return False
                    if not hasattr(item, 'HNMR') or not hasattr(item, 'ElementalAnalysis') or not hasattr(item, 'InfraredSpectroscopy'):
                        return False
            
            return True
        except Exception:
            return False
    
    def __len__(self) -> int:
        """Return the number of characterisation devices."""
        return len(self.Devices)
    
    def __iter__(self):
        """Iterate over characterisation devices."""
        return iter(self.Devices)
    
    def __str__(self) -> str:
        """String representation of the characterisation object."""
        return f"Characterisation(Devices={len(self.Devices)})"
    
    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"Characterisation(Devices={self.Devices})"


def characterisation_schema():
    """
    Defines a JSON schema for characterisation devices and their associated characterisation data.
    
    Returns:
        dict: A dictionary representing the JSON schema for characterisation devices.
    """
    schema = {    
        "type": "json_schema",
        "json_schema": {
            "name": "characterisationDevices",
            "schema": {
                "type": "object",
                "properties": {
                    "Devices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                # Device schema for HNMR (Hydrogen Nuclear Magnetic Resonance)
                                "HNMRDevice": {
                                    "type": "object",
                                    "properties": {
                                        "deviceName": { "type": "string" },
                                        "frequency": { "type": "string" },
                                        "solventNames": { "type": "array", "items": {"type":"string"} }
                                    },
                                    "required": ["deviceName", "frequency", "solventNames"],
                                    "additionalProperties": False
                                },
                                # Device schema for Elemental Analysis
                                "ElementalAnalysisDevice": {
                                    "type": "object",
                                    "properties": {
                                        "deviceName": { "type": "string" }
                                    },
                                    "required": ["deviceName"],
                                    "additionalProperties": False
                                },
                                # Device schema for Infrared Spectroscopy
                                "InfraredSpectroscopyDevice": {
                                    "type": "object",
                                    "properties": {
                                        "deviceName": { "type": "string" },
                                        "solventNames": { "type": "array", "items": {"type":"string"} }
                                    },
                                    "required": ["deviceName", "solventNames"],
                                    "additionalProperties": False
                                },
                                # Characterisation data for products
                                "Characterisation": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "productNames": { "type": "array",
                                                    "items":{"type":"string"}},
                                            "productCCDCNumber": { "type": "string" },
                                            # HNMR characterisation details
                                            "HNMR": {
                                                "type": "object",
                                                "properties": {
                                                    "shifts": { "type": "string" },
                                                    "solvent": { "type": "string" },
                                                    "temperature": { "type": "string" }
                                                },
                                                "required": ["shifts", "solvent", "temperature"],
                                                "additionalProperties": False
                                            },
                                            # Elemental Analysis characterisation details
                                            "ElementalAnalysis": {
                                                "type": "object",
                                                "properties": {
                                                    "weightPercentageCalculated": { "type": "string" },
                                                    "weightPercentageExperimental": { "type": "string" },
                                                    "chemicalFormula": { "type": "string" },
                                                    "measurementDevice": { "type": "string" }
                                                },
                                                "required": ["weightPercentageCalculated", "weightPercentageExperimental", "chemicalFormula", "measurementDevice"],
                                                "additionalProperties": False
                                            },
                                            # Infrared Spectroscopy characterisation details
                                            "InfraredSpectroscopy": {
                                                "type": "object",
                                                "properties": {
                                                    "material": { "type": "string" },
                                                    "bands": { "type": "string" }
                                                },
                                                "required": ["material", "bands"],
                                                "additionalProperties": False
                                            }
                                        },
                                        "required": ["productNames", "productCCDCNumber", "HNMR", "ElementalAnalysis", "InfraredSpectroscopy"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["HNMRDevice", "ElementalAnalysisDevice", "InfraredSpectroscopyDevice", "Characterisation"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["Devices"],
                "additionalProperties": False
            }
        }
    }
    return schema


if __name__ == "__main__":
    # Example usage
    char = Characterisation()
    
    # Create HNMR device
    hnmr_device = HNMRDevice(
        deviceName="Bruker Avance III 400 MHz",
        frequency="400 MHz",
        solventNames=["CDCl3", "DMSO-d6"]
    )
    
    # Create Elemental Analysis device
    ea_device = ElementalAnalysisDevice(
        deviceName="PerkinElmer 2400 Series II"
    )
    
    # Create Infrared Spectroscopy device
    ir_device = InfraredSpectroscopyDevice(
        deviceName="PerkinElmer Spectrum Two",
        solventNames=["KBr", "ATR"]
    )
    
    # Create characterisation data
    char_data = CharacterisationItem(
        productNames=["MOP-1", "Metal-Organic Polyhedra"],
        productCCDCNumber="CCDC 2359340",
        HNMR=HNMRData(
            shifts="δ 8.5 (s, 2H), 7.2 (d, 4H)",
            solvent="CDCl3",
            temperature="298 K"
        ),
        ElementalAnalysis=ElementalAnalysisData(
            weightPercentageCalculated="C: 45.2%, H: 3.1%, N: 8.9%",
            weightPercentageExperimental="C: 44.8%, H: 3.3%, N: 8.7%",
            chemicalFormula="C24H16N4O8",
            measurementDevice="PerkinElmer 2400 Series II"
        ),
        InfraredSpectroscopy=InfraredSpectroscopyData(
            material="KBr pellet",
            bands="ν 1650 cm⁻¹ (C=O), 1600 cm⁻¹ (C=C), 1250 cm⁻¹ (C-N)"
        )
    )
    
    # Create characterisation device
    device = CharacterisationDevice(
        HNMRDevice=hnmr_device,
        ElementalAnalysisDevice=ea_device,
        InfraredSpectroscopyDevice=ir_device,
        Characterisation=[char_data]
    )
    
    char.add_device(device)
    
    print(char.to_json())
    print(f"Total characterisation devices: {len(char)}")
