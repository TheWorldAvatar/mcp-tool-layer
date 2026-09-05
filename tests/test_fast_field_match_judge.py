from evaluation.utils.fast_field_match_judge import (
    deterministic_species_match,
    strip_quantity_annotation,
    strip_unspecified_hydrate,
)


def test_strip_quantity_annotation_keeps_the_species_token() -> None:
    assert strip_quantity_annotation("5 mL DEF") == "DEF"
    assert strip_quantity_annotation("H2TEI (100 mg)") == "H2TEI"
    assert strip_quantity_annotation("DMF (5.0 mL)") == "DMF"
    assert strip_quantity_annotation("Cr(OAc)2 (50 mg)") == "Cr(OAc)2"
    assert strip_quantity_annotation("2.5mL mixture of DMF/CH3OH (v/v: 1:2)").startswith(
        "mixture"
    )


def test_deterministic_species_match_only_when_core_names_align() -> None:
    assert deterministic_species_match("DEF", "5 mL DEF")
    assert deterministic_species_match("H2TEI", "H2TEI (100 mg)")
    assert deterministic_species_match("VOSO4xxH2O", "VOSO4-xH2O")
    assert deterministic_species_match("VOSO4·xH2O", "VOSO4-xH2O")
    assert not deterministic_species_match(
        "DMF", "2.5mL mixture of DMF/CH3OH (v/v: 1:2)"
    )
    assert not deterministic_species_match("CuCl2", "CuCl2·2H2O")
    assert not deterministic_species_match("VOSO4", "VOSO4-xH2O")


def test_strip_unspecified_hydrate_keeps_the_salt() -> None:
    assert strip_unspecified_hydrate("VOSO4xxH2O").casefold() == "voso4"
    assert strip_unspecified_hydrate("VOSO4-xH2O").casefold() == "voso4"
    assert strip_unspecified_hydrate("CuCl2·2H2O") == ""


def test_deterministic_species_match_known_aliases_and_hydrate_punct() -> None:
    assert deterministic_species_match("triethylamine", "TEA")
    assert deterministic_species_match("Et2O", "diethyl ether")
    assert deterministic_species_match("H2BDC", "terephthalic acid")
    assert deterministic_species_match("cu(oac)2·h2o", "Cu(OAc)2-H2O")
    assert deterministic_species_match(
        "H2BTB",
        "1,3,5-tris(4-carboxyphenyl)-benzene (H2BTB)",
    )
    assert not deterministic_species_match("TEA", "triethylamine hydrochloride")
    assert not deterministic_species_match("Cu(OAc)2", "Cu(OAc)2-H2O")


def test_deterministic_species_match_reviewed_hydrate_names() -> None:
    assert deterministic_species_match(
        "copper(II) nitrate hexahydrate",
        "Cu(NO3)2·6H2O",
    )
    assert deterministic_species_match(
        "vanadyl sulfate pentahydrate",
        "VOSO4-5H2O",
    )
    assert not deterministic_species_match(
        "copper(II) nitrate pentahydrate",
        "Cu(NO3)2·6H2O",
    )


def test_equate_hydrate_counts_is_opt_in(monkeypatch) -> None:
    assert not deterministic_species_match(
        "copper(II) nitrate hexahydrate",
        "copper(II) nitrate",
    )
    monkeypatch.setenv("ONTOSYN_EQUATE_HYDRATE_COUNTS", "1")
    assert deterministic_species_match(
        "copper(II) nitrate hexahydrate",
        "copper(II) nitrate",
    )
    assert deterministic_species_match("CuCl2·2H2O", "CuCl2")
