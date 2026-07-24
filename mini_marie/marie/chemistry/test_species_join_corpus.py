"""Tests for OntoSpecies competency join tables (derived from corpus)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE corpus_species (species_iri TEXT PRIMARY KEY, primary_label TEXT, indexed_at REAL);
        CREATE TABLE corpus_species_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species_iri TEXT, name_type TEXT, name_value TEXT, name_value_lc TEXT
        );
        CREATE TABLE corpus_species_pka (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species_iri TEXT, pka_iri TEXT, pka_value TEXT,
            temperature TEXT, ionic_strength TEXT, method TEXT,
            reliability TEXT, acidity_label TEXT, provenance TEXT
        );
        CREATE TABLE corpus_species_references (ref_iri TEXT PRIMARY KEY, label_value TEXT, label_value_lc TEXT);
        CREATE TABLE corpus_species_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, species_iri TEXT, use_value TEXT, use_value_lc TEXT
        );
        CREATE TABLE corpus_species_properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species_iri TEXT, property_local TEXT, property_value TEXT
        );
        INSERT INTO corpus_species VALUES ('http://ex/a', 'Acetic acid', 1.0);
        INSERT INTO corpus_species_names VALUES
            (1, 'http://ex/a', 'formula', 'C2H4O2', 'c2h4o2'),
            (2, 'http://ex/a', 'smiles', 'CC(=O)O', 'cc(=o)o'),
            (3, 'http://ex/a', 'inchi', 'InChI=1/...', 'inchi=1/...');
        INSERT INTO corpus_species_pka VALUES
            (1, 'http://ex/a', 'http://ex/pka1', '4.76', '298', '', 'potentiometry', 'uncertain', 'AH', 'http://ex/ref1');
        INSERT INTO corpus_species_references VALUES ('http://ex/ref1', 'Smith 2020', 'smith 2020');
        INSERT INTO corpus_species_uses VALUES (1, 'http://ex/a', 'vinegar precursor', 'vinegar precursor');
        INSERT INTO corpus_species_properties VALUES
            (1, 'http://ex/a', 'hasHydrogenBondDonorCount', '1'),
            (2, 'http://ex/a', 'hasHydrogenBondAcceptorCount', '2');
        """
    )
    conn.commit()
    conn.close()


def test_build_join_tables() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "join_test.sqlite"
        _seed_db(db)
        store = SpeciesJoinStore(str(db))
        try:
            built = store.build_all()
            assert built["identifiers"] == 1
            assert built["pka_enriched"] == 1
            assert built["uses_enriched"] == 1
            assert built["physprops_wide"] == 1
            assert built["profile"] == 1

            pka = store.query_pka_enriched(reliability_fragment="uncertain", limit=5)
            assert len(pka) == 1
            assert pka[0]["ref_label"] == "Smith 2020"
            assert pka[0]["smiles"] == "CC(=O)O"

            phys = store.lookup_physprops_by_smiles("CC(=O)O")
            assert phys[0]["hbond_donors"] == "1"
            assert phys[0]["hbond_acceptors"] == "2"
        finally:
            store.close()
