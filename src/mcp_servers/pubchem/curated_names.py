"""Curated ligand identities that PubChem does not register.

These paper-specific alkoxy / amide isophthalates return 404 from PUG REST.
The local CBU CSV already has their SMILES; serve that instead of letting
the organic agent loop name variants for hundreds of tool turns.
"""

from __future__ import annotations

import re
from typing import Any


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


# SMILES are the CBU-database deprotonated forms used by chemistry MCP.
_LIGANDS: list[dict[str, Any]] = [
    {
        "formula": "C12H14O5",
        "canonical_smiles": "CCCCOc1cc(C(=O)[O-])cc(C(=O)[O-])c1",
        "cbu_formula": "[(C6H3)O(CH2)3CH3(CO2)2]",
        "names": [
            "5-butoxyisophthalic acid",
            "5-butoxy-1,3-benzenedicarboxylic acid",
            "5-OBu-1,3-H2bdc",
            "5-OBu-bdc",
            "Cu_OBu-bdc",
            "OBu-bdc",
        ],
    },
    {
        "formula": "C10H10O5",
        "canonical_smiles": "CCOc1cc(C(=O)[O-])cc(C(=O)[O-])c1",
        "cbu_formula": "[(C6H3)(OCH2CH3)(CO2)2]",
        "names": [
            "5-ethoxyisophthalic acid",
            "5-ethoxy-1,3-benzenedicarboxylic acid",
            "5-OEt-1,3-H2bdc",
            "5-OEt-bdc",
            "Cu_OEt-bdc",
            "OEt-bdc",
        ],
    },
    {
        "formula": "C11H12O5",
        "canonical_smiles": "CCCOc1cc(C(=O)[O-])cc(C(=O)[O-])c1",
        "cbu_formula": "[(C6H3)(OCH2CH2CH3)(CO2)2]",
        "names": [
            "5-propoxyisophthalic acid",
            "5-propoxy-1,3-benzenedicarboxylic acid",
            "5-OPr-1,3-H2bdc",
            "5-OPr-bdc",
            "Cu_OPr-bdc",
            "OPr-bdc",
        ],
    },
    {
        "formula": "C14H18O5",
        "canonical_smiles": "CCCCCOc1cc(C(=O)[O-])cc(C(=O)[O-])c1",
        "cbu_formula": "[(C6H3)O(CH2)4CH3(CO2)2]",
        "names": [
            "5-pentoxyisophthalic acid",
            "5-pentyloxyisophthalic acid",
            "5-pentoxy-1,3-benzenedicarboxylic acid",
            "5-pentyloxy-1,3-benzenedicarboxylic acid",
            "5-OPent-1,3-H2bdc",
            "5-OPent-bdc",
            "Cu_OPent-bdc",
            "OPent-bdc",
        ],
    },
    {
        "formula": "C13H15NO5",
        "canonical_smiles": "CC(C)(C)C(=O)Nc1cc(C(=O)[O-])cc(C(=O)[O-])c1",
        "cbu_formula": "[(C6H3)NHCOC(CH3)3(CO2)2]",
        "names": [
            "5-tert-butylamide-1,3-benzenedicarboxylic acid",
            "5-tertbutylamide-1,3-benzenedicarboxylic acid",
            "5-HN(CO)tBu-1,3-H2bdc",
            "5-tBu-amide-bdc",
            "tBu-amide-bdc",
        ],
    },
]


def _index() -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for ligand in _LIGANDS:
        for name in ligand["names"]:
            table[_key(name)] = ligand
    return table


_INDEX = _index()


def lookup_curated_ligand(name: str) -> dict[str, Any] | None:
    """Exact alias hit, then a token contained in a longer entity label."""
    raw = (name or "").strip()
    if not raw:
        return None
    direct = _INDEX.get(_key(raw))
    if direct:
        return direct
    compact = _key(raw)
    for alias_key, ligand in _INDEX.items():
        if len(alias_key) >= 6 and alias_key in compact:
            return ligand
    return None


def curated_pubchem_record(name: str) -> dict[str, Any] | None:
    ligand = lookup_curated_ligand(name)
    if not ligand:
        return None
    return {
        "cid": "curated",
        "iupac_name": ligand["names"][0],
        "molecular_formula": ligand["formula"],
        "canonical_smiles": ligand["canonical_smiles"],
        "isomeric_smiles": ligand["canonical_smiles"],
        "names": list(ligand["names"]),
        "source": "curated-not-in-pubchem",
        "cbu_formula": ligand["cbu_formula"],
    }
