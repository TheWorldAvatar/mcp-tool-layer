from src.mcp_servers.pubchem.curated_names import (
    curated_pubchem_record,
    lookup_curated_ligand,
)
from src.mcp_servers.pubchem.name_dedup import slim_compound_record


def test_lookup_hits_entity_label_and_common_name() -> None:
    by_label = lookup_curated_ligand("Cu_OBu-bdc cage synthesis")
    by_name = lookup_curated_ligand("5-butoxyisophthalic acid")
    assert by_label is not None and by_name is not None
    assert by_label["cbu_formula"] == "[(C6H3)O(CH2)3CH3(CO2)2]"
    assert by_name["canonical_smiles"] == by_label["canonical_smiles"]


def test_unrelated_name_is_not_curated() -> None:
    assert lookup_curated_ligand("isophthalic acid") is None
    assert curated_pubchem_record("water") is None


def test_slim_record_keeps_curated_smiles() -> None:
    record = curated_pubchem_record("5-ethoxy-1,3-benzenedicarboxylic acid")
    slim = slim_compound_record(record, query="5-ethoxy-1,3-benzenedicarboxylic acid")
    assert slim["canonical_smiles"].startswith("CCOc1cc")
    assert slim["source"] == "curated-not-in-pubchem"
    assert "cbu_formula" not in slim
