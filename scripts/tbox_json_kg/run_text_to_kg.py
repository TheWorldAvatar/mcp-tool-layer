"""Run text -> TBox-derived canonical JSON -> RDF with GPT-4.1.

The extraction plan chooses ontology classes for each LLM stage, while every
field, RDF predicate, range and materialization rule is compiled mechanically
from the supplied TBoxes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from scripts.tbox_json_kg.compiler import TBoxCompiler
from scripts.tbox_json_kg.materializer import CanonicalJsonMaterializer


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TBOXES = [
    REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl",
    REPO_ROOT / "data" / "ontologies" / "ontospecies.ttl",
    REPO_ROOT / "data" / "ontologies" / "ontomops-subgraph.ttl",
    REPO_ROOT / "data" / "ontologies" / "om2.ttl",
]

ONTOSYN = "https://www.theworldavatar.com/kg/OntoSyn/"
ONTOSPECIES = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
)
ONTOMOPS = "https://www.theworldavatar.com/kg/ontomops/"
OM2 = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
PERIODIC = "http://www.daml.org/2003/01/periodictable/PeriodicTable#"


@dataclass(frozen=True)
class Stage:
    name: str
    roots: tuple[str, ...]
    instruction: str
    depends_on: tuple[str, ...] = ()
    require_expected_refs: bool = False


STAGES = (
    Stage(
        "core",
        (
            ONTOSYN + "ChemicalSynthesis",
            ONTOSYN + "ChemicalInput",
            ONTOSYN + "ChemicalOutput",
        ),
        """
Extract the complete MOP synthesis scaffold.
- Create one ChemicalSynthesis per standalone procedure whose primary purpose is
  producing a discrete metal-organic polyhedron/cage. Exclude ligand or precursor
  preparations, MOFs, characterization-only treatments and sample preparation.
- Scan the entire article and supporting information. Enumerate every distinct
  qualifying product/procedure before emitting JSON; never stop after the first.
- Create every explicitly consumed reactant, reagent, catalyst, process solvent,
  washing solvent and separation medium as ChemicalInput.
- Create each MOP product as ChemicalOutput. Preserve all stated product names,
  formula and CCDC identity through the available TBox properties.
- Set each ChemicalSynthesis rdfs:label to the shortest canonical product
  identifier/name used by the article (for example IRMOP-50 or VMOP-α), without
  "Synthesis of", a long formula, or explanatory prose. Keep those longer names
  on the ChemicalOutput instead.
- Link each synthesis to all inputs and outputs.
- Predeclare every synthesis step as an @id reference in procedural order; the
  step stage will define those IDs.
- Predeclare characterization nodes and represented MOP nodes from each output
  when evidence exists; later stages will define those IDs.
- Leave document context, retrieved document, supplier and global equipment links
  empty; those resources are outside this scored extraction view.
- Use IDs such as synthesis-1, input-1-1, output-1-1, step-1-1,
  ccdc-1-1, formula-1-1 and mop-1-1. Reuse IDs exactly.
- Do not reuse one ID for resources whose object-property ranges are incompatible
  (for example ChemicalFormula versus MolecularFormula, or HNMRData versus
  CharacterizationSession).
""",
    ),
    Stage(
        "step_operations_primary",
        tuple(
            ONTOSYN + name
            for name in (
                "Add",
                "HeatChill",
                "Crystallize",
                "Stir",
                "Sonicate",
            )
        )
        + tuple(
            OM2 + name
            for name in (
                "Duration",
                "Temperature",
                "Pressure",
                "Volume",
                "TemperatureRate",
            )
        ),
        """
Define all Add, HeatChill, Crystallize, Stir and Sonicate step IDs referenced
by the core stage.
- Preserve procedure order and emit only operations explicitly supported by text.
- Link Add chemicals to existing core ChemicalInput IDs.
- Predeclare shared vessel, environment and equipment IDs as references.
- For every explicit duration, temperature, pressure, volume or rate, create a
  quantity root in this same stage and link it from the step.
- Quantities use OM2 hasNumericalValue and hasUnit. Unit references must be
  absolute IRIs or OM2 CURIEs such as om2:degreeCelsius, om2:hour, om2:minute,
  om2:second or om2:millilitre.
- Do not infer temperature, time, atmosphere, equipment or amounts when absent.
""",
        ("core",),
    ),
    Stage(
        "step_operations_workup",
        tuple(
            ONTOSYN + name
            for name in (
                "Dry",
                "Filter",
                "Evaporate",
                "Transfer",
                "Separate",
            )
        )
        + tuple(
            OM2 + name
            for name in (
                "Duration",
                "Temperature",
                "Pressure",
                "Volume",
                "TemperatureRate",
            )
        ),
        """
