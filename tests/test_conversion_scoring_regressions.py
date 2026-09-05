from rdflib import Graph, Literal, Namespace, RDF, RDFS

from evaluation.normalize_steps import normalize_json_structure
from evaluation.scoring_characterisation import (
    _normalize_ir_bands,
    _normalize_percent,
    _normalize_shifts,
)
from evaluation.scoring_steps import (
    _analyze_errors_by_field,
    _compare_step_fields,
    score_steps_fine_grained,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_step_conversion import (
    build_step_json,
    extract_duration,
    extract_temperature,
    extract_temperature_rate,
    get_namespaces,
    query_step_chemicals,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_chemicals_conversion import (
    get_namespaces as get_chemical_namespaces,
    query_synthesis_procedures,
)


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
EX = Namespace("https://example.test/")


def test_conversion_uses_canonical_ontology_iri_when_prefix_is_rewritten() -> None:
    graph = Graph()
    graph.bind("ns1", ONTOSYN, override=True, replace=True)
    graph.add((EX.synthesis, RDF.type, ONTOSYN.ChemicalSynthesis))
    graph.add((EX.synthesis, RDFS.label, Literal("specific route")))

    step_namespaces = get_namespaces(graph)
    chemical_namespaces = get_chemical_namespaces(graph)

    assert str(step_namespaces["ontosyn"]) == str(ONTOSYN)
    assert str(chemical_namespaces["ontosyn"]) == str(ONTOSYN)
    assert query_synthesis_procedures(graph, chemical_namespaces) == [
        {"uri": str(EX.synthesis), "label": "specific route"}
    ]


def test_temperature_conversion_preserves_label_only_room_temperature() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("om-2", OM2)
    graph.bind("rdfs", RDFS)
    graph.add((EX.step, ONTOSYN.hasTargetTemperature, EX.temperature))
    graph.add((EX.temperature, RDF.type, OM2.Temperature))
    graph.add((EX.temperature, RDFS.label, Literal("room temperature")))

    result = extract_temperature(
        graph,
        get_namespaces(graph),
        str(EX.step),
        str(ONTOSYN.hasTargetTemperature),
    )

    assert result == "room temperature"


def test_temperature_conversion_prefers_om2_iri_over_free_text_label() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("om-2", OM2)
    graph.bind("rdfs", RDFS)
    graph.add((EX.step, ONTOSYN.hasTargetTemperature, EX.temperature))
    graph.add((EX.temperature, RDF.type, OM2.Temperature))
    graph.add((EX.temperature, RDFS.label, Literal("150 degC")))
    graph.add((EX.temperature, OM2.hasNumericalValue, Literal(150.0)))
    graph.add((EX.temperature, OM2.hasUnit, OM2.degreeCelsius))

    result = extract_temperature(
        graph,
        get_namespaces(graph),
        str(EX.step),
        str(ONTOSYN.hasTargetTemperature),
    )

    assert result == "150 degree celsius"


def test_temperature_rate_conversion_uses_om2_iri_not_label() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("om-2", OM2)
    graph.bind("rdfs", RDFS)
    graph.add((EX.step, ONTOSYN.hasTemperatureRate, EX.rate))
    graph.add((EX.rate, RDF.type, OM2.TemperatureRate))
    graph.add((EX.rate, RDFS.label, Literal("10 degc/h")))
    graph.add((EX.rate, OM2.hasNumericalValue, Literal(10.0)))
    graph.add((EX.rate, OM2.hasUnit, OM2.degreeCelsiusPerHour))

    result = extract_temperature_rate(graph, get_namespaces(graph), str(EX.step))

    assert result == "10 degree celsius per hour"


def test_temperature_rate_conversion_resolves_legacy_string_unit() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("om-2", OM2)
    graph.add((EX.step, ONTOSYN.hasTemperatureRate, EX.rate))
    graph.add((EX.rate, RDF.type, OM2.TemperatureRate))
    graph.add((EX.rate, RDFS.label, Literal("10 degC h-1")))
    graph.add((EX.rate, OM2.hasNumericalValue, Literal(10.0)))
    graph.add((EX.rate, OM2.hasUnit, Literal("degC h-1")))

    result = extract_temperature_rate(graph, get_namespaces(graph), str(EX.step))

    assert result == "10 degree celsius per hour"


def test_duration_conversion_prefers_om2_iri_over_label() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("om-2", OM2)
    graph.bind("rdfs", RDFS)
    graph.add((EX.step, ONTOSYN.hasStepDuration, EX.duration))
    graph.add((EX.duration, RDF.type, OM2.Duration))
    graph.add((EX.duration, RDFS.label, Literal("60 hours")))
    graph.add((EX.duration, OM2.hasNumericalValue, Literal(60.0)))
    graph.add((EX.duration, OM2.hasUnit, OM2.hour))

    result = extract_duration(graph, get_namespaces(graph), str(EX.step))

    assert result == "60 h"


def test_duration_conversion_preserves_label_only_overnight() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("om-2", OM2)
    graph.bind("rdfs", RDFS)
    graph.add((EX.step, ONTOSYN.hasStepDuration, EX.duration))
    graph.add((EX.duration, RDF.type, OM2.Duration))
    graph.add((EX.duration, RDFS.label, Literal("overnight")))

    result = extract_duration(
        graph,
        get_namespaces(graph),
        str(EX.step),
    )

    assert result == "overnight"


def test_step_chemical_amounts_are_scoped_by_relationship_role() -> None:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("rdfs", RDFS)
    graph.add((EX.addExclusive, ONTOSYN.hasAddedChemicalInput, EX.exclusive))
    graph.add((EX.exclusive, RDFS.label, Literal("exclusive reagent")))
    graph.add((EX.exclusive, ONTOSYN.hasAmount, Literal("5 mg")))

    graph.add((EX.addShared, ONTOSYN.hasAddedChemicalInput, EX.shared))
    graph.add((EX.filter, ONTOSYN.hasWashingSolvent, EX.shared))
    graph.add((EX.shared, RDFS.label, Literal("shared chemical")))
    graph.add((EX.shared, ONTOSYN.hasAmount, Literal("1.67 mL")))

    graph.add((EX.filter, ONTOSYN.hasWashingSolvent, EX.washOnly))
    graph.add((EX.washOnly, RDFS.label, Literal("wash-only solvent")))
    graph.add((EX.washOnly, ONTOSYN.hasAmount, Literal("2 mL")))

    namespaces = get_namespaces(graph)
    exclusive = query_step_chemicals(
        graph, namespaces, str(EX.addExclusive)
    )
    shared_add = query_step_chemicals(graph, namespaces, str(EX.addShared))
    washing = query_step_chemicals(graph, namespaces, str(EX.filter))

    assert exclusive["addedChemical"][0]["chemicalAmount"] == "5 mg"
    assert shared_add["addedChemical"][0]["chemicalAmount"] == "1.67 mL"
    washing_by_name = {
        item["chemicalName"][0]: item["chemicalAmount"]
        for item in washing["washingSolvent"]
    }
    assert washing_by_name == {
        "shared chemical": "N/A",
        "wash-only solvent": "2 mL",
    }


def test_non_chemical_step_never_serializes_unrelated_chemical_amounts() -> None:
    result = build_step_json(
        {
            "step_type": "HeatChill",
            "order": 4,
            "duration": "2 days",
            "target_temperature": "120 degree celsius",
        },
        {
            "addedChemical": [
                {
                    "chemicalName": ["CH3OH"],
                    "chemicalAmount": "1.67 mL",
                }
            ],
            "washingSolvent": [],
            "solvent": [],
        },
    )

    assert "addedChemical" not in result["HeatChill"]
    assert "chemicalAmount" not in result["HeatChill"]


def test_step_normalization_covers_temperature_rate_and_known_name_variants() -> None:
    normalized = normalize_json_structure(
        {
            "targetTemperature": "room temperature",
            "heatingCoolingRate": "4 °C/h",
            "chemicalName": [
                "CoCl2-6H2O",
                '4,4\',4"-benzene-1,3,5-triyltribenzoic acid',
            ],
        }
    )

    assert normalized["targetTemperature"] == "25 degree celsius"
    assert normalized["heatingCoolingRate"] == "4 degree celsius per hour"
    assert normalized["chemicalName"] == [
        "cocl2-6h2o",
        "4, 4', 4''-benzene-1, 3, 5-triyltribenzoic acid",
    ]


def test_synthesis_pairing_uses_step_structure_not_prediction_order() -> None:
    gt = {
        "Synthesis": [
            {
                "productCCDCNumber": "123",
                "productNames": ["form-a"],
                "steps": [{"HeatChill": {"targetTemperature": "120 degree celsius"}}],
            },
            {
                "productCCDCNumber": "123",
                "productNames": ["form-a"],
                "steps": [{"Sonicate": {"duration": "10 min"}}],
            },
        ]
    }
    correctly_ordered = {
        "Synthesis": [
            {
                "productCCDCNumber": "123",
                "productNames": ["form-a"],
                "steps": [{"HeatChill": {"targetTemperature": "120 degree celsius"}}],
            },
            {
                "productCCDCNumber": "123",
                "productNames": ["form-a"],
                "steps": [{"Sonicate": {"duration": "10 min"}}],
            },
        ]
    }
    reversed_predictions = {
        "Synthesis": list(reversed(correctly_ordered["Synthesis"]))
    }

    expected = score_steps_fine_grained(gt, correctly_ordered, skip_order=True)
    actual = score_steps_fine_grained(gt, reversed_predictions, skip_order=True)

    assert actual == expected
    field_errors, _ = _analyze_errors_by_field(
        gt,
        reversed_predictions,
        skip_order=True,
    )
    assert field_errors == {}


def test_no_vessel_ignores_transfer_target_vessel_fields() -> None:
    gt = {"targetVesselName": "vial", "targetVesselType": "glass vial"}
    pred = {"targetVesselName": "autoclave", "targetVesselType": "steel autoclave"}

    assert _compare_step_fields(gt, pred, "Transfer", ignore_vessel=True) == (0, 0, 0)
    assert _compare_step_fields(gt, pred, "Transfer", ignore_vessel=False) != (0, 0, 0)


def test_characterisation_normalizes_wrappers_and_wavenumber_symbols() -> None:
    assert _normalize_percent(
        "Calculated for [complex formula], C 56.38, H 7.34, N 8.37"
    ) == _normalize_percent("C: 56.38; H: 7.34; N: 8.37")
    assert _normalize_percent(
        "Found, C 47.79, H 6.48, N 9.76"
    ) == _normalize_percent("C 47.79, H 6.48, N 9.76")
    assert _normalize_ir_bands("3401, 1652 cm@1") == "3401, 1652 cm-1"
    assert _normalize_shifts("δ = 13.22, 9.61 ppm") == _normalize_shifts(
        "delta = 13.22 (s, 2H, OH), 9.61 (s, 1H, NH)"
    )
