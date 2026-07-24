"""Document measured gaps between RDF nodes and numeric time-series values."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from mini_marie.zaha.sg_old.local_store import db_path, ensure_db

SHIP_TS = "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
CONC_TS = "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_43d6f952-630a-4aeb-9d27-9c9202c9b2d8"
SHIP_SPEED_MEASURE = "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure"
DERIV = "https://www.theworldavatar.com/kg/ontodispersion/DerivationWithTimeSeries_79119944-40dd-41b3-a924-6307fed779f2"


def _triples(conn: sqlite3.Connection, subject: str) -> List[tuple]:
    return conn.execute(
        "SELECT p, o FROM triples WHERE ns='kb' AND s=? ORDER BY p", (subject,)
    ).fetchall()


def get_sg_ship_speed_value_chain(mmsi: str = "563071320") -> List[Dict[str, Any]]:
    """Full RDF chain for ship speed; shows where numerics drop out."""
    ensure_db()
    conn = sqlite3.connect(db_path())
    ship = f"https://www.theworldavatar.com/kg/ontodispersion/Ship{mmsi}"
    speed_measure = f"https://www.theworldavatar.com/kg/ontodispersion/Ship{mmsi}SpeedMeasure"

    measure_triples = _triples(conn, speed_measure)
    ts_iri = next((o for p, o in measure_triples if "hasTimeSeries" in p), "")
    ts_triples = _triples(conn, ts_iri) if ts_iri else []

    # All ship measures share one timeseries on this stack
    shared_measures = conn.execute(
        """
        SELECT s FROM triples WHERE ns='kb' AND o=? AND p LIKE '%hasTimeSeries%'
        """,
        (ts_iri or SHIP_TS,),
    ).fetchall()

    numerics_on_measure = conn.execute(
        """
        SELECT COUNT(*) FROM triples WHERE ns='kb' AND s=? AND p LIKE '%hasNumericalValue%'
        """,
        (speed_measure,),
    ).fetchone()[0]

    table_preds = conn.execute(
        """
        SELECT COUNT(*) FROM triples WHERE ns='kb' AND s LIKE '%Timeseries_%'
        AND (p LIKE '%hasTable%' OR p LIKE '%hasColumn%' OR p LIKE '%hasQuery%' OR p LIKE '%tableName%')
        """
    ).fetchone()[0]

    conn.close()
    return [
        {
            "question": f"Ship MMSI {mmsi} speed",
            "ship_iri": ship,
            "speed_measure_iri": speed_measure,
            "measure_predicates": "; ".join(p.rsplit("/", 1)[-1] for p, _ in measure_triples),
            "timeseries_iri": ts_iri or SHIP_TS,
            "timeseries_predicates": "; ".join(p.rsplit("/", 1)[-1] for p, _ in ts_triples),
            "measures_sharing_timeseries": len(shared_measures),
            "has_numerical_value_on_measure": numerics_on_measure > 0,
            "has_table_metadata_on_timeseries_nodes": table_preds > 0,
            "gap": "Measure has only type+hasTimeSeries; TimeSeries has only type+hasRDB(internal PostGIS)+hasTimeUnit",
            "missing_in_rdf": "table/column/query IRI; public timeseries HTTP API (all probed paths 404)",
            "internal_agents_in_kb": "sg-ship-data-agent:8080/ShipDataAgent (not routable from sg-old nginx)",
        }
    ]


def get_sg_concentration_value_chain(pollutant: str = "CO") -> List[Dict[str, Any]]:
    """Full RDF chain for dispersion concentrations at Jurong/simulation grid."""
    ensure_db()
    conn = sqlite3.connect(db_path())

    matrix_link = conn.execute(
        """
        SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%hasDispersionMatrix%' LIMIT 1
        """
    ).fetchone()
    matrix_iri = matrix_link[1] if matrix_link else ""
    matrix_triples = _triples(conn, matrix_iri) if matrix_iri else []
    ts_iri = next((o for p, o in matrix_triples if "hasTimeSeries" in p), CONC_TS)
    ts_triples = _triples(conn, ts_iri) if ts_iri else []

    deriv_triples = _triples(conn, DERIV)
    virtual_sensors = conn.execute(
        """
        SELECT COUNT(*) FROM triples WHERE ns='kb' AND o LIKE '%VirtualSensor%'
        AND p LIKE '%type%' AND s NOT LIKE '%Agent%'
        """
    ).fetchone()[0]

    conn.close()
    return [
        {
            "question": f"Jurong / simulation {pollutant} concentration",
            "derivation_iri": DERIV,
            "derivation_predicates": "; ".join(p.rsplit("/", 1)[-1] for p, _ in deriv_triples),
            "dispersion_matrix_iri": matrix_iri,
            "matrix_predicates": "; ".join(p.rsplit("/", 1)[-1] for p, _ in matrix_triples),
            "timeseries_iri": ts_iri,
            "timeseries_predicates": "; ".join(p.rsplit("/", 1)[-1] for p, _ in ts_triples),
            "virtual_sensor_instances_in_kb": virtual_sensors,
            "gap": "DispersionMatrix->hasTimeSeries->hasRDB only; no concentration literals in Blazegraph",
            "live_api": "GetDispersionSimulations 200; GetColourBar/GetPollutantConcentrations 500 (PostGIS); CreateVirtualSensor POST 403",
            "get_colour_bar_params": "pollutant + timestep + derivationIri + zIri (DispersionHandler.js; not 'time')",
            "feature_info_params": "iri + endpoint -> feature-info-agent/get (500 class-determination)",
            "jurong_in_kb_labels": 0,
        }
    ]