Define all Dry, Filter, Evaporate, Transfer and Separate step IDs referenced by
the core stage.
- Preserve procedure order and emit only operations explicitly supported by text.
- Link washing, separation, drying and removed chemicals to existing core
  ChemicalInput IDs.
- Predeclare shared vessel, environment and equipment IDs as references.
- For every explicit duration, temperature, pressure, volume or rate, create a
  quantity root in this same stage and link it from the step.
- Quantities use OM2 hasNumericalValue and hasUnit with explicit units.
- Do not infer temperature, time, atmosphere, equipment or amounts when absent.
""",
        ("core",),
    ),
    Stage(
        "step_context",
        tuple(
            ONTOSYN + name
            for name in (
                "Vessel",
                "VesselType",
                "VesselEnvironment",
                "Equipment",
                "HeatChillDevice",
                "SeparationType",
            )
        ),
        """
Define all vessel, vessel type, atmosphere/environment, equipment, heat/chill
device and separation type IDs referenced by the step-operation stages.
- Reuse a shared entity ID when the article clearly refers to the same object.
- Preserve verbatim labels; do not infer an unstated type or device.
""",
        ("step_operations_primary", "step_operations_workup"),
    ),
    Stage(
        "characterisation",
        tuple(
            ONTOSPECIES + name
            for name in (
                "CCDCNumber",
                "ChemicalFormula",
                "MolecularFormula",
                "CharacterizationSession",
                "HNMRData",
                "HNMRDevice",
                "ChemicalShift",
                "InfraredSpectroscopyData",
                "InfraredSpectroscopyDevice",
                "InfraredBand",
                "ElementalAnalysisData",
                "ElementalAnalysisDevice",
                "WeightPercentage",
                "Device",
                "Solvent",
                "Material",
                "AtomicWeight",
            )
        )
        + (PERIODIC + "Element",),
        """
Define characterization resources referenced by core ChemicalOutput nodes.
- Extract only characterization explicitly associated with a synthesized product.
- Preserve CCDC number, chemical/molecular formula, elemental analysis calculated
  and experimental values, 1H NMR shifts/solvent/temperature/frequency, and IR
  bands/material/device where stated.
- Do not invent missing peaks, solvents, devices or values.
- Reuse every @id predeclared by the core stage.
""",
        ("core",),
        True,
    ),
    Stage(
        "cbu",
        (
            ONTOMOPS + "MetalOrganicPolyhedron",
            ONTOMOPS + "ChemicalBuildingUnit",
        ),
        """
Define each MOP and its chemical building units referenced by core outputs.
- Preserve MOP labels, formula and CCDC number.
- Link each MOP to its explicit metal and organic ChemicalBuildingUnit resources.
- Put canonical CBU formula in ontomops:hasCBUFormula and all stated species names
  or abbreviations in rdfs:label.
- Be conservative: do not derive a CBU that is not supported by the article.
""",
        ("core",),
        True,
    ),
)


GENERAL_PROMPT = """
You are extracting a scientific article into canonical RDF-path JSON.
The JSON Schema was generated mechanically from OWL/RDFS TBoxes:
- @type identifies the RDF class.
- Every other non-@ key is an RDF property CURIE.
- Datatype-property values are arrays of primitives.
- Object-property values are arrays of {"@id": "..."} references.
- Every schema field is required; use [] when evidence is absent.
- Never invent fields or emit null/N/A placeholders.
- A full root has @id, @type and all schema fields. A reference has only @id.
- Local @id values are paper-local stable identifiers. The same real entity must
  use exactly the same ID in every stage.
