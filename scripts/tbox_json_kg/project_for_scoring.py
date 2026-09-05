"""Project a materialized canonical RDF graph into existing scorer JSON files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rdflib import Graph

from scripts.output_conversion_ttl_to_json import (
    ontosynthesis_cbu_conversion as cbu,
)
from scripts.output_conversion_ttl_to_json import (
    ontosynthesis_characterisation_conversion as characterisation,
)
from scripts.output_conversion_ttl_to_json import (
    ontosynthesis_chemicals_conversion as chemicals,
)
from scripts.output_conversion_ttl_to_json import (
    ontosynthesis_step_conversion as steps,
)


def _write(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def project(ttl_path: Path, output_dir: Path) -> None:
    graph = Graph().parse(ttl_path, format="turtle")
    output_dir.mkdir(parents=True, exist_ok=True)

    chemical_namespaces = chemicals.get_namespaces(graph)
    syntheses = chemicals.query_synthesis_procedures(
        graph, chemical_namespaces
    )
    ontomops_data = chemicals.query_all_ontomops_data(
        graph, chemical_namespaces
    )
    _write(
        output_dir / "chemicals.json",
        chemicals.build_json_structure(
            graph,
            chemical_namespaces,
            syntheses,
            ontomops_data,
        ),
    )

    step_namespaces = steps.get_namespaces(graph)
    step_syntheses = steps.query_chemical_syntheses(graph, step_namespaces)
    if not step_syntheses:
        step_syntheses = steps.query_syntheses_via_steps(
            graph, step_namespaces
        )
    _write(
        output_dir / "steps.json",
        steps.build_json_structure(graph, step_namespaces, step_syntheses),
    )

    char_namespaces = characterisation.get_namespaces(graph)
    devices = characterisation.query_characterisation_devices(
        graph, char_namespaces
    )
    characterisations = characterisation.query_characterisation_data(
        graph, char_namespaces
    )
    _write(
        output_dir / "characterisation.json",
        characterisation.build_json_structure(devices, characterisations),
    )

    _write(output_dir / "cbu.json", cbu.build_cbu_json_from_graph(graph))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    project(args.input, args.output_dir)
    print(f"Wrote scorer projections to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
