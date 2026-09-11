"""No-LLM tests for nested owned-dependent scalar injection and gates."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.extraction_prompt_generation.generate.extraction_prompts.contracts.generation_contract import (
    _format_nested_owned_dependent_scalar_contract,
    _nested_owned_dependent_scalar_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.gates import (
    _nested_owned_dependent_scalar_failures,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.materializable import (
    _detach_nested_owned_scalar_from_prompt,
    _splice_nested_owned_scalar_into_extraction_prompt,
    _strip_nested_owned_scalar_splice,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _iteration_owned_scope,
)
from src.extraction_prompt_generation.paths import repository_root


_FAKE_PARSED = {
    "classes": {
        "OwnerStep": {
            "parent_classes": ["OwnerParent"],
            "datatype_properties": {"hasOrder": "integer"},
            "object_properties": {"hasLinkedInput": "InputClass"},
        },
        "OwnerParent": {
            "parent_classes": [],
            "datatype_properties": {},
            "object_properties": {},
        },
        "InputClass": {
            "parent_classes": [],
            "datatype_properties": {
                "hasQty": "string",
                "hasAlias": "string",
            },
            "object_properties": {},
        },
        "OwnedPeer": {
            "parent_classes": [],
            "datatype_properties": {"hasPeerNote": "string"},
            "object_properties": {},
        },
    },
    "properties": {
        "hasLinkedInput": {
            "kind": "object",
            "domains": ["OwnerStep"],
            "range": "InputClass",
        },
        "hasPeer": {
            "kind": "object",
            "domains": ["OwnerStep"],
            "range": "OwnedPeer",
        },
        "hasQty": {"kind": "datatype", "domains": ["InputClass"]},
        "hasAlias": {"kind": "datatype", "domains": ["InputClass"]},
        "hasPeerNote": {"kind": "datatype", "domains": ["OwnedPeer"]},
    },
}

_FAKE_SCOPE = {
    "classes": ["OwnerStep", "OwnedPeer"],
    "object_properties": ["hasLinkedInput", "hasPeer"],
    "linked_materialization_classes": ["InputClass"],
}

_FAKE_ROWS = [
    {
        "owner_class_local": "OwnerStep",
        "predicate_local": "hasLinkedInput",
        "dependent_class_local": "InputClass",
        "property_locals": ["hasQty", "hasAlias"],
    }
]


def _fake_context() -> SimpleNamespace:
    return SimpleNamespace(
        parsed=_FAKE_PARSED,
        contract={
            "ontology_publish_contract": {
                "subclass_closure": [],
                "classes": [],
            },
            "relationship_tool_contracts": {},
            "materialization_operation_candidates": {"candidates": []},
            "materialization_operation_decisions": {"decisions": []},
            "ordered_member_profile": {},
        },
        iteration_blueprint={"iterations": []},
    )


class NestedOwnedDependentScalarContractTests(unittest.TestCase):
    def test_compiles_linked_range_scalars_under_owner_not_peer(self) -> None:
        rows = _nested_owned_dependent_scalar_contract(_fake_context(), _FAKE_SCOPE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner_class_local"], "OwnerStep")
        self.assertEqual(row["predicate_local"], "hasLinkedInput")
        self.assertEqual(row["dependent_class_local"], "InputClass")
        self.assertEqual(row["property_locals"], ["hasAlias", "hasQty"])

    def test_empty_when_iteration_has_no_linked_materialization_classes(self) -> None:
        scope = dict(_FAKE_SCOPE)
        scope["linked_materialization_classes"] = []
        rows = _nested_owned_dependent_scalar_contract(_fake_context(), scope)
        self.assertEqual(rows, [])

    def test_mechanical_render_overrides_closed_lists(self) -> None:
        text = _format_nested_owned_dependent_scalar_contract(_FAKE_ROWS)
        self.assertIn("mechanically injected", text)
        self.assertIn("overrides any closed scalar list", text)
        self.assertIn("`OwnerStep`", text)
        self.assertIn("`hasLinkedInput`", text)
        self.assertIn("`hasQty`", text)
        self.assertNotIn("Add", text)
        self.assertNotIn("ChemicalInput", text)

    def test_gate_rejects_closed_owner_list_without_dependent_scalars(self) -> None:
        authored = (
            "Extract the current target entity only.\n"
            "Optional semantic scalars:\n"
            "- OwnerStep only: hasOrder: <integer>\n"
            "- OwnerStep: hasLinkedInput: <verbatim token>\n"
        )
        failures = _nested_owned_dependent_scalar_failures(
            authored, _FAKE_ROWS, "EXTRACTION_ITER_3.md"
        )
        self.assertTrue(failures)
        self.assertTrue(any("hasQty" in item for item in failures))

    def test_gate_accepts_owner_occurrence_attachment(self) -> None:
        authored = (
            "Extract the current target entity only.\n"
            "Under each OwnerStep that asserts hasLinkedInput, emit "
            "hasAlias and hasQty as standalone property-local lines.\n"
        )
        failures = _nested_owned_dependent_scalar_failures(
            authored, _FAKE_ROWS, "EXTRACTION_ITER_3.md"
        )
        self.assertEqual(failures, [])

    def test_gate_rejects_dependent_class_only_mention(self) -> None:
        authored = (
            "Extract the current target entity only.\n"
            "InputClass scalars: hasAlias; hasQty.\n"
        )
        failures = _nested_owned_dependent_scalar_failures(
            authored, _FAKE_ROWS, "EXTRACTION_ITER_3.md"
        )
        self.assertTrue(failures)
        self.assertTrue(any("owner occurrence" in item for item in failures))

    def test_gate_ignores_mechanically_spliced_block(self) -> None:
        authored = "Extract the current target entity only.\n"
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = Path(raw_tmp) / "EXTRACTION_ITER_3.md"
            target.write_text(authored, encoding="utf-8")
            block = _format_nested_owned_dependent_scalar_contract(_FAKE_ROWS)
            _splice_nested_owned_scalar_into_extraction_prompt(target, block)
            spliced = target.read_text(encoding="utf-8")
            self.assertIn("hasQty", spliced)
            failures = _nested_owned_dependent_scalar_failures(
                spliced, _FAKE_ROWS, "EXTRACTION_ITER_3.md"
            )
            self.assertTrue(failures)
            stripped = _strip_nested_owned_scalar_splice(spliced)
            self.assertNotIn("hasQty", stripped)
            _detach_nested_owned_scalar_from_prompt(target)
            self.assertEqual(
                target.read_text(encoding="utf-8").strip(),
                authored.strip(),
            )

    def test_historical_002_passes_and_003_fails_when_present(self) -> None:
        root = repository_root()
        pack_003 = (
            root / "generated" / "runs" / "0908-extraction-prompt-003"
        )
        pack_002 = (
            root / "generated" / "runs" / "0908-extraction-prompt-002"
        )
        parsed_path = (
            pack_003
            / "ontology_structures"
            / "ontosynthesis"
            / "parsed.json"
        )
        plan_path = pack_003 / "iterations" / "ontosynthesis" / "iterations.json"
        iter3_003 = (
            pack_003 / "prompts" / "ontosynthesis" / "EXTRACTION_ITER_3.md"
        )
        iter3_002 = (
            pack_002 / "prompts" / "ontosynthesis" / "EXTRACTION_ITER_3.md"
        )
        if not all(
            path.is_file()
            for path in (parsed_path, plan_path, iter3_003, iter3_002)
        ):
            self.skipTest("local 002/003 prompt packs are not present")
        parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        iteration = next(
            item
            for item in plan.get("iterations") or []
            if item.get("iteration_number") == 3
        )
        context = SimpleNamespace(
            parsed=parsed,
            contract={
                "ontology_publish_contract": {
                    "subclass_closure": [],
                    "classes": [],
                },
                "relationship_tool_contracts": {},
                "materialization_operation_candidates": {"candidates": []},
                "materialization_operation_decisions": {"decisions": []},
                "ordered_member_profile": {},
            },
            iteration_blueprint=plan,
        )
        rows = _nested_owned_dependent_scalar_contract(
            context, _iteration_owned_scope(iteration)
        )
        self.assertTrue(rows)
        fail_003 = _nested_owned_dependent_scalar_failures(
            iter3_003.read_text(encoding="utf-8"),
            rows,
            "EXTRACTION_ITER_3.md",
        )
        fail_002 = _nested_owned_dependent_scalar_failures(
            iter3_002.read_text(encoding="utf-8"),
            rows,
            "EXTRACTION_ITER_3.md",
        )
        self.assertTrue(fail_003)
        self.assertFalse(fail_002)


if __name__ == "__main__":
    unittest.main()
