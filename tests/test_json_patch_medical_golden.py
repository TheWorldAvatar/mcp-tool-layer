from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path

from rdflib import RDF, RDFS, Literal, URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)


DEFAULT_GENERATED_MEDICAL_DIR = Path(
    "ai_generated_contents_agent_candidate_json_medical_one_script/scripts/medical"
)
TARGET_SCRIPT = "medical_creation_checks.py"


def _py_name(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "item"


def _generated_dir() -> Path:
    return Path(os.environ.get("MEDICAL_CHECKS_SCRIPT_DIR") or DEFAULT_GENERATED_MEDICAL_DIR)


def _expected_classes() -> list[str]:
    return sorted((_medical_context().parsed.get("classes") or {}).keys())


def _medical_context():
    meta_task_config = os.environ.get("MEDICAL_META_TASK_CONFIG")
    context = build_agentic_generation_context(
        ontology_name="medical",
        meta_task_config_path=meta_task_config if meta_task_config else None,
        output_root=Path("tmp/agentic_generation/test_medical_golden_context"),
        write_files=False,
    )
    return context


def _string_constants(node: ast.AST) -> set[str]:
    return {child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def _import_generated_module(scripts_dir: Path):
    package_name = "_golden_generated_medical"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.medical_creation_checks"
    spec = importlib.util.spec_from_file_location(module_name, scripts_dir / TARGET_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not create import spec for generated medical checks script")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, sys.modules[f"{package_name}.medical_creation_base"]


def _import_base_module(scripts_dir: Path):
    package_name = "_golden_generated_medical_base"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.medical_creation_base"
    spec = importlib.util.spec_from_file_location(module_name, scripts_dir / "medical_creation_base.py")
    if spec is None or spec.loader is None:
        raise AssertionError("Could not create import spec for generated medical base script")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestJsonPatchMedicalGolden(unittest.TestCase):
    def setUp(self) -> None:
        self.scripts_dir = _generated_dir()
        self.target = self.scripts_dir / TARGET_SCRIPT
        if not self.target.is_file():
            self.skipTest(
                f"Missing generated medical script {self.target}; "
                "run json_patch_medical_one_script.py, set MEDICAL_CHECKS_SCRIPT_DIR, "
                "or restore the tree from archive/workspace_cleanup_20260511/"
            )
        self.expected_classes = _expected_classes()
        self.source = self.target.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source, filename=str(self.target))

    def test_static_shape_is_exact(self) -> None:
        top_level = self.tree.body
        self.assertIsInstance(top_level[0], ast.ImportFrom)
        self.assertEqual(top_level[0].module, "__future__")
        self.assertEqual([alias.name for alias in top_level[0].names], ["annotations"])

        self.assertIsInstance(top_level[1], ast.Import)
        self.assertEqual([alias.name for alias in top_level[1].names], ["json"])

        self.assertIsInstance(top_level[2], ast.ImportFrom)
        self.assertEqual(top_level[2].level, 1)
        self.assertEqual(top_level[2].module, "medical_creation_base")
        self.assertEqual([alias.name for alias in top_level[2].names], ["GRAPH", "NS", "RDF"])

        functions = [node for node in top_level if isinstance(node, ast.FunctionDef)]
        expected_function_names = [f"check_existing_{_py_name(class_local)}s" for class_local in self.expected_classes]
        check_function_names = [node.name for node in functions if node.name.startswith("check_existing_")]
        self.assertEqual(check_function_names, expected_function_names)

    def test_each_checker_matches_golden_ast_contract(self) -> None:
        function_by_name = {
            node.name: node for node in self.tree.body if isinstance(node, ast.FunctionDef)
        }
        for class_local in self.expected_classes:
            with self.subTest(class_local=class_local):
                fn_name = f"check_existing_{_py_name(class_local)}s"
                node = function_by_name[fn_name]
                self.assertEqual(node.args.args, [])
                self.assertIsInstance(node.returns, ast.Name)
                self.assertEqual(node.returns.id, "str")
                self.assertIn(class_local, _string_constants(node))

    def test_runtime_reports_existing_instances_per_class(self) -> None:
        module, base = _import_generated_module(self.scripts_dir)
        base.GRAPH.remove((None, None, None))

        expected_iris_by_class: dict[str, set[str]] = {}
        for class_local in self.expected_classes:
            iris_1 = URIRef(base.NS[f"golden_{class_local}_1"])
            iris_2 = URIRef(base.NS[f"golden_{class_local}_2"])
            base.GRAPH.add((iris_1, RDF.type, base.NS[class_local]))
            base.GRAPH.add((iris_2, RDF.type, base.NS[class_local]))
            expected_iris_by_class[class_local] = {str(iris_1), str(iris_2)}

        for class_local in self.expected_classes:
            with self.subTest(class_local=class_local):
                fn = getattr(module, f"check_existing_{_py_name(class_local)}s")
                payload = json.loads(fn())
                self.assertEqual(payload, {
                    "status": "ok",
                    "class": class_local,
                    "iris": payload["iris"],
                })
                self.assertEqual(set(payload["iris"]), expected_iris_by_class[class_local])


class TestJsonPatchMedicalBaseGolden(unittest.TestCase):
    def setUp(self) -> None:
        self.scripts_dir = _generated_dir()
        self.target = self.scripts_dir / "medical_creation_base.py"
        if not self.target.is_file():
            self.skipTest(
                f"Missing generated medical base script {self.target}; "
                "run json_patch_medical_one_script.py, set MEDICAL_CHECKS_SCRIPT_DIR, "
                "or restore the tree from archive/workspace_cleanup_20260511/"
            )
        self.context = _medical_context()
        self.base = _import_base_module(self.scripts_dir)

    def test_base_exports_required_contract_surface(self) -> None:
        required_symbols = {
            "GRAPH",
            "NS",
            "RDF",
            "RDFS",
            "Literal",
            "URIRef",
            "PREDICATE_URIS",
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
        missing = sorted(symbol for symbol in required_symbols if not hasattr(self.base, symbol))
        self.assertEqual(missing, [])

    def test_base_namespace_and_predicates_match_medical_tbox(self) -> None:
        namespace = self.context.contract.get("namespace_uri")
        self.assertEqual(str(self.base.NS), namespace)

        expected_predicates = set((self.context.parsed.get("properties") or {}).keys())
        self.assertEqual(set(self.base.PREDICATE_URIS), expected_predicates)
        for local, iri in self.base.PREDICATE_URIS.items():
            self.assertTrue(str(iri).startswith(str(namespace)), local)

    def test_base_entity_creation_reuse_and_literal_linking(self) -> None:
        self.base.GRAPH.remove((None, None, None))
        self.base.CURRENT_ENTITY_CONTEXT = "golden-context"

        iri, created = self.base._create_entity("MedicalCase", "Golden Case", prefer_top=True)
        self.assertTrue(created)
        self.assertIn((iri, RDF.type, self.base.NS["MedicalCase"]), self.base.GRAPH)
        self.assertIn((iri, RDFS.label, Literal("Golden Case")), self.base.GRAPH)

        reused, created_again = self.base._create_entity("MedicalCase", "Golden Case", prefer_top=True)
        self.assertEqual(reused, iri)
        self.assertFalse(created_again)

        self.base._add_literal(str(iri), "Fall_Nr", "12345")
        self.assertIn((iri, URIRef(self.base.PREDICATE_URIS["Fall_Nr"]), Literal("12345")), self.base.GRAPH)

        object_properties = {
            local: prop
            for local, prop in (self.context.parsed.get("properties") or {}).items()
            if prop.get("type") == "object"
        }
        if object_properties:
            object_local, object_contract = next(iter(sorted(object_properties.items())))
            target_class = object_contract.get("range") or "MedicalCase"
            target_iri, _ = self.base._create_entity(target_class, "Golden Target")
            self.base._add_object(str(iri), object_local, str(target_iri))
            self.assertIn(
                (iri, URIRef(self.base.PREDICATE_URIS[object_local]), target_iri),
                self.base.GRAPH,
            )

    def test_base_label_scalar_splitter_is_contract_compatible(self) -> None:
        self.assertEqual(self.base._split_label_scalar("Drug A (12 mg)"), ("Drug A", "12 mg"))
        self.assertEqual(self.base._split_label_scalar("Plain Label"), ("Plain Label", ""))


if __name__ == "__main__":
    unittest.main()
