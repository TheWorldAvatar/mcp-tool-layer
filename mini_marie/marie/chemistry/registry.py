"""Chemistry Blazegraph namespace registry and T-Box paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

TBOX_ROOT = Path(__file__).resolve().parents[1] / "docs" / "resources" / "tbox"
CHEMISTRY_HOST = "https://theworldavatar.io/chemistry/blazegraph/namespace"

NAMESPACES: Dict[str, Dict[str, Any]] = {
    "ontospecies": {
        "label": "OntoSpecies",
        "ontology_prefix": "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#",
        "tbox_files": ["ontospecies/OntoSpecies_v2.owl"],
        "description": "Chemical species, identifiers, spectra (PubChem/ChEBI).",
    },
    "ontokin": {
        "label": "OntoKin",
        "ontology_prefix": "http://www.theworldavatar.com/ontology/ontokin/OntoKin.owl#",
        "tbox_files": ["ontokin/OntoKin.owl"],
        "description": "Reaction mechanisms, kinetic models, thermodynamic properties.",
    },
    "ontocompchem": {
        "label": "OntoCompChem",
        "ontology_prefix": "http://www.theworldavatar.com/ontology/ontocompchem/OntoCompChem.owl#",
        "tbox_files": ["ontocompchem/ontocompchem.owl"],
        "description": "Quantum chemistry calculations (Gaussian, orbitals, geometries).",
    },
    "ontozeolite": {
        "label": "OntoZeolite + OntoCrystal",
        "ontology_prefix": "http://www.theworldavatar.com/kg/ontozeolite/",
        "tbox_files": ["ontozeolite/ontozeolite.owl", "ontozeolite/ontocrystal.owl"],
        "description": "Zeolite frameworks/materials and crystallographic data.",
    },
    "ontomops": {
        "label": "OntoMOPs",
        "ontology_prefix": "https://www.theworldavatar.com/kg/ontomops/",
        "tbox_files": ["ontomops/ontomops-ogm.ttl"],
        "description": "Metal-organic polyhedra, CBUs, assembly models (T-box; A-box on MOPs stack).",
        "endpoint_note": "Blazegraph namespace empty; use twa-mops or mof-twa for instance data.",
    },
    "ontoprovenance": {
        "label": "OntoProvenance",
        "ontology_prefix": "http://www.theworldavatar.com/ontology/ontoprovenance/OntoProvenance.owl#",
        "tbox_files": ["ontoprovenance/OntoProvenance.owl"],
        "description": "Authors, publications, provenance metadata.",
    },
    "ontopesscan": {
        "label": "OntoPESScan",
        "ontology_prefix": "http://www.theworldavatar.com/ontology/ontopesscan/OntoPESScan.owl#",
        "tbox_files": ["ontopesscan/OntoPESScan.owl"],
        "description": "Photoelectron spectroscopy scan ontology.",
    },
}


def endpoint(ns: str) -> str:
    return f"{CHEMISTRY_HOST}/{ns}/sparql"


def tbox_paths(ns: str) -> List[Path]:
    meta = NAMESPACES[ns]
    return [TBOX_ROOT / rel for rel in meta["tbox_files"]]
