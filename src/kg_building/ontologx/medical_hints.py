"""Load medical extraction ledgers for OntoLogX."""

from __future__ import annotations

import json
from pathlib import Path

from extraction_hints import HINT_RUNS, HintEntity, _entity_with_budget
from src.extraction_runtime.names import entity_artifact_name, entity_scope_name


from paths import HERE, REPO_ROOT


def medical_runtime(hash_id: str, run: str | None = None) -> tuple[Path, str]:
    runs = [run] if run else list(HINT_RUNS)
    for candidate in runs:
        if not candidate:
            continue
        rooted = Path(candidate)
        extra = []
        if rooted.is_absolute() or any(sep in candidate for sep in ("/", "\\")):
            extra = [
                rooted,
                rooted / hash_id,
                rooted / "runtime" / hash_id,
            ]
        for base in extra + [
            REPO_ROOT / "scenarios" / "medical" / "runs" / candidate / "runtime" / hash_id,
            REPO_ROOT / "data_medical_from_human_domain_eval30_20260904" / hash_id,
            REPO_ROOT / "data_medical_new_cases" / hash_id,
        ]:
            if (base / "mcp_run" / "iter1_top_entities.json").is_file():
                return base, candidate
    raise FileNotFoundError(f"No medical runtime for {hash_id} in {runs}")


def load_medical_entities(hash_id: str) -> list[HintEntity]:
    runtime, run = medical_runtime(hash_id)
    tops = json.loads((runtime / "mcp_run" / "iter1_top_entities.json").read_text(encoding="utf-8"))
    entities: list[HintEntity] = []
    for index, record in enumerate(tops, start=1):
        label = str(record.get("label") or "").strip()
        uri = str(record.get("uri") or "").strip()
        if not label:
            continue
        scope = entity_scope_name(label, uri) if uri else label
        artifact = entity_artifact_name(label)
        candidates = [
            runtime / "mcp_run" / f"iter2_hints_{artifact}.txt",
            runtime / "mcp_run" / f"iter2_hints_{scope}.txt",
            runtime / "mcp_run" / f"iter2_hints_{label}.txt",
        ]
        path = next((item for item in candidates if item.is_file()), None)
        if path is None:
            matches = sorted((runtime / "mcp_run").glob("iter2_hints_*.txt"))
            path = matches[0] if len(matches) == 1 else None
        if path is None:
            raise FileNotFoundError(f"No medical iter2 hints for {hash_id} / {label!r}")
        entity = HintEntity(
            key=f"MedicalCase-{index}",
            label=label,
            path=path,
            run=run,
            text=path.read_text(encoding="utf-8"),
            uri=uri,
        )
        from kg_token_budget import entity_kg_building_budget

        detail = entity_kg_building_budget(
            hash_id,
            label,
            run=run,
            runtime=runtime,
        )
        entity = _entity_with_budget(entity, detail)
        entities.append(entity)
    if not entities:
        raise FileNotFoundError(f"No medical top entities for {hash_id}")
    return entities
