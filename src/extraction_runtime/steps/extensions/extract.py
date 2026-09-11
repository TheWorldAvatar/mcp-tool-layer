"""Extract extension hints from slim or conversion markdown."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.extraction_runtime.artifact_root import load_iterations_config, load_prompt
from src.extraction_runtime.llm import write_text
from src.extraction_runtime.models_map import get_extraction_model
from models.locked_llm import LOCKED_EXTRACTION_MODEL
from src.extraction_runtime.names import entity_artifact_name
from src.extraction_runtime.paper import load_paper_content
from src.extraction_runtime.locked_mechanisms import (
    assert_extraction_revision_locked,
    skip_extraction_judges,
    skip_if_already_revised,
)
from src.extraction_runtime.steps.extensions.queue import (
    configured_extensions,
    load_extension_queue,
)
from src.extraction_runtime.steps.main_extraction.prompts import (
    SEMANTIC_HINT_REPRESENTATION,
    append_ref_entity_output_boundary,
    append_semantic_hint_output_boundary,
    bind_runtime_context,
)
from src.extraction_runtime.steps.main_extraction.run import (
    _default_hint_representation,
    _run_extraction_with_judges,
)


def run_step(doi_hash: str, config: dict) -> bool:
    assert_extraction_revision_locked(config)
    data_dir = config.get("data_dir", "data")
    doi_folder = Path(data_dir) / doi_hash
    print(f">> Extensions Extractions: {doi_hash}")
    paper_content = load_paper_content(doi_hash, data_dir)
    if not paper_content.strip():
        print("[WARN] No usable paper content for extension extraction; continuing")
        return True
    extensions = configured_extensions(config)
    if not extensions:
        print("[WARN] No extension ontologies configured")
        (doi_folder / ".extensions_extractions_done").write_text("completed\n", encoding="utf-8")
        return True
    had_failures = False
    for extension in extensions:
        ontology_name = str(extension.get("name") or "").strip()
        upstream = str(extension.get("upstream_ontology") or "").strip()
        print(f"  [INFO] Extension: {ontology_name}")
        if not upstream:
            print(f"  [WARN] {ontology_name} has no upstream_ontology in domain config")
            had_failures = True
            continue
        try:
            queue = load_extension_queue(
                doi_folder=doi_folder,
                extension_name=ontology_name,
                main_ontology=upstream,
            )
        except Exception as exc:
            print(f"  [WARN] Extension queue failed for {ontology_name}: {exc}")
            had_failures = True
            continue
        if not queue:
            print(f"  [WARN] No inherited root entities for {ontology_name}")
            had_failures = True
            continue
        iterations = list((load_iterations_config(ontology_name).get("iterations") or []))
        if not iterations:
            print(f"  [WARN] iterations.json missing for {ontology_name}")
            had_failures = True
            continue
        for entity in queue:
            label = str(entity.get("label") or "")
            uri = str(entity.get("uri") or "")
            safe = entity_artifact_name(label)
            for iteration in iterations:
                iter_num = int(iteration.get("iteration_number") or 1)
                prompt_path = str(iteration.get("extraction_prompt") or "")
                if not prompt_path:
                    prompt_path = f"prompts/{ontology_name}/EXTRACTION_ITER_{iter_num}.md"
                template = load_prompt(prompt_path)
                if not template:
                    print(f"    [WARN] Empty extension prompt: {prompt_path}")
                    had_failures = True
                    continue
                hint_file = (
                    doi_folder
                    / "mcp_run"
                    / f"{ontology_name}_iter{iter_num}_hints_{safe}.txt"
                )
                if skip_if_already_revised("extension hints", hint_file):
                    continue
                prompt = bind_runtime_context(
                    template,
                    doi_hash=doi_hash,
                    entity_label=label,
                    entity_uri=uri,
                    source_text=paper_content,
                )
                representation = str(
                    iteration.get("hint_representation")
                    or _default_hint_representation(config)
                )
                prompt = (
                    append_semantic_hint_output_boundary(prompt)
                    if representation == SEMANTIC_HINT_REPRESENTATION
                    else append_ref_entity_output_boundary(prompt)
                )
                model = get_extraction_model(
                    str(iteration.get("model_config_key") or f"extension_{ontology_name}"),
                    default=LOCKED_EXTRACTION_MODEL,
                )
                hints = asyncio.run(
                    _run_extraction_with_judges(
                        prompt=prompt,
                        prompt_template=template,
                        model_name=model,
                        use_agent=False,
                        mcp_set_name=None,
                        mcp_tools=None,
                        representation=representation,
                        source_text=paper_content,
                        closed_ledger_revision=False,
                        hint_file=hint_file,
                        extraction_validation=iteration.get("extraction_validation"),
                        entity_label=label,
                        entity_uri=uri,
                        iter_num=iter_num,
                        accumulated_hints="",
                        skip_judges=skip_extraction_judges(config),
                    )
                )
                if not str(hints or "").strip():
                    print(
                        f"    [WARN] Empty extension hints for {label} "
                        "after payload retries"
                    )
                    had_failures = True
                    continue
                write_text(str(hint_file), hints)
                print(f"    [OK] Wrote {hint_file.name}")
    if had_failures:
        print(
            f"[WARN] Extensions extractions partial for {doi_hash}; "
            "continuing without the done marker"
        )
        return True
    (doi_folder / ".extensions_extractions_done").write_text("completed\n", encoding="utf-8")
    print(f"[OK] Extensions extractions completed: {doi_hash}")
    return True
