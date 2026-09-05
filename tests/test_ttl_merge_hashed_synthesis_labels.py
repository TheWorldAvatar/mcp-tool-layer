from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef

from scripts.output_conversion_ttl_to_json.name_utils import (
    collapse_labeled_syntheses,
    filter_product_names,
    is_hashed_artifact_label,
    prefer_synthesis_label,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_step_conversion import (
    get_namespaces,
    query_chemical_syntheses,
)
from scripts.output_conversion_ttl_to_json.ttl_merge import merge_for_hash


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
SYNTH = URIRef(
    "https://www.theworldavatar.com/kg/instance/ChemicalSynthesis/"
    "24f5b3586b45b0b29c9a922e742bf5dc755dded9"
)
HUMAN = (
    "Synthesis of [Co24(C-pentylpyrogallol[4]arene)6] nanocapsule (1) by reacting "
    "C-pentylpyrogallol[4]arene (PgC5), CoCl2-6H2O, and NaOMe in 1:1 (v/v) "
    "DMF/methanol mixture, yielding dark blue crystals over several weeks under "
    "slow evaporation"
)
HASHED = "Synthesis_of_Co24_C-pentylpyroga--8816a66c81dd"


def test_hashed_artifact_label_detects_export_stems() -> None:
    assert is_hashed_artifact_label(HASHED)
    assert is_hashed_artifact_label("Zr-bpydc-CuCl2_tetrahedral_coord--19f01710f375")
    assert not is_hashed_artifact_label(HUMAN)
    assert not is_hashed_artifact_label("Preparation of II [Ni24(C40H36O16)6]")


def test_prefer_and_filter_keep_human_label() -> None:
    assert prefer_synthesis_label([HASHED, HUMAN]) == HUMAN
    assert filter_product_names([HASHED, HUMAN, "[Co24] nanocapsule (1)"]) == [
        HUMAN,
        "[Co24] nanocapsule (1)",
    ]
    assert collapse_labeled_syntheses(
        [(str(SYNTH), HASHED), (str(SYNTH), HUMAN)]
    ) == [{"uri": str(SYNTH), "label": HUMAN}]


def _write_ttl(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .\n\n"
        f"<{SYNTH}> a ontosyn:ChemicalSynthesis ;\n"
        f'    rdfs:label "{label}" .\n',
        encoding="utf-8",
    )


def test_merge_drops_extension_hashed_synthesis_label(tmp_path: Path) -> None:
    hash_id = "a527729b"
    root = tmp_path / hash_id
    _write_ttl(root / "ontosynthesis_output" / "main.ttl", HUMAN)
    _write_ttl(root / "ontospecies_output" / "ext.ttl", HASHED)

    merged = merge_for_hash(hash_id, str(tmp_path), add_links=False)
    labels = {str(value) for value in merged.objects(SYNTH, RDFS.label)}
    assert HUMAN in labels
    assert HASHED not in labels

    syntheses = query_chemical_syntheses(merged, get_namespaces(merged))
    assert syntheses == [{"uri": str(SYNTH), "label": HUMAN}]


def test_query_collapses_two_labels_on_one_iri() -> None:
    graph = Graph()
    graph.add((SYNTH, RDF.type, ONTOSYN.ChemicalSynthesis))
    graph.add((SYNTH, RDFS.label, Literal(HUMAN)))
    graph.add((SYNTH, RDFS.label, Literal(HASHED)))

    syntheses = query_chemical_syntheses(graph, get_namespaces(graph))
    assert syntheses == [{"uri": str(SYNTH), "label": HUMAN}]
