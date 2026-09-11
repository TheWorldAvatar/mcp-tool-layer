"""Map pipeline step names to `run_step` callables."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

STEP_MODULES = {
    "pdf_conversion": "src.extraction_runtime.steps.pdf_conversion.convert",
    "tbox_slim": "src.extraction_runtime.steps.tbox_slim.run",
    "top_entity_extraction": "src.extraction_runtime.steps.top_entity.extract",
    "top_entity_kg_building": "src.extraction_runtime.steps.top_entity.kg",
    "main_ontology_extractions": "src.extraction_runtime.steps.main_extraction.run",
    "main_kg_building": "src.extraction_runtime.steps.main_kg.run",
    "extensions_extractions": "src.extraction_runtime.steps.extensions.extract",
    "extensions_kg_building": "src.extraction_runtime.steps.extensions.kg",
    "mop_derivation": "src.extraction_runtime.steps.mop_derivation.derive",
}


def load_step_module(step_name: str) -> ModuleType | None:
    module_name = STEP_MODULES.get(step_name)
    if not module_name:
        print(f"[FAIL] Unknown step: {step_name}")
        return None
    try:
        return import_module(module_name)
    except Exception as exc:
        print(f"[FAIL] Could not import step {step_name}: {exc}")
        return None
