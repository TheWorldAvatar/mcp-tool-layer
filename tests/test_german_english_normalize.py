from evaluation.utils.german_english_normalize import (
    fold_german_orthography,
    normalize_german_english,
    same_german_english_text,
)


def test_umlaut_and_sz_fold_to_ascii() -> None:
    assert fold_german_orthography("Lungenadhäsionen") == "Lungenadhaesionen"
    assert fold_german_orthography("Müller") == "Mueller"
    assert fold_german_orthography("Größe") == "Groesse"
    assert fold_german_orthography("a\u0308") == "ae"


def test_lungenadhaesionen_matches_ascii_and_english() -> None:
    assert same_german_english_text("Lungenadhäsionen", "Lungenadhaesionen")
    assert same_german_english_text("Lungenadhäsionen", "lung adhesions")
    assert same_german_english_text("Lungenadhäsion links", "left lung adhesion")


def test_names_and_hyperhidrosis() -> None:
    assert same_german_english_text("Müller", "Mueller")
    assert same_german_english_text("Hyperhidrose manuum", "Hyperhidrosis manuum")


def test_empty_and_unrelated_do_not_match() -> None:
    assert normalize_german_english("") == ""
    assert not same_german_english_text("", "lung")
    assert not same_german_english_text("NSCLC", "SCLC")
