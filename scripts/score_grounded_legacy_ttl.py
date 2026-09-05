"""Score a grounded legacy OntoSynthesis TTL against the chemicals ground truth.

The repository's chemicals scorer operates on converted JSON rather than RDF.
This adapter reads the source-preservation triples emitted by
MOPTools/MOP_Literature_Extraction/local_ttl_export.py, reconstructs the
chemical-name prediction, and invokes the same scoring implementation used for
the current pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rdflib import Graph, Namespace, RDF

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from evaluation.scoring_chemicals import (
    _extract_chemical_names_flexible,
    _extract_input_chemical_names_from_gt,
    _score_name_lists,
)
from evaluation.utils.chemical_synonym_judge import SynonymJudgeConfig
from evaluation.utils.scoring_common import precision_recall_f1


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")


def _metrics(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision, recall, f1 = precision_recall_f1(tp, fp, fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def extract_legacy_prediction(graph: Graph) -> dict[str, Any]:
    procedures: list[dict[str, Any]] = []
    syntheses = sorted(
        set(graph.subjects(RDF.type, ONTOSYN.ChemicalSynthesis)), key=str
    )
    for synthesis in syntheses:
        labels = sorted(str(value) for value in graph.objects(synthesis, None))
        procedure_name = next(
            (
                str(value)
                for value in graph.objects(synthesis, ONTOSYN.sourceProcedureName)
            ),
            labels[0] if labels else "",
        )
        input_chemicals: list[dict[str, Any]] = []
        for chemical_input in sorted(
            graph.objects(synthesis, ONTOSYN.hasChemicalInput), key=str
        ):
            names: list[str] = []
            amounts: list[str] = []
            formulas: list[str] = []
            for material in graph.objects(
                chemical_input, ONTOSYN.referencesMaterial
            ):
                for single_phase in graph.objects(material, None):
                    for phase_component in graph.objects(
                        single_phase,
                        Namespace(
                            "http://www.theworldavatar.com/ontology/ontocape/"
                            "upper_level/system.owl#"
                        ).isComposedOfSubsystem,
                    ):
                        names.extend(
                            str(value)
                            for value in graph.objects(
                                phase_component, ONTOSYN.sourceChemicalName
                            )
                        )
                        amounts.extend(
                            str(value)
                            for value in graph.objects(
                                phase_component, ONTOSYN.sourceChemicalAmount
                            )
                        )
                        formulas.extend(
                            str(value)
                            for value in graph.objects(
                                phase_component, ONTOSYN.sourceChemicalFormula
                            )
                        )
            if names:
                input_chemicals.append(
                    {
                        "chemical": [
                            {
                                "chemicalName": sorted(set(names)),
                                "chemicalAmount": amounts[0] if amounts else "N/A",
                                "chemicalFormula": formulas[0] if formulas else "N/A",
                            }
                        ],
                        "supplierName": next(
                            (
                                str(value)
                                for value in graph.objects(
                                    chemical_input, ONTOSYN.sourceSupplierName
                                )
                            ),
                            "N/A",
                        ),
                        "purity": next(
                            (
                                str(value)
                                for value in graph.objects(
                                    chemical_input, ONTOSYN.hasPurity
                                )
                            ),
                            "N/A",
                        ),
                    }
                )
        procedures.append(
            {
                "procedureName": procedure_name,
                "steps": [
                    {
                        "inputChemicals": input_chemicals,
                        "outputChemical": [],
                    }
                ],
            }
        )
    return {"synthesisProcedures": procedures}


def score_prediction(
    ground_truth: dict[str, Any],
    prediction: dict[str, Any],
    synonym_config: SynonymJudgeConfig,
) -> tuple[dict[str, Any], list[str], list[str], int]:
    gt_names = _extract_input_chemical_names_from_gt(ground_truth)
    prediction_names = _extract_chemical_names_flexible(prediction)
    tp, fp, fn, _, synonym_tp = _score_name_lists(
        gt_names, prediction_names, synonym_config
    )
    return (
        _metrics(tp, fp, fn),
        gt_names,
        prediction_names,
        synonym_tp,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ttl", type=Path)
    parser.add_argument("--doi", required=True)
    parser.add_argument("--pipeline-hash", required=True)
    parser.add_argument(
        "--pipeline-prediction",
        type=Path,
        help=(
            "Pipeline chemicals.json to compare. Defaults to "
            "evaluation/data/merged_tll/<hash>/chemicals.json."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--llm-synonyms", action="store_true")
    parser.add_argument("--llm-synonym-model", default="gpt-4o")
    parser.add_argument(
        "--llm-synonym-cache-dir",
        type=Path,
        default=Path("evaluation/cache/chemical_synonym_judge"),
    )
    args = parser.parse_args()

    graph = Graph().parse(args.ttl, format="turtle")
    ground_truth_path = Path("full_ground_truth/chemicals") / f"{args.doi}.json"
    pipeline_path = args.pipeline_prediction or (
        Path("evaluation/data/merged_tll") / args.pipeline_hash / "chemicals.json"
    )
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    pipeline_prediction = json.loads(pipeline_path.read_text(encoding="utf-8"))
    legacy_prediction = extract_legacy_prediction(graph)
    synonym_config = SynonymJudgeConfig(
        enabled=args.llm_synonyms,
        model=args.llm_synonym_model,
        cache_dir=args.llm_synonym_cache_dir,
    )

    legacy_metrics, gt_names, legacy_names, legacy_synonym_tp = score_prediction(
        ground_truth, legacy_prediction, synonym_config
    )
    pipeline_metrics, _, pipeline_names, pipeline_synonym_tp = score_prediction(
        ground_truth, pipeline_prediction, synonym_config
    )
    report = {
        "doi": args.doi.replace("_", "/"),
        "scorer": "evaluation.scoring_chemicals._score_name_lists",
        "llm_synonyms": args.llm_synonyms,
        "llm_synonym_model": (
            args.llm_synonym_model if args.llm_synonyms else None
        ),
        "ground_truth_name_count": len(gt_names),
        "legacy_grounded_ttl": {
            "ttl": str(args.ttl.resolve()),
            "metrics": legacy_metrics,
            "synonym_tp": legacy_synonym_tp,
            "prediction_names": legacy_names,
        },
        "latest_pipeline": {
            "hash": args.pipeline_hash,
            "prediction": str(pipeline_path.resolve()),
            "metrics": pipeline_metrics,
            "synonym_tp": pipeline_synonym_tp,
            "prediction_names": pipeline_names,
        },
        "delta_legacy_minus_pipeline": {
            metric: round(
                legacy_metrics[metric] - pipeline_metrics[metric], 6
            )
            for metric in ("precision", "recall", "f1")
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