- Never reuse one ID for incompatible RDF classes.
- Extract all qualifying procedures and all supported evidence, without guessing.
"""


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _load_secret_module(path: Path) -> str:
    spec = importlib.util.spec_from_file_location("tbox_json_kg_secret", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load secret module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(getattr(module, "API_KEY", "")).strip()


def _client(secret_file: Path | None, base_url: str | None) -> OpenAI:
    _load_env_file(REPO_ROOT / ".env")
    key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("REMOTE_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
    )
    selected_base = base_url or os.getenv("REMOTE_BASE_URL")
    if not key and secret_file:
        key = _load_secret_module(secret_file)
        if base_url is None:
            selected_base = None
    if not key:
        raise RuntimeError("No API key found")
    return OpenAI(api_key=key, base_url=selected_base or None)


def _field_guide(bundle: dict[str, Any]) -> str:
    schema = bundle["json_schema"]["schema"]
    definitions = schema["$defs"]
    lines: list[str] = []
    for class_curie, class_info in bundle["classes"].items():
        definition = definitions[class_info["definition"]]
        fields: list[str] = []
        for key in definition["properties"]:
            if key.startswith("@"):
                continue
            property_info = bundle["properties"][key]
            ranges = [
                next(
                    (
                        curie
                        for curie, info in bundle["classes"].items()
                        if info["iri"] == range_iri
                    ),
                    range_iri,
                )
                for range_iri in property_info.get("ranges", [])
            ]
            suffix = f" -> {' | '.join(ranges)}" if ranges else ""
            fields.append(f"{key}{suffix}")
        lines.append(f"- {class_curie}: {', '.join(fields)}")
    return "\n".join(lines)


def _required_reference_ids(
    compiler: TBoxCompiler,
    stage: Stage,
    dependency_documents: list[dict[str, Any]],
) -> list[str]:
    accepted_ranges = {
        ancestor
        for root in stage.roots
        for ancestor in compiler.ancestors(root)
    }
    required: set[str] = set()
    for document in dependency_documents:
        for node in document.get("roots", []):
            for key, values in node.items():
                registry_spec = next(
                    (
                        property_spec
                        for property_spec in compiler.properties.values()
                        if compiler.curie(property_spec.iri) == key
                    ),
                    None,
                )
                if (
                    registry_spec is None
                    or registry_spec.kind != "object"
                    or not accepted_ranges.intersection(registry_spec.ranges)
                ):
                    continue
                for value in values:
                    if isinstance(value, dict) and value.get("@id"):
                        required.add(str(value["@id"]))
    return sorted(required)


def _text_call(
    client: OpenAI, model: str, system: str, prompt: str
) -> tuple[str, dict[str, int | None]]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        top_p=0.1,
    )
    usage = response.usage
    return response.choices[0].message.content or "", {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def _structured_call(
    client: OpenAI,
    model: str,
    bundle: dict[str, Any],
    prompt: str,
) -> tuple[dict[str, Any], dict[str, int | None]]:
    response = client.chat.completions.create(
        model=model,
        response_format={
            "type": "json_schema",
            "json_schema": bundle["json_schema"],
        },
        messages=[
            {"role": "system", "content": GENERAL_PROMPT.strip()},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        top_p=0.1,
    )
    content = response.choices[0].message.content or ""
    data = json.loads(content)
    usage = response.usage
    return data, {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--tbox", type=Path, action="append")
    parser.add_argument("--base-url")
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_text = input_path.read_text(encoding="utf-8")
    paper_id = output_dir.name
    tboxes = [path.resolve() for path in (args.tbox or DEFAULT_TBOXES)]
    compiler = TBoxCompiler(tboxes)
    client = _client(args.secret_file, args.base_url)

    usage: dict[str, Any] = {}
    evidence_path = output_dir / "selected_evidence.txt"
    if args.resume and evidence_path.exists():
        selected_evidence = evidence_path.read_text(encoding="utf-8")
        usage["evidence"] = {
            "prompt_tokens": None,
            "completion_tokens": None,
        }
    else:
        selected_evidence, usage["evidence"] = _text_call(
            client,
            args.model,
            (
                "Select complete evidence for structured extraction. Preserve "
                "verbatim facts and never invent missing details."
            ),
            (
                "Scan the entire article and supporting information before "
                "answering. Enumerate EVERY standalone procedure whose primary "
                "purpose is producing a discrete metal-organic polyhedron/cage. "
                "Do not stop after the first sibling product. For each qualifying "
                "procedure, copy or faithfully preserve: product names/formula/"
                "CCDC, every consumed chemical and amount, the complete ordered "
                "procedure, characterization associated with that product, and "
                "explicit metal/organic CBU evidence. Exclude precursor/ligand "
                "preparations, MOFs and characterization-only sample treatment.\n\n"
                f"ARTICLE:\n{paper_text}"
            ),
        )
        evidence_path.write_text(selected_evidence, encoding="utf-8")

    operations_path = output_dir / "operation_ledger.txt"
    if args.resume and operations_path.exists():
        operation_ledger = operations_path.read_text(encoding="utf-8")
        usage["operation_ledger"] = {
            "prompt_tokens": None,
            "completion_tokens": None,
        }
    else:
        operation_ledger, usage["operation_ledger"] = _text_call(
            client,
            args.model,
            (
                "Create a complete evidence-bound operation ledger for later "
                "structured extraction."
            ),
            (
                "For every procedure in the selected evidence, list every "
                "operation in exact order. Split simultaneous additions into "
                "separate Add entries, and preserve chemicals, amounts, vessels, "
                "atmosphere, duration, temperature, pressure, equipment, washing, "
                "separation and transfer evidence. Use only Add, HeatChill, "
                "Crystallize, Dry, Filter, Stir, Sonicate, Evaporate, Transfer or "
                "Separate. Do not infer absent values.\n\n"
                f"SELECTED EVIDENCE:\n{selected_evidence}"
            ),
        )
        operations_path.write_text(operation_ledger, encoding="utf-8")

    stage_documents: list[dict[str, Any]] = []
    document_by_stage: dict[str, dict[str, Any]] = {}
    stage_context: dict[str, str] = {}
    for stage in STAGES:
        bundle = compiler.compile(stage.roots, reference_only=True)
        bundle_path = output_dir / f"{stage.name}_schema_bundle.json"
        bundle_path.write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        canonical_path = output_dir / f"{stage.name}_canonical.json"
        if args.resume and canonical_path.exists():
            document = json.loads(canonical_path.read_text(encoding="utf-8"))
            usage[stage.name] = {
                "prompt_tokens": None,
                "completion_tokens": None,
            }
        else:
            dependency_documents = [
                document_by_stage[name]
                for name in stage.depends_on
                if name in document_by_stage
            ]
            required_ids = _required_reference_ids(
                compiler, stage, dependency_documents
            )
            prior = "\n\n".join(
                stage_context[name]
                for name in stage.depends_on
                if name in stage_context
            )
            prompt = (
                f"Stage: {stage.name}\n\n"
                f"{stage.instruction.strip()}\n\n"
                f"Generated class/property guide:\n{_field_guide(bundle)}\n\n"
                f"Required referenced IDs this stage can define (copy exactly):\n"
                f"{json.dumps(required_ids, ensure_ascii=False)}\n\n"
                f"Prior canonical stage outputs (reuse their IDs exactly):\n"
                f"{prior or '(none)'}\n\n"
                f"Selected all-procedure evidence:\n{selected_evidence}\n\n"
                f"Ordered operation ledger:\n{operation_ledger}\n\n"
                f"Full article for verification:\n{paper_text}"
            )
            document, usage[stage.name] = _structured_call(
                client, args.model, bundle, prompt
            )
            if stage.require_expected_refs:
                emitted_ids = {
                    str(root.get("@id"))
                    for root in document.get("roots", [])
                    if root.get("@id")
                }
                missing_ids = sorted(set(required_ids) - emitted_ids)
                if missing_ids:
                    repair_prompt = (
                        prompt
                        + "\n\nYour previous response failed the cross-stage ID "
                        "constraint. Regenerate the COMPLETE stage output. Define "
                        "these exact missing IDs without renaming or suffixing: "
                        + json.dumps(missing_ids, ensure_ascii=False)
                        + "\n\nPrevious invalid stage output:\n"
                        + json.dumps(document, ensure_ascii=False)
                    )
                    document, retry_usage = _structured_call(
                        client, args.model, bundle, repair_prompt
                    )
                    usage[stage.name]["retry"] = retry_usage
            canonical_path.write_text(
                json.dumps(document, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        stage_documents.append(document)
        document_by_stage[stage.name] = document
        stage_context[stage.name] = (
            f"[{stage.name}]\n"
            + json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        )

    combined = {
        "roots": [
            root
            for document in stage_documents
            for root in document.get("roots", [])
        ]
    }
    combined_path = output_dir / "canonical.json"
    combined_path.write_text(
        json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    full_bundle = compiler.compile(
        tuple(root for stage in STAGES for root in stage.roots),
        reference_only=True,
    )
    materializer = CanonicalJsonMaterializer(
        full_bundle,
        f"https://www.theworldavatar.com/kg/generated/{paper_id}/",
    )
    graph = materializer.materialize(
        combined,
        dangling_policy="drop",
        range_policy="drop",
    )
    ttl_path = output_dir / "output.ttl"
    graph.serialize(ttl_path, format="turtle")

    manifest = {
        "input": str(input_path),
        "paper_id": paper_id,
        "model": args.model,
        "tboxes": [str(path) for path in tboxes],
        "stages": [stage.name for stage in STAGES],
        "triples": len(graph),
        "dropped_dangling_ids": materializer.dropped_dangling_ids,
        "dropped_range_edges": materializer.dropped_range_edges,
        "usage": usage,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {len(graph)} triples to {ttl_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
