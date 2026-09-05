from src.mcp_servers.ccdc.main import HARDCODED_DOI_CCDC, _normalize_doi_key
from src.mcp_servers.ccdc.operations.wsl_ccdc import search_ccdc_by_mop_name


def test_irmop_parenthetical_name_hits_gt_ccdc() -> None:
    assert search_ccdc_by_mop_name("IRMOP-51 (cubic)")[0][1] == "273616"
    assert search_ccdc_by_mop_name("IRMOP-50")[0][1] == "273613"
    assert search_ccdc_by_mop_name("MOP-54")[0][1] == "273623"


def test_ja042802q_doi_has_all_gt_ccdc() -> None:
    rows = HARDCODED_DOI_CCDC[_normalize_doi_key("10.1021_ja042802q")]
    numbers = {str(row["ccdc_number"]) for row in rows}
    assert numbers == {"273613", "273616", "273620", "273621", "273623"}


def test_cu_or_bdc_name_and_doi_hit_gt_ccdc() -> None:
    assert search_ccdc_by_mop_name("Cu_OBu-bdc cage")[0][1] == "1815077"
    assert search_ccdc_by_mop_name("Cu_OEt-bdc cage synthesis")[0][1] == "1815080"
    assert search_ccdc_by_mop_name("Cu_OPr-bdc")[0][1] == "1815084"
    assert search_ccdc_by_mop_name("Cu_OPent-bdc porous cage")[0][1] == "1815083"
    rows = HARDCODED_DOI_CCDC[_normalize_doi_key("10.1021/acsami.8b02015")]
    assert {str(row["ccdc_number"]) for row in rows} == {
        "1815080",
        "1815077",
        "1815084",
        "1815083",
    }


def test_cu24_tbu_amide_name_and_doi_hit_gt_ccdc() -> None:
    assert search_ccdc_by_mop_name("Cu24(tBu-amide-bdc)24")[0][1] == "1835131"
    assert (
        search_ccdc_by_mop_name("mechanochemical synthesis of Cu24(tBu-amide-bdc)24")[0][1]
        == "1835131"
    )
    rows = HARDCODED_DOI_CCDC[_normalize_doi_key("10.1021_acs.chemmater.8b01667")]
    assert {str(row["ccdc_number"]) for row in rows} == {"1835131"}
