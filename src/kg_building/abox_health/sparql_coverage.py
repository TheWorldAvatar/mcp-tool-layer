"""SPARQL-coverage orphans: typed instance IRIs never bound by conversion queries.

Official scoring only sees JSON vs ground truth. JSON is projected from the
published Turtle by conversion SPARQL (Reproduction
``scripts/output_conversion_ttl_to_json``). A-Box individuals that those
queries never bind never enter JSON, so they are invisible to official FP.

This module:

- rewrites each conversion ``SELECT`` to ``SELECT *`` so WHERE-only variables
  (``?durationUri``, ``?tempUri``, ``?cf``, …) count as covered, except
  aggregate queries (``GROUP BY`` / ``HAVING`` / ``MIN()`` in the SELECT
  head) which SPARQL cannot rewrite that way;
- always returns the **original** query result to conversion so coverage
  instrumentation cannot empty a ``GROUP BY`` step list;
- collects URIRef bindings, ``initBindings``, and ``<iri>`` constants;
- subtracts the covered set from **GT-scored** typed instances, with each
  IRI owned by at most one module so merged-graph Chem/Char/CBU do not
  repeat the same extra FP.

Connectivity orphans (``DIRECTED_UNREACHABLE`` / ``ISOLATED_TYPED_NODE``) are
a different diagnostic and are not used here.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import types
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from .graph_index import (
    instance_graph,
    is_vocabulary_iri,
    load_tbox,
    load_ttl,
    typed_instance_nodes,
    vocabulary_iris,
)

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]
_DEFAULT_REPRO = (
    _REPO_ROOT
    / "data"
    / "third_party_repos"
    / "MCP-enhanced-MOPs-Extraction_Reproduction"
)
REPRO_ROOT = Path(os.environ.get("MOPS_REPRO_ROOT") or _DEFAULT_REPRO)

OM2_NAME = frozenset({"om2.ttl", "om2_mock.ttl"})
EXTRA_VOCAB_PREFIXES = (
    "http://www.ontology-of-units-of-measure.org/resource/om-2/",
    "http://qudt.org/schema/qudt/",
    "http://purl.org/dc/terms/",
    "http://purl.org/dc/elements/1.1/",
    "http://xmlns.com/foaf/0.1/",
)
ONTOLOGY_TYPE_PREFIX = {
    "ontosynthesis": "https://www.theworldavatar.com/kg/OntoSyn/",
    "ontomops": "https://www.theworldavatar.com/kg/ontomops/",
    "ontospecies": "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#",
    "medical": "https://www.theworldavatar.com/kg/medical/",
}

_SELECT_HEAD = re.compile(
    r"(?is)\bSELECT(\s+(?:DISTINCT|REDUCED))?\s+(?!\*)(.+?)(?=\s+WHERE\b)"
)
_ASK_OR_CONSTRUCT = re.compile(
    r"(?is)^\s*(?:PREFIX\s+\S+\s*<[^>]+>\s*)*(ASK|CONSTRUCT|DESCRIBE)\b"
)
_IRI_CONSTANT = re.compile(r"<([^<>\s]+)>")
_AGG_CLAUSE = re.compile(r"(?is)\b(GROUP\s+BY|HAVING)\b")
_SELECT_AGG = re.compile(
    r"(?is)\b(MIN|MAX|COUNT|SUM|AVG|SAMPLE|GROUP_CONCAT)\s*\("
)
_CONV: dict[str, Any] = {}

# Official F1 fields, exclusive by module so one IRI is extra-FP at most once.
# Steps gold has no vessel keys; usedDevice still scores (HeatChillDevice).
# Chemicals F1 is input-name lists only (not supplier/purity/output).
# Char scores product identity + HNMR/EA/IR values, not characterisation devices.
# CBU scores MOP CCDC + CBU formula/species names.
STEP_OPERATION_TYPES = frozenset(
    {
        "Add",
        "HeatChill",
        "Filter",
        "Sonicate",
        "Dissolve",
        "Stir",
        "Transfer",
        "Wash",
        "Dry",
        "Collect",
        "PH",
        "Degas",
        "Evaporate",
        "Extract",
        "Quench",
        "Purify",
        "Concentrate",
        "Reflux",
        "Crystallize",
        "Distill",
        "Grind",
        "Mix",
        "Separate",
        "Centrifuge",
        "SynthesisStep",
        "Step",
    }
)
STEP_FIELD_TYPES = frozenset(
    {
        "Duration",
        "Temperature",
        "TemperatureRate",
        "VesselEnvironment",
        "HeatChillDevice",
        "Volume",
        "Mass",
    }
)
CHEMICALS_SCORED_TYPES = frozenset({"ChemicalInput"})
# Official chemicals F1 is a flat input-name list (synthesis-level
# hasChemicalInput ∪ Add hasAddedChemicalInput). TBox still requires a
# fresh ChemicalInput per Filter/Separate/Dry/Evaporate workup use; those
# occurrences are not 1:1 with GT and must not become extra FP.
CHEMICALS_INPUT_PREDICATES = frozenset(
    {"hasChemicalInput", "hasAddedChemicalInput"}
)
CHEMICALS_PROCESS_MEDIUM_PREDICATES = frozenset(
    {
        "hasWashingSolvent",
        "hasSeparationSolvent",
        "hasDryingAgent",
        "removesSpecies",
    }
)
# Char official F1 compares one product record's HNMR/EA/IR *strings*.
# TBox-faithful ABox reifies CHN/peaks/formula as extra individuals; those
# types are not 1:1 with GT and must not become extra FP.
CHAR_SCORED_TYPES = frozenset(
    {
        "Species",
        "HNMRData",
        "InfraredSpectroscopyData",
        "Solvent",
        "Material",
        "CCDCNumber",
    }
)
CBU_SCORED_TYPES = frozenset(
    {
        "MetalOrganicPolyhedron",
        "CoordinationCage",
        "MolecularCage",
        "ChemicalBuildingUnit",
    }
)
MODULE_SCORED_TYPES = {
    "cbu": CBU_SCORED_TYPES,
    "characterisation": CHAR_SCORED_TYPES,
    "chemicals": CHEMICALS_SCORED_TYPES,
    "steps": STEP_OPERATION_TYPES | STEP_FIELD_TYPES,
}
OWNER_ORDER = ("cbu", "characterisation", "chemicals", "steps")


def type_local_name(iri: object) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def instance_type_locals(graph: Graph, node: URIRef) -> set[str]:
    return {
        type_local_name(obj)
        for obj in graph.objects(node, RDF.type)
        if type_local_name(obj)
    }


def owning_module(type_names: Iterable[str]) -> str | None:
    """Map RDF type locals to the single official-F1 module that owns them."""
    names = {str(name) for name in type_names if name}
    for module in OWNER_ORDER:
        if names & MODULE_SCORED_TYPES[module]:
            return module
    return None


def incoming_predicate_locals(graph: Graph, node: URIRef) -> set[str]:
    return {
        type_local_name(pred)
        for _, pred, _ in graph.triples((None, None, node))
        if type_local_name(pred)
    }


def is_process_medium_chemical_input(graph: Graph, node: URIRef) -> bool:
    """True for TBox workup ChemicalInput that official chemicals F1 does not score."""
    if "ChemicalInput" not in instance_type_locals(graph, node):
        return False
    preds = incoming_predicate_locals(graph, node)
    if preds & CHEMICALS_INPUT_PREDICATES:
        return False
    return bool(preds & CHEMICALS_PROCESS_MEDIUM_PREDICATES)


def is_aggregate_sparql(query: str) -> bool:
    """True when ``SELECT *`` would be illegal or empty under rdflib."""
    text = str(query or "")
    if not text.strip():
        return False
    if _AGG_CLAUSE.search(text):
        return True
    head_match = re.search(r"(?is)\bSELECT\b(.+?)(?=\s+WHERE\b)", text)
    if not head_match:
        return False
    return bool(_SELECT_AGG.search(head_match.group(1)))


def rewrite_select_star(query: str) -> str:
    """Replace SELECT variable lists with ``SELECT *`` so WHERE vars bind."""
    text = str(query or "")
    if not text.strip():
        return text
    if _ASK_OR_CONSTRUCT.search(text):
        return text
    if is_aggregate_sparql(text):
        return text
    return _SELECT_HEAD.sub(
        lambda match: f"SELECT{match.group(1) or ''} * ",
        text,
    )


def iris_in_query_text(query: str) -> set[URIRef]:
    """Collect ``<iri>`` constants from a SPARQL string (PREFIX + WHERE)."""
    found: set[URIRef] = set()
    for match in _IRI_CONSTANT.finditer(str(query or "")):
        iri = match.group(1).strip()
        if iri:
            found.add(URIRef(iri))
    return found


def _as_uriref(value: Any) -> URIRef | None:
    if isinstance(value, URIRef):
        return value
    return None


def _note_term(store: set[URIRef], value: Any) -> None:
    iri = _as_uriref(value)
    if iri is not None:
        store.add(iri)


def _note_bindings(store: set[URIRef], bindings: Any) -> None:
    if not bindings:
        return
    if isinstance(bindings, dict):
        for value in bindings.values():
            _note_term(store, value)
        return
    try:
        for value in bindings.values():
            _note_term(store, value)
    except Exception:
        return


@contextlib.contextmanager
def collect_sparql_coverage(covered: set[URIRef] | None = None):
    """Patch ``Graph.query`` process-wide and collect bound instance IRIs."""
    store = covered if covered is not None else set()
    original = Graph.query

    def wrapped(self, query_object, processor="sparql", result="sparql", initNs=None, initBindings=None, **kwargs):
        text = query_object if isinstance(query_object, str) else str(query_object)
        store.update(iris_in_query_text(text))
        _note_bindings(store, initBindings)
        rewritten = rewrite_select_star(text) if isinstance(query_object, str) else query_object
        cover_query = rewritten
        try:
            cover_rows = original(
                self,
                cover_query,
                processor=processor,
                result=result,
                initNs=initNs,
                initBindings=initBindings,
                **kwargs,
            )
        except Exception:
            cover_rows = original(
                self,
                query_object,
                processor=processor,
                result=result,
                initNs=initNs,
                initBindings=initBindings,
                **kwargs,
            )
            cover_query = query_object
        try:
            for row in cover_rows:
                if hasattr(row, "__iter__") and not isinstance(row, (str, bytes)):
                    for cell in row:
                        _note_term(store, cell)
                else:
                    _note_term(store, row)
        except TypeError:
            pass
        if cover_query is query_object:
            return cover_rows
        return original(
            self,
            query_object,
            processor=processor,
            result=result,
            initNs=initNs,
            initBindings=initBindings,
            **kwargs,
        )

    Graph.query = wrapped  # type: ignore[method-assign]
    try:
        yield store
    finally:
        Graph.query = original  # type: ignore[method-assign]


def is_coverage_vocab(iri: str, vocab: set[str]) -> bool:
    text = str(iri or "").strip()
    if is_vocabulary_iri(text, vocab):
        return True
    return any(text.startswith(prefix) for prefix in EXTRA_VOCAB_PREFIXES)


def tboxes_without_om2(tbox_paths: Sequence[Path]) -> list[Path]:
    """Drop OM-2 from a T-Box list (HermiT should not load the unit catalog)."""
    kept: list[Path] = []
    for path in tbox_paths:
        if Path(path).name.lower() in OM2_NAME:
            continue
        kept.append(Path(path))
    return kept


def primary_tbox_no_om2(ontology: str) -> list[Path]:
    from .check import resolve_ontology_tboxes

    tboxes, _ = resolve_ontology_tboxes(ontology)
    filtered = tboxes_without_om2(tboxes)
    if not filtered:
        return []
    return [filtered[0]]


def coverage_vocab(tbox_paths: Sequence[Path]) -> set[str]:
    """T-Box IRIs plus OM-2 catalog individuals when the file is present."""
    paths = [Path(item) for item in tbox_paths if Path(item).is_file()]
    om2 = _REPO_ROOT / "data" / "ontologies" / "om2.ttl"
    if om2.is_file() and om2 not in paths:
        paths.append(om2)
    if not paths:
        return set(EXTRA_VOCAB_PREFIXES)
    return vocabulary_iris(load_tbox(paths))


def typed_instances_for_coverage(
    graph: Graph,
    vocab: set[str],
    *,
    type_prefix: str | None = None,
    scored_modules: Sequence[str] | None = None,
) -> set[URIRef]:
    abox = instance_graph(graph)
    nodes = {
        node
        for node in typed_instance_nodes(abox, vocab)
        if not is_coverage_vocab(str(node), vocab)
    }
    if type_prefix:
        kept: set[URIRef] = set()
        for node in nodes:
            types = [str(obj) for obj in abox.objects(node, RDF.type)]
            if any(item.startswith(type_prefix) for item in types):
                kept.add(node)
        nodes = kept
    if scored_modules:
        allowed = {str(item) for item in scored_modules}
        nodes = {
            node
            for node in nodes
            if owning_module(instance_type_locals(abox, node)) in allowed
        }
        if "chemicals" in allowed:
            nodes = {
                node
                for node in nodes
                if not is_process_medium_chemical_input(abox, node)
            }
    return nodes


def orphan_iris(
    graph: Graph,
    covered: Iterable[URIRef],
    vocab: set[str],
    *,
    type_prefix: str | None = None,
    scored_modules: Sequence[str] | None = None,
) -> set[URIRef]:
    instances = typed_instances_for_coverage(
        graph,
        vocab,
        type_prefix=type_prefix,
        scored_modules=scored_modules,
    )
    covered_set = {iri for iri in covered if isinstance(iri, URIRef)}
    return instances - covered_set


def f1_from_counts(tp: int, fp: int, fn: int) -> float:
    denom = 2 * tp + fp + fn
    if denom <= 0:
        return 0.0
    return 2.0 * tp / denom


@contextlib.contextmanager
def _quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def _stub_om2_runtime() -> None:
    """Satisfy step conversion's OM-2 import without Reproduction's ``src``."""
    parent = "src.agents.scripts_and_prompts_generation"
    child = parent + ".fixed_om2_runtime"
    if child in sys.modules:
        return
    pkg = sys.modules.get(parent)
    if pkg is None:
        pkg = types.ModuleType(parent)
        pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules[parent] = pkg
        agents = sys.modules.get("src.agents")
        if agents is not None:
            setattr(agents, "scripts_and_prompts_generation", pkg)
    mod = types.ModuleType(child)

    def resolve_om2_unit(unit: str):
        text = str(unit or "").strip()
        return URIRef(text) if text else URIRef(
            "http://www.ontology-of-units-of-measure.org/resource/om-2/one"
        )

    mod.resolve_om2_unit = resolve_om2_unit  # type: ignore[attr-defined]
    sys.modules[child] = mod
    setattr(pkg, "fixed_om2_runtime", mod)


def _namespace_package(name: str, path: Path) -> None:
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = mod


def _load_reproduction_modules() -> dict[str, Any]:
    """Import Reproduction conversion without shadowing this repo's ``src``."""
    if _CONV:
        return _CONV
    if not REPRO_ROOT.is_dir():
        raise FileNotFoundError(f"Reproduction repo not found: {REPRO_ROOT}")
    _stub_om2_runtime()
    if str(REPRO_ROOT) not in sys.path:
        sys.path.append(str(REPRO_ROOT))
    saved = {
        key: sys.modules[key]
        for key in list(sys.modules)
        if key == "scripts" or key.startswith("scripts.")
    }
    for key in list(saved):
        del sys.modules[key]
    _namespace_package("scripts", REPRO_ROOT / "scripts")
    try:
        from scripts.output_conversion_ttl_to_json import (  # type: ignore
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
        from scripts.medical_ttl_to_csv_sparql import (  # type: ignore
            Q_FIND_CASES,
            extract_row_from_ttl,
        )

        _CONV["steps"] = steps
        _CONV["chemicals"] = chemicals
        _CONV["characterisation"] = characterisation
        _CONV["cbu"] = cbu
        _CONV["medical_extract"] = extract_row_from_ttl
        _CONV["medical_q"] = Q_FIND_CASES
    finally:
        sys.modules.update(saved)
    return _CONV


def run_steps_conversion(graph: Graph) -> None:
    conv = _load_reproduction_modules()["steps"]
    namespaces = conv.get_namespaces(graph)
    syntheses = conv.query_chemical_syntheses(graph, namespaces)
    if not syntheses:
        syntheses = conv.query_syntheses_via_steps(graph, namespaces)
    if syntheses:
        conv.build_json_structure(graph, namespaces, syntheses)


def run_chemicals_conversion(graph: Graph) -> None:
    conv = _load_reproduction_modules()["chemicals"]
    namespaces = conv.get_namespaces(graph)
    ontomops = conv.query_all_ontomops_data(graph, namespaces)
    syntheses = conv.query_synthesis_procedures(graph, namespaces)
    if syntheses:
        conv.build_json_structure(graph, namespaces, syntheses, ontomops)


def run_characterisation_conversion(graph: Graph) -> None:
    conv = _load_reproduction_modules()["characterisation"]
    namespaces = conv.get_namespaces(graph)
    devices = conv.query_characterisation_devices(graph, namespaces)
    characterisations = conv.query_characterisation_data(graph, namespaces)
    conv.build_json_structure(devices, characterisations)


def run_cbu_conversion(graph: Graph) -> None:
    """Run the module's SPARQL (not ``build_cbu_json_from_graph`` triple walk)."""
    conv = _load_reproduction_modules()["cbu"]
    namespaces = conv.get_namespaces(graph)
    if "ontomops" not in namespaces:
        from rdflib import Namespace

        namespaces["ontomops"] = Namespace(
            "https://www.theworldavatar.com/kg/ontomops/"
        )
    if "rdfs" not in namespaces:
        from rdflib.namespace import RDFS

        namespaces["rdfs"] = RDFS
    mops = conv.query_mop_data(graph, namespaces)
    cbu_uris: list[str] = []
    for mop in mops:
        cbu_uris.extend(uri for uri in mop.get("cbu_uris") or [] if uri)
    if cbu_uris:
        conv.query_cbu_details(graph, namespaces, cbu_uris)


def run_medical_conversion(ttl_path: Path) -> None:
    conv = _load_reproduction_modules()
    extract = conv["medical_extract"]
    data_dir = ttl_path.parent
    for _ in range(6):
        if data_dir.name in {"runtime", "runs", "data"}:
            break
        data_dir = data_dir.parent
    extract(
        ttl_path,
        data_dir=data_dir,
        case_query=conv["medical_q"],
        case_literal_hops=2,
    )


MODULE_RUNNERS: dict[str, Callable[[Graph], None]] = {
    "steps": run_steps_conversion,
    "chemicals": run_chemicals_conversion,
    "characterisation": run_characterisation_conversion,
    "cbu": run_cbu_conversion,
}

MODULE_ONTOLOGY = {
    "steps": "ontosynthesis",
    "chemicals": "ontosynthesis",
    "characterisation": "ontospecies",
    "cbu": "ontomops",
    "medical": "medical",
}


def cover_graph(graph: Graph, module: str) -> set[URIRef]:
    """Run one conversion module under SPARQL interception; return covered IRIs."""
    return cover_graph_modules(graph, [module])


def cover_graph_modules(graph: Graph, modules: Sequence[str]) -> set[URIRef]:
    """Run several conversion modules under one SPARQL interceptor."""
    runners = []
    for module in modules:
        runner = MODULE_RUNNERS.get(module)
        if runner is None:
            raise KeyError(f"Unknown conversion module: {module}")
        runners.append(runner)
    with collect_sparql_coverage() as covered:
        with _quiet():
            for runner in runners:
                runner(graph)
    return set(covered)


def _chemistry_scored_modules(run_modules: Sequence[str]) -> list[str] | None:
    scored = [module for module in run_modules if module in MODULE_SCORED_TYPES]
    return scored or None


def cover_ttl_files(
    ttl_paths: Sequence[Path],
    module: str,
    *,
    tbox_paths: Sequence[Path] | None = None,
    type_prefix: str | None = None,
    modules: Sequence[str] | None = None,
    scored_only: bool = True,
) -> dict[str, Any]:
    """Load TTL, run conversion SPARQL, and count uncovered scored instances."""
    graph = Graph()
    loaded: list[str] = []
    for path in ttl_paths:
        resolved = Path(path)
        if not resolved.is_file():
            continue
        graph.parse(str(resolved), format="turtle")
        loaded.append(str(resolved))
    run_modules = list(modules or [module])
    ontology = MODULE_ONTOLOGY.get(run_modules[0], "ontosynthesis")
    tboxes = list(tbox_paths or primary_tbox_no_om2(ontology))
    vocab = coverage_vocab(tboxes)
    covered: set[URIRef] = set()
    error = ""
    if loaded:
        try:
            covered = cover_graph_modules(graph, run_modules)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    scored_modules = _chemistry_scored_modules(run_modules) if scored_only else None
    instances = typed_instances_for_coverage(
        graph,
        vocab,
        type_prefix=type_prefix,
        scored_modules=scored_modules,
    )
    orphans = instances - covered
    return {
        "module": "+".join(run_modules),
        "files": loaded,
        "n_triple": len(graph),
        "n_instance": len(instances),
        "n_covered": len(instances & covered),
        "n_orphan": len(orphans),
        "orphan_iris": sorted(str(iri) for iri in orphans),
        "error": error,
    }


def cover_medical_ttl(
    ttl_path: Path,
    *,
    tbox_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    graph = load_ttl(ttl_path) if Path(ttl_path).is_file() else Graph()
    tboxes = list(tbox_paths or primary_tbox_no_om2("medical"))
    vocab = coverage_vocab(tboxes)
    covered: set[URIRef] = set()
    error = ""
    try:
        with collect_sparql_coverage(covered):
            with _quiet():
                run_medical_conversion(Path(ttl_path))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    instances = typed_instances_for_coverage(graph, vocab)
    orphans = instances - covered
    return {
        "module": "medical",
        "files": [str(ttl_path)],
        "n_triple": len(graph),
        "n_instance": len(instances),
        "n_covered": len(instances & covered),
        "n_orphan": len(orphans),
        "orphan_iris": sorted(str(iri) for iri in orphans),
        "error": error,
    }


def coverage_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry: ``{key, module, ttl, tboxes?}``."""
    module = str(payload["module"])
    ttl = payload.get("ttl") or payload.get("files") or []
    if isinstance(ttl, (str, Path)):
        paths = [Path(ttl)]
    else:
        paths = [Path(item) for item in ttl]
    tboxes = [Path(item) for item in (payload.get("tboxes") or [])]
    modules = payload.get("modules")
    if module == "medical":
        result = cover_medical_ttl(
            paths[0],
            tbox_paths=tboxes or None,
        )
    else:
        result = cover_ttl_files(
            paths,
            module,
            tbox_paths=tboxes or None,
            type_prefix=payload.get("type_prefix"),
            modules=modules,
            scored_only=payload.get("scored_only", True),
        )
    result["key"] = payload.get("key") or ""
    result["hash"] = payload.get("hash") or ""
    result["tag"] = payload.get("tag") or ""
    result["pack"] = payload.get("pack") or ""
    result["ontology"] = payload.get("ontology") or MODULE_ONTOLOGY.get(module, "")
    result["builder"] = payload.get("builder") or ""
    return result


def hermit_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry: HermiT on domain T-Box only (no SHACL, no OM-2)."""
    from .graph_index import instance_graph
    from .reasoners import check_owl_dl

    paths = [Path(item) for item in (payload.get("ttl") or [])]
    tboxes = [Path(item) for item in (payload.get("tboxes") or [])]
    graph = Graph()
    loaded: list[str] = []
    error = ""
    status = "skipped"
    inconsistent: list[str] = []
    try:
        with _quiet():
            for path in paths:
                if path.is_file():
                    graph.parse(str(path), format="turtle")
                    loaded.append(str(path))
            if tboxes:
                abox = instance_graph(graph) if loaded else Graph()
                findings = check_owl_dl(abox, tbox_paths=tboxes)
                skipped = [
                    item for item in findings if str(item.code).endswith("_SKIPPED")
                ]
                errors = [
                    item for item in findings if item.code == "OWL_DL_INCONSISTENT"
                ]
                if skipped and not errors:
                    status = "skipped"
                    error = skipped[0].message
                elif errors:
                    status = "inconsistent"
                    inconsistent = [iri for item in errors for iri in item.iris]
                else:
                    status = "consistent"
    except Exception as exc:
        status = "skipped"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "key": payload.get("key") or "",
        "hash": payload.get("hash") or "",
        "pack": payload.get("pack") or "",
        "ontology": payload.get("ontology") or "",
        "builder": payload.get("builder") or "",
        "files": loaded,
        "status": status,
        "inconsistent": inconsistent[:20],
        "error": error,
    }
