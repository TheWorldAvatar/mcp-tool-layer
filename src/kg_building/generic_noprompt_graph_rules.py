"""Shared generic-noprompt extras: Graph rules + T-Box handbook.

generic-noprompt is additive on each constructor's strict surface:
Pipeline keeps the no-contract envelope + MCP; OX keeps occurrence protocol.
Both then receive the same Graph rules and the same T-Box text.
generic-strict must not receive either extra.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

GENERIC_NOPROMPT_PROTOCOL = "generic-noprompt"
GENERIC_STRICT_PROTOCOL = "generic-strict"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MEDICAL_TBOX = _REPO_ROOT / "data" / "ontologies" / "medical_case_schema_de_non_flat_v4.ttl"
_MEDICAL_PARSED = _REPO_ROOT / "data" / "ontologies" / "medical_parsed.md"
_TBOX_HEADER = (
    "# Authoritative OntoSynthesis T-Box\n"
    "The following schema and comments are the same T-Box the project pipeline uses.\n"
)
_MEDICAL_TBOX_HEADER = (
    "# Authoritative medical T-Box\n"
    "The following schema and comments are the same T-Box the project pipeline uses.\n"
)

GENERIC_NOPROMPT_GRAPH_RULES = """Graph rules:
- At least one ontosyn:ChemicalSynthesis. Each is one MOP (discrete cage/polyhedron) producing workflow.
- Every ChemicalSynthesis has exactly one ontosyn:hasChemicalOutput and at least one ontosyn:hasSynthesisStep.
- Every ChemicalSynthesis must ontosyn:retrievedFrom a bibo:Document whose rdfs:label is the paper DOI.
- Allowed step types only: Add, Stir, HeatChill, Evaporate, Sonicate, Transfer, Separate, Filter, Dry.
- Never emit a node typed only as ontosyn:SynthesisStep; always use the concrete subclass.
- Every step (Add, Stir, HeatChill, ...) must have rdfs:label. Conversion and scoring cannot read a step that has type and order but no label.
- ontosyn:hasOrder starts at 1 and is unique and contiguous inside each ChemicalSynthesis.
- One Add owns exactly one ontosyn:hasAddedChemicalInput. A clause that names N materials is N Add nodes.
- ChemicalInput rdfs:label is identity only; put the amount on ontosyn:hasAmount.
- Attach vessels, atmospheres, temperatures, durations, washing solvents, and yields only when the source states them.
- Do not default atmosphere to air. Shared-context atmosphere (for example argon glovebox for a whole family) must be inherited onto covered steps via ontosyn:hasVesselEnvironment.
- A wash of retained solid is Filter with ontosyn:hasWashingSolvent, not Add.
- A heat-to-temperature-and-hold (including solvothermal 130 degC for 2 days) is HeatChill, not Stir, even if a vessel is named.
- Passive duration hold that yields crystals is HeatChill. There is no ontosyn:Crystallize class; do not emit that type.
- Cooling / return-to-room-temperature is a separate HeatChill and must keep ontosyn:hasTargetTemperature.
- Measure nodes (om-2:Temperature, Duration, Pressure, Volume, TemperatureRate, AmountOfSubstanceFraction) need rdfs:label. When a number and unit are explicit, also set om-2:hasNumericalValue and om-2:hasUnit.
- om-2:hasUnit MUST be an OM-2 unit individual (om-2:degreeCelsius, om-2:hour, om-2:day, om-2:minute, om-2:degreeCelsiusPerHour, om-2:millilitre, om-2:percent, ...). Never a free-text string such as "degC", "degC h-1", or "days".
- Qualitative measures (room temperature, overnight) are label-only; do not invent a unit or numerical value.
- Use prefixed types and properties: ontosyn:*, om-2:*, ontomops:*, rdfs:label, ontomops:hasCCDCNumber."""

MEDICAL_GENERIC_NOPROMPT_GRAPH_RULES = """Graph rules:
- Exactly one medical:MedicalCase (the bound root). Give it rdfs:label.
- Owner classes attach to that root only: PatientInfo via hasPatientInfo, CaseTimeline via hasTimeline, SurgicalApproach via hasSurgicalApproach, Procedure via hasProcedure, SurgicalTeam via hasSurgicalTeam, Diagnosis via hasDiagnosis, Complication via hasComplication, PathologyOutcome via hasPathologyOutcome.
- Emit an owner node when the ledger has that heading or any grounded facet for it. Do not emit an empty owner with no properties.
- Every node needs rdfs:label. Facets stay on their owner; these owners have no nested hops.
- binary_checklist fields: write the literal "1" when the ledger supports the item; otherwise omit the property. Do not write "0", "n", "false", or "nein" for those fields.
- Same_Day_Surgery is not binary_checklist. When admission date and OP date are both grounded: same calendar day → "j", otherwise "n". Omit it when admission date is missing.
- SurgicalApproach: at most one of offen / VATS / RATS is "1". Use the FINAL completed approach. Conversion to open means only offen="1".
- PatientInfo.Name is the patient. Never copy Operateur / Assistent / Behandler names into PatientInfo. SurgicalTeam keeps the two roles distinct.
- Dates Geburtsdatum, OP_Datum, Entlassdatum, praeop_TuKo are TT.MM.JJJJ strings.
- Derived integers Alter, Verweildauer, and Dauer_d_zwischen_TuKo_und_OP: compute only when the input dates are present; do not invent.
- Canonical binary diagnosis/procedure fields beat sonst_* free-text fallbacks. sonst_* is only for an explicit leftover that no canonical field covers.
- Komplikation_j_n is "1" only for postoperative complications in the OP-to-discharge window. Intraoperative technical events without an explicit complication mark are not automatically complications.
- Use prefixed types and properties: medical:*, rdfs:label.
- Emit the complete graph for this MedicalCase on every constructor pass, including correction rounds."""

_PIPELINE_APPENDIX_HEADER = (
    "# Generic-noprompt graph rules\n"
    "These construction rules are part of generic-noprompt only. "
    "They are not present in generic-strict.\n\n"
)
_MEDICAL_APPENDIX_HEADER = (
    "# Generic-noprompt medical graph rules\n"
    "These construction rules are part of generic-noprompt only. "
    "They are not present in generic-strict.\n\n"
)


def is_generic_noprompt(protocol: str | None) -> bool:
    return str(protocol or "").strip() == GENERIC_NOPROMPT_PROTOCOL


def append_generic_noprompt_graph_rules(
    user: str,
    protocol: str | None,
    *,
    ontology: str | None = None,
) -> str:
    """Leave strict / unset envelopes unchanged. Append Graph rules only for noprompt.

    Chemistry Graph rules are OntoSyn / extension only. Medical gets its own
    encoding rules (binary checklist, final approach, patient vs team). It
    does not receive Add/Filter/HeatChill rules.
    """
    text = str(user or "")
    if not is_generic_noprompt(protocol):
        return text
    if str(ontology or "").strip().lower() == "medical":
        if MEDICAL_GENERIC_NOPROMPT_GRAPH_RULES in text:
            return text
        return (
            text.rstrip()
            + "\n\n"
            + _MEDICAL_APPENDIX_HEADER
            + MEDICAL_GENERIC_NOPROMPT_GRAPH_RULES
            + "\n"
        )
    if GENERIC_NOPROMPT_GRAPH_RULES in text:
        return text
    return text.rstrip() + "\n\n" + _PIPELINE_APPENDIX_HEADER + GENERIC_NOPROMPT_GRAPH_RULES + "\n"


def _pack_ontosynthesis_tbox_candidates() -> list[Path]:
    """Repo-relative pack dumps. No machine-specific absolute paths."""
    runs = _REPO_ROOT / "generated" / "runs"
    if not runs.is_dir():
        return []
    found = [
        path
        for path in sorted(runs.glob("*/ontology_structures/ontosynthesis/parsed.md"))
        if path.is_file()
    ]
    preferred = [path for path in found if "fullpack" in path.parts]
    return preferred or found


def resolve_default_tbox() -> Path:
    """Prefer the frozen data copy, else a generated ontology_structures dump.

    Lives here so Pipeline KG can load the handbook without importing OX-local
    ``paths`` (that package is only on sys.path for the OntoLogX CLI).
    """
    frozen = _REPO_ROOT / "data" / "ontologies" / "ontosynthesis_parsed.md"
    if frozen.is_file():
        return frozen
    candidates: list[Path] = []
    env_root = str(os.environ.get("TWA_GENERATED_ARTIFACT_ROOT") or "").strip()
    if env_root:
        env_path = Path(env_root)
        if not env_path.is_absolute():
            env_path = _REPO_ROOT / env_path
        candidates.append(env_path / "ontology_structures" / "ontosynthesis" / "parsed.md")
    current = _REPO_ROOT / "generated" / "current.json"
    if current.is_file():
        try:
            payload = json.loads(current.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        rel = str(payload.get("path") or "").strip()
        if rel:
            rel_path = Path(rel)
            pack = rel_path if rel_path.is_absolute() else _REPO_ROOT / rel_path
            candidates.append(pack / "ontology_structures" / "ontosynthesis" / "parsed.md")
    candidates.extend(_pack_ontosynthesis_tbox_candidates())
    for path in candidates:
        if path.is_file():
            return path
    return frozen


def load_ontosynthesis_tbox_text() -> str:
    """Same T-Box file OX generic-noprompt / with-prompt already resolve."""
    path = resolve_default_tbox()
    if not path.is_file():
        raise FileNotFoundError(
            "Missing OntoSynthesis T-Box handbook. Expected "
            "data/ontologies/ontosynthesis_parsed.md or "
            "generated/runs/*/ontology_structures/ontosynthesis/parsed.md "
            f"(resolved {path})"
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty OntoSynthesis T-Box prompt: {path}")
    return text


def append_ontosynthesis_tbox(text: str) -> str:
    """Append the frozen OntoSynthesis handbook if it is not already present."""
    marker = "# Authoritative OntoSynthesis T-Box"
    if marker in text:
        return text
    return str(text).rstrip() + "\n\n" + _TBOX_HEADER + load_ontosynthesis_tbox_text() + "\n"


def load_medical_tbox_text() -> str:
    """OntoMed handbook freeze, analogous to ontosynthesis_parsed.md."""
    if _MEDICAL_PARSED.is_file():
        text = _MEDICAL_PARSED.read_text(encoding="utf-8").strip()
        if text:
            return text
    from src.kg_building.medical_handbook import render_medical_parsed_markdown

    if not _MEDICAL_TBOX.is_file():
        raise RuntimeError(f"Missing medical T-Box: {_MEDICAL_TBOX}")
    text = render_medical_parsed_markdown(_MEDICAL_TBOX).strip()
    if not text:
        raise RuntimeError(f"Empty medical T-Box handbook from {_MEDICAL_TBOX}")
    return text


def append_medical_tbox(text: str) -> str:
    marker = "# Authoritative medical T-Box"
    if marker in text:
        return text
    return str(text).rstrip() + "\n\n" + _MEDICAL_TBOX_HEADER + load_medical_tbox_text() + "\n"


def append_generic_noprompt_context(
    user: str,
    protocol: str | None,
    *,
    ontology: str | None = None,
) -> str:
    """Append Graph rules (chemistry only) and the matching T-Box handbook."""
    text = append_generic_noprompt_graph_rules(user, protocol, ontology=ontology)
    if not is_generic_noprompt(protocol):
        return text
    if str(ontology or "").strip().lower() == "medical":
        return append_medical_tbox(text)
    return append_ontosynthesis_tbox(text)
