from rdflib import Graph, Literal, Namespace, RDF, RDFS

from scripts.output_conversion_ttl_to_json.ontosynthesis_characterisation_conversion import (
    _select_aligned_ccdc,
    build_json_structure,
    get_namespaces,
    query_characterisation_data,
    query_characterisation_devices,
)


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOSPECIES = Namespace(
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
)
EX = Namespace("https://example.test/")


def _base_graph() -> Graph:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("ontospecies", ONTOSPECIES)
    graph.add((EX.synth, RDF.type, ONTOSYN.ChemicalSynthesis))
    graph.add((EX.synth, RDFS.label, Literal("HCCF-1")))
    graph.add((EX.synth, ONTOSYN.hasChemicalOutput, EX.species))
    graph.add((EX.species, RDF.type, ONTOSPECIES.Species))
    graph.add((EX.species, RDFS.label, Literal("HCCF-1")))
    return graph


def test_select_aligned_ccdc_prefers_product_label() -> None:
    assert (
        _select_aligned_ccdc(
            [
                ("1528353", "CCDC number for HCCF-1-d"),
                ("1528352", "CCDC number for HCCF-1"),
            ],
            ["HCCF-1"],
        )
        == "1528352"
    )


def test_select_aligned_ccdc_is_deterministic_without_labels() -> None:
    assert _select_aligned_ccdc([("1528353", ""), ("1528352", "")], ["HCCF-1"]) == "1528352"


def test_characterisation_reads_hnmr_and_aligned_ccdc() -> None:
    graph = _base_graph()
    graph.add((EX.ccdc_guest, RDF.type, ONTOSPECIES.CCDCNumber))
    graph.add((EX.ccdc_guest, RDFS.label, Literal("CCDC number for HCCF-1-d")))
    graph.add((EX.ccdc_guest, ONTOSPECIES.hasCCDCNumberValue, Literal("1528353")))
    graph.add((EX.ccdc_main, RDF.type, ONTOSPECIES.CCDCNumber))
    graph.add((EX.ccdc_main, RDFS.label, Literal("CCDC number for HCCF-1")))
    graph.add((EX.ccdc_main, ONTOSPECIES.hasCCDCNumberValue, Literal("1528352")))
    graph.add((EX.species, ONTOSPECIES.hasCCDCNumber, EX.ccdc_guest))
    graph.add((EX.species, ONTOSPECIES.hasCCDCNumber, EX.ccdc_main))

    graph.add((EX.nmr, RDF.type, ONTOSPECIES.HNMRData))
    graph.add(
        (
            EX.nmr,
            ONTOSPECIES.hasShifts,
            Literal("δ 6.50 (d, 120H, Cp-H), δ 2.68 (s, 12H, C-H)"),
        )
    )
    graph.add((EX.nmr, ONTOSPECIES.hasTemperature, Literal("not specified")))
    graph.add((EX.solvent, RDF.type, ONTOSPECIES.Solvent))
    graph.add((EX.solvent, ONTOSPECIES.hasSolventName, Literal("[D4]CH3OH")))
    graph.add((EX.nmr, ONTOSPECIES.usesSolvent, EX.solvent))
    graph.add((EX.species, ONTOSPECIES.hasHNMRData, EX.nmr))

    graph.add((EX.session, RDF.type, ONTOSPECIES.CharacterizationSession))
    graph.add((EX.hnmr_dev, RDF.type, ONTOSPECIES.HNMRDevice))
    graph.add((EX.hnmr_dev, RDFS.label, Literal("Bruker AVANCE 400 spectrometer")))
    graph.add((EX.hnmr_dev, ONTOSPECIES.hasDeviceName, Literal("Bruker AVANCE 400")))
    graph.add((EX.hnmr_dev, ONTOSPECIES.hasFrequency, Literal("400 MHz")))
    graph.add((EX.session, ONTOSPECIES.hasHNMRDevice, EX.hnmr_dev))
    graph.add((EX.species, ONTOSPECIES.hasCharacterizationSession, EX.session))

    records = query_characterisation_data(graph, get_namespaces(graph))
    devices = query_characterisation_devices(graph, get_namespaces(graph))
    payload = build_json_structure(devices, records)

    assert len(records) == 1
    record = records[0]
    assert record["productCCDCNumber"] == "1528352"
    assert record["productNames"] == ["HCCF-1"]
    assert record["HNMR"]["shifts"] == "6.50 (d, 120H, Cp-H), 2.68 (s, 12H, C-H)"
    assert record["HNMR"]["solvent"] == "[D4]CH3OH"
    assert record["HNMR"]["temperature"] == "N/A"
    assert devices["HNMRDevice"]["deviceName"] == "Bruker AVANCE 400"
    assert payload["Devices"][0]["Characterisation"][0]["productCCDCNumber"] == "1528352"
