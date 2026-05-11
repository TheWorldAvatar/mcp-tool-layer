from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from rdflib import RDF, RDFS, Literal, URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)


DEFAULT_GENERATED_ONTOSYNTHESIS_DIR = Path(
    "ai_generated_contents_agent_candidate_json_ontosynthesis_one_script_json_patch/scripts/ontosynthesis"
)
SCRIPT_FILES = {
    "base": "ontosynthesis_creation_base.py",
    "entities": "ontosynthesis_creation_entities.py",
    "checks": "ontosynthesis_creation_checks.py",
    "relationships": "ontosynthesis_creation_relationships.py",
    "main": "main.py",
}


def _py_name(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "item"


def _generated_dir() -> Path:
    return Path(
        os.environ.get("ONTOSYNTHESIS_SCRIPT_DIR")
        or DEFAULT_GENERATED_ONTOSYNTHESIS_DIR
    )


def _ontosynthesis_context():
    return build_agentic_generation_context(
        ontology_name="ontosynthesis",
        output_root=Path("tmp/agentic_generation/test_ontosynthesis_golden_context"),
        write_files=False,
    )


def _expected_classes() -> list[str]:
    return sorted((_ontosynthesis_context().parsed.get("classes") or {}).keys())


def _expected_object_properties() -> list[str]:
    properties = _ontosynthesis_context().parsed.get("properties") or {}
    return sorted(
        local
        for local, spec in properties.items()
        if (spec or {}).get("kind") == "object"
    )


def _expected_namespace_uri() -> str:
    classes = _ontosynthesis_context().parsed.get("classes") or {}
    iri = str((classes.get("ChemicalSynthesis") or {}).get("iri") or "")
    if not iri:
        raise AssertionError(
            "ChemicalSynthesis IRI missing from parsed OntoSynthesis context"
        )
    return iri.rsplit("/", 1)[0] + "/"


def _import_package_module(scripts_dir: Path, module_stem: str):
    package_name = "_golden_generated_ontosynthesis"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.{module_stem}"
    spec = importlib.util.spec_from_file_location(
        module_name, scripts_dir / f"{module_stem}.py"
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not create import spec for {module_stem}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class OntoSynthesisGoldenBase(unittest.TestCase):
    scripts_dir: Path

    def setUp(self) -> None:
        self.scripts_dir = _generated_dir()
        if not self.scripts_dir.is_dir():
            self.skipTest(
                f"Missing generated scripts dir {self.scripts_dir}; "
                "run json_patch_one_script_generation.py for OntoSynthesis, set ONTOSYNTHESIS_SCRIPT_DIR, "
                "or restore ai_generated_contents_agent_candidate_json_ontosynthesis_one_script_json_patch "
                "from archive/workspace_cleanup_20260511/"
            )


class TestOntoSynthesisScriptInventoryGolden(OntoSynthesisGoldenBase):
    def test_expected_generated_scripts_exist(self) -> None:
        missing = [
            filename
            for filename in SCRIPT_FILES.values()
            if not (self.scripts_dir / filename).is_file()
        ]
        self.assertEqual(missing, [])


class TestOntoSynthesisBaseGolden(OntoSynthesisGoldenBase):
    def setUp(self) -> None:
        super().setUp()
        self.context = _ontosynthesis_context()
        self.base = _import_package_module(
            self.scripts_dir, "ontosynthesis_creation_base"
        )

    def test_base_exports_required_contract_surface(self) -> None:
        required_symbols = {
            "GRAPH",
            "NS",
            "OM2",
            "RDF",
            "RDFS",
            "Literal",
            "URIRef",
            "PREDICATE_URIS",
            "ORDERING_PROPERTY_LOCALS",
            "_format_success_json",
            "_format_error_json",
            "_create_entity",
            "_add_literal",
            "_add_object",
            "_find_by_type_and_label",
            "_split_label_scalar",
            "init_memory_wrapper",
            "export_memory_wrapper",
            "get_top_entity_iri",
        }
        missing = sorted(
            symbol for symbol in required_symbols if not hasattr(self.base, symbol)
        )
        self.assertEqual(missing, [])

    def test_base_namespace_and_predicates_match_tbox(self) -> None:
        self.assertEqual(str(self.base.NS), _expected_namespace_uri())

        expected = {
            local: str((spec or {}).get("iri") or "")
            for local, spec in (self.context.parsed.get("properties") or {}).items()
        }
        self.assertEqual(self.base.PREDICATE_URIS, expected)
        self.assertEqual(set(self.base.ORDERING_PROPERTY_LOCALS), {"hasOrder"})

    def test_base_entity_creation_reuse_order_and_links(self) -> None:
        self.base.GRAPH.remove((None, None, None))
        self.base.CURRENT_ENTITY_CONTEXT = "Golden Synthesis"

        iri, created = self.base._create_entity(
            "ChemicalSynthesis", "Golden Synthesis", prefer_top=True
        )
        self.assertTrue(created)
        self.assertIn((iri, RDF.type, self.base.NS.ChemicalSynthesis), self.base.GRAPH)
        self.assertIn((iri, RDFS.label, Literal("Golden Synthesis")), self.base.GRAPH)

        reused, created_again = self.base._create_entity(
            "ChemicalSynthesis", "Golden Synthesis", prefer_top=True
        )
        self.assertEqual(reused, iri)
        self.assertFalse(created_again)

        step_iri, _ = self.base._create_entity("SynthesisStep", "Golden Step")
        self.base._add_literal(str(step_iri), "hasOrder", "1")
        self.assertIn(
            (step_iri, URIRef(self.base.PREDICATE_URIS["hasOrder"]), Literal(1)),
            self.base.GRAPH,
        )

        output_iri, _ = self.base._create_entity("ChemicalOutput", "Golden Output")
        self.base._add_object(str(iri), "hasChemicalOutput", str(output_iri))
        self.assertIn(
            (iri, URIRef(self.base.PREDICATE_URIS["hasChemicalOutput"]), output_iri),
            self.base.GRAPH,
        )


class TestOntoSynthesisEntitiesGolden(OntoSynthesisGoldenBase):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.scripts_dir / SCRIPT_FILES["entities"]
        self.source = self.target.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source, filename=str(self.target))
        self.entities = _import_package_module(
            self.scripts_dir, "ontosynthesis_creation_entities"
        )
        self.base = sys.modules[
            "_golden_generated_ontosynthesis.ontosynthesis_creation_base"
        ]

    def test_entities_import_only_package_local_helpers(self) -> None:
        self.assertNotIn("universal_utils", self.source)
        self.assertNotIn("sandbox", self.source)

    def test_every_tbox_class_has_create_function(self) -> None:
        missing = [
            f"create_{_py_name(class_local)}"
            for class_local in _expected_classes()
            if not callable(
                getattr(self.entities, f"create_{_py_name(class_local)}", None)
            )
        ]
        self.assertEqual(missing, [])

    def test_create_function_signatures_cover_core_contracts(self) -> None:
        synthesis_sig = inspect.signature(self.entities.create_ChemicalSynthesis)
        self.assertIn("label", synthesis_sig.parameters)
        self.assertIn("hasChemicalOutput_label", synthesis_sig.parameters)
        self.assertIn("hasSynthesisStep_label", synthesis_sig.parameters)

        add_sig = inspect.signature(self.entities.create_Add)
        self.assertIn("hasOrder", add_sig.parameters)
        self.assertIn("hasAddedChemicalInput_label", add_sig.parameters)

    def test_chemical_synthesis_does_not_invent_required_link_placeholders(self) -> None:
        self.base.GRAPH.remove((None, None, None))
        self.base.CURRENT_ENTITY_CONTEXT = "Golden Product synthesis"
        payload = json.loads(
            self.entities.create_ChemicalSynthesis("Golden Product synthesis")
        )
        self.assertEqual(payload["status"], "ok")
        subject = URIRef(payload["iri"])

        outputs = list(
            self.base.GRAPH.objects(
                subject, URIRef(self.base.PREDICATE_URIS["hasChemicalOutput"])
            )
        )
        steps = list(
            self.base.GRAPH.objects(
                subject, URIRef(self.base.PREDICATE_URIS["hasSynthesisStep"])
            )
        )
        self.assertEqual(
            outputs,
            [],
            "Required ChemicalOutput links must come from source-supported hints",
        )
        self.assertEqual(
            steps,
            [],
            "Required SynthesisStep links must come from source-supported hints",
        )

    def test_add_step_materializes_step_scoped_added_input(self) -> None:
        self.base.GRAPH.remove((None, None, None))
        self.base.CURRENT_ENTITY_CONTEXT = "Golden Synthesis"
        payload = json.loads(
            self.entities.create_Add(
                "Add reagent",
                hasOrder=1,
                hasAddedChemicalInput_label="Golden Reagent (12 mg)",
            )
        )
        self.assertEqual(payload["status"], "ok")
        subject = URIRef(payload["iri"])
        targets = list(
            self.base.GRAPH.objects(
                subject, URIRef(self.base.PREDICATE_URIS["hasAddedChemicalInput"])
            )
        )
        self.assertEqual(len(targets), 1)
        self.assertIn(
            (targets[0], RDF.type, self.base.NS.ChemicalInput), self.base.GRAPH
        )


class TestOntoSynthesisChecksGolden(OntoSynthesisGoldenBase):
    def setUp(self) -> None:
        super().setUp()
        self.checks = _import_package_module(
            self.scripts_dir, "ontosynthesis_creation_checks"
        )
        self.base = sys.modules[
            "_golden_generated_ontosynthesis.ontosynthesis_creation_base"
        ]

    def test_each_checker_reports_existing_instances(self) -> None:
        self.base.GRAPH.remove((None, None, None))
        for class_local in _expected_classes():
            iri = self.base.NS[f"golden_{class_local}"]
            self.base.GRAPH.add((iri, RDF.type, self.base.NS[class_local]))

        for class_local in _expected_classes():
            with self.subTest(class_local=class_local):
                fn = getattr(self.checks, f"check_existing_{_py_name(class_local)}s")
                payload = json.loads(fn())
                self.assertEqual(payload["status"], "ok")
                self.assertEqual(payload["class"], class_local)
                self.assertIn(
                    str(self.base.NS[f"golden_{class_local}"]), payload["iris"]
                )


class TestOntoSynthesisRelationshipsGolden(OntoSynthesisGoldenBase):
    def setUp(self) -> None:
        super().setUp()
        self.relationships = _import_package_module(
            self.scripts_dir, "ontosynthesis_creation_relationships"
        )
        self.base = sys.modules[
            "_golden_generated_ontosynthesis.ontosynthesis_creation_base"
        ]

    def test_every_object_property_has_add_function(self) -> None:
        missing = [
            f"add_{_py_name(prop_local)}"
            for prop_local in _expected_object_properties()
            if not callable(
                getattr(self.relationships, f"add_{_py_name(prop_local)}", None)
            )
        ]
        self.assertEqual(missing, [])

    def test_relationship_function_links_uri_refs(self) -> None:
        self.base.GRAPH.remove((None, None, None))
        subject = self.base.NS["golden_synthesis"]
        obj = self.base.NS["golden_output"]
        payload = json.loads(
            self.relationships.add_hasChemicalOutput(str(subject), str(obj))
        )
        self.assertEqual(payload["status"], "ok")
        self.assertIn(
            (subject, URIRef(self.base.PREDICATE_URIS["hasChemicalOutput"]), obj),
            self.base.GRAPH,
        )


class TestOntoSynthesisMainGolden(OntoSynthesisGoldenBase):
    def setUp(self) -> None:
        super().setUp()
        self.main = _import_package_module(self.scripts_dir, "main")

    def test_main_exports_mcp_and_materialization_maps(self) -> None:
        self.assertIsNotNone(getattr(self.main, "mcp", None))
        self.assertEqual(set(self.main._CREATE_TOOLS), set(_expected_classes()))
        self.assertEqual(set(self.main._ADD_TOOLS), set(_expected_object_properties()))
        self.assertIn("Add", self.main._ORDERED_MEMBER_CLASSES)
        yield_specs = [
            spec
            for spec in self.main._TOP_LINK_SPECS
            if spec.get("predicate") == "hasYield"
        ]
        self.assertEqual(len(yield_specs), 1)
        self.assertIn("Yield", yield_specs[0].get("accepted_classes", []))
        self.assertIn(
            "hasAddedChemicalInput",
            {
                item["predicate"]
                for item in self.main._REQUIRED_STEP_SCOPED_OBJECT_PROPERTIES
            },
        )

    def test_materializer_links_yield_hint_to_top_entity(self) -> None:
        materialize = getattr(self.main, "materialize_hints")
        if not callable(materialize) and callable(getattr(materialize, "fn", None)):
            materialize = materialize.fn

        previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(prefix="ontosyn_golden_yield_") as tmp_dir:
                os.environ["TWA_AGENTIC_DATA_DIR"] = tmp_dir
                result = json.loads(
                    materialize(
                        "golden-yield-doi",
                        "Golden Product",
                        "Golden Product",
                        json.dumps(
                            {
                                "Add": {
                                    "label": "Add reagent",
                                    "hasOrder": 1,
                                    "hasAddedChemicalInput_label": "Golden Reagent",
                                },
                                "Yield": {"label": "45%"},
                            }
                        ),
                    )
                )
        finally:
            if previous_data_dir is None:
                os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
            else:
                os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir

        self.assertEqual(result["status"], "ok", result)
        graph = self.main.GRAPH
        top = URIRef(result["top_iri"])
        yield_targets = list(graph.objects(top, URIRef(self.main.NS["hasYield"])))
        self.assertEqual(len(yield_targets), 1)
        self.assertIn((yield_targets[0], RDF.type, self.main.NS.Yield), graph)


if __name__ == "__main__":
    unittest.main()
