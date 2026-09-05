from evaluation.normalize_steps import normalize_chemical_name
from evaluation.utils.chemical_name_aliases import canonical_chemical_name
from evaluation.utils.scoring_common import to_fingerprint
from evaluation.utils.step_equivalence_judge import _normalize_text


def test_h2sdb_matches_paper_full_name() -> None:
    short = "H2SDB"
    full = "4,4'-sulfonyldibenzoic acid"
    assert canonical_chemical_name("h2sdb") == canonical_chemical_name(
        "4,4'-sulfonyldibenzoic acid"
    )
    assert normalize_chemical_name(short) == normalize_chemical_name(full)
    assert to_fingerprint(short) == to_fingerprint(full)
    assert to_fingerprint(short)


def test_h2mdb_and_tmtah3_match_paper_full_names() -> None:
    assert to_fingerprint("H2MDB") == to_fingerprint("4,4'-methylenedibenzoic acid")
    assert to_fingerprint("TMTAH3") == to_fingerprint("trimesoyltri(L-alanine)")


def test_full_pack_reviewed_pairing_aliases() -> None:
    assert to_fingerprint("H2bpydc") == to_fingerprint(
        "2,2-bipyridine-5,5-dicarboxylic acid"
    )
    assert to_fingerprint("H2TEI") == to_fingerprint(
        "triisopropylsilyl ethynyl isophthalic acid"
    )
    assert to_fingerprint("Cr(OAc)2") == to_fingerprint("chromium(II) acetate")
    assert to_fingerprint("magnesium nitrate hexahydrate") == to_fingerprint(
        "Mg(NO3)2-6H2O"
    )
    assert to_fingerprint("PgC3OH") == to_fingerprint(
        "C-propan-3-ol pyrogallol[4]arene"
    )
    assert to_fingerprint("copper chloride dihydrate") == to_fingerprint(
        "CuCl2·2H2O"
    )
    assert to_fingerprint("H3TATB") == to_fingerprint(
        "2,4,6-tris(4-carboxyphenyl)-1,3,5-triazine"
    )
    assert _normalize_text("Na9[A-α-PW9O34]·nH2O", "chemical_name") == _normalize_text(
        "Na9[A-alpha-PW9O34]-nH2O", "chemical_name"
    )
    assert _normalize_text("VMOP-α", "chemical_name") == _normalize_text(
        "VMOP-alpha", "chemical_name"
    )
    assert _normalize_text("VMOP-β", "chemical_name") == _normalize_text(
        "VMOP-beta", "chemical_name"
    )
    assert _normalize_text("VMOP-β", "chemical_name") != _normalize_text(
        "VMOP-B", "chemical_name"
    )


def test_hydrate_count_is_not_collapsed() -> None:
    assert to_fingerprint("Cu(NO3)2·6H2O") != to_fingerprint("copper(II) nitrate")
    assert to_fingerprint("Cu(NO3)2·3H2O") != to_fingerprint("Cu(NO3)2")