"""Build the main-ontology KG from per-entity hints."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Any

from src.extraction_runtime.names import entity_artifact_name
from src.kg_building.pipeline.main_kg.agent import (
    build_kg_prompt,
    kg_mcp_binding,
    ontology_from_config,
    run_kg_agent,
)
from src.kg_building.pipeline.main_kg.hints import (
    MAIN_KG_HINT_ITERS,
    find_hints_file,
    load_entity_hints,
)
from src.kg_building.pipeline.main_kg.publish import entity_ttl_path, publish_entity_ttl
from src.kg_building.pipeline.main_kg.repair import graph_is_parseable
from src.kg_building.pipeline.main_kg.traces import write_main_kg_token_trace
from src.extraction_runtime.steps.top_entity.identity import load_top_entities_json
from src.kg_building.revision_lock import assert_kg_revision_locked_off

EXTRACTION_DONE_MARKER = ".main_ontology_extractions_done"


def _memory_candidate(doi_folder: Path, entity_label: str, entity_uri: str) -> Path | None:
    from src.extraction_runtime.names import entity_scope_name

    memory = doi_folder / "memory"
    names = [
        f"{entity_scope_name(entity_label, entity_uri)}.ttl",
        f"{entity_artifact_name(entity_label)}.ttl",
    ]
    for name in names:
        path = memory / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    if not memory.is_dir():
        return None
    ttl_files = sorted(
        (path for path in memory.glob("*.ttl") if path.stat().st_size > 0),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return ttl_files[0] if ttl_files else None


def _hint_files(doi_folder: Path, entity_safe: str) -> list[Path]:
    mcp_run = doi_folder / "mcp_run"
    found: list[Path] = []
    for iter_num in MAIN_KG_HINT_ITERS:
        path = find_hints_file(
            mcp_run_dir=mcp_run, iter_num=iter_num, entity_safe=entity_safe
        )
        if path is not None:
            found.append(path)
    return found


def should_reuse_published_ttl(
    dest: Path, *, doi_folder: Path, entity_safe: str
) -> bool:
    """Keep a published TTL only when it is newer than iter2/3/4 ledgers."""
    if not dest.is_file() or dest.stat().st_size <= 0 or not graph_is_parseable(dest):
        return False
    hints = _hint_files(doi_folder, entity_safe)
    if not hints:
        return False
    latest_hint = max(path.stat().st_mtime for path in hints)
    return dest.stat().st_mtime >= latest_hint


def _run_isolated(coro):
    """Official s1ka shape: one entity, one ``asyncio.run``."""
    return asyncio.run(coro)


def _spawnable_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key != "domain"}


def _bind_spawned_config(config: dict[str, Any]) -> dict[str, Any]:
    from src.extraction_runtime.domain_binding import (
        load_runtime_domain,
        runtime_domain_from_config,
    )

    bound = dict(config)
    if runtime_domain_from_config(bound) is None:
        bound["domain"] = load_runtime_domain(
            str(bound.get("ontology_name") or bound.get("ontology") or "ontosynthesis")
        )
    return bound


def _execute_kg_entity(job: dict[str, Any]) -> dict[str, Any]:
    """Fresh interpreter entry: one entity, one MCP lifetime, one asyncio.run."""
    config = _bind_spawned_config(dict(job["config"]))
    dump = job.get("dump_dir")
    try:
        reply, metadata = _run_isolated(
            run_kg_agent(
                job["prompt"],
                config,
                doi_hash=str(job["doi_hash"]),
                entity_label=str(job["entity_label"]),
                entity_uri=str(job.get("entity_uri") or ""),
                dump_dir=Path(dump) if dump else None,
            )
        )
        return {"ok": True, "reply": reply, "metadata": metadata}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _entity_process_entry(job: dict[str, Any], conn) -> None:
    try:
        conn.send(_execute_kg_entity(job))
    except Exception as exc:
        conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()


def _run_kg_agent_for_entity(
    prompt: str,
    config: dict[str, Any],
    *,
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    dump_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """s1ka is one asyncio.run per entity. Spawn only when MCP stdio would poison the next loop."""
    job = {
        "prompt": prompt,
        "config": _spawnable_config(config),
        "doi_hash": doi_hash,
        "entity_label": entity_label,
        "entity_uri": entity_uri,
        "dump_dir": str(dump_dir),
    }
    flag = str(os.environ.get("TWA_KG_ENTITY_INPROCESS") or "").strip()
    inprocess = flag == "1" or (flag != "0" and sys.version_info[:2] == (3, 11))
    if inprocess:
        result = _execute_kg_entity(job)
    else:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_entity_process_entry, args=(job, child))
        proc.start()
        result = None
        while True:
            if parent.poll(1.0):
                result = parent.recv()
                break
            if not proc.is_alive():
                if parent.poll():
                    result = parent.recv()
                break
        proc.join()
        parent.close()
        if result is None:
            result = {
                "ok": False,
                "error": f"entity process exited {proc.exitcode} with no result",
            }
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "KG entity process failed"))
    return str(result.get("reply") or ""), dict(result.get("metadata") or {})


def run_step(doi_hash: str, config: dict) -> bool:
    assert_kg_revision_locked_off(config)
    data_dir = config.get("data_dir", "data")
    ontology_name = str(config.get("ontology_name") or "").strip()
    doi_folder = Path(data_dir) / doi_hash
    kg_model = str(config.get("kg_model") or "").strip()
    if kg_model:
        print(f">> Main KG Building: {doi_hash} model={kg_model}")
    else:
        print(f">> Main KG Building: {doi_hash}")
    if not (doi_folder / EXTRACTION_DONE_MARKER).is_file():
        print(
            "[WARN] Main KG Building deferred: "
            "main ontology extraction is not complete"
        )
        return False
    entities = load_top_entities_json(doi_hash, data_dir)
    if not entities:
        print("[WARN] No top entities for main KG; continuing")
        return True
    dump_dir = doi_folder / "prompts" / "kg_building"
    published = 0
    skipped = 0
    for entity in entities:
        label = str(entity.get("label") or "")
        uri = str(entity.get("uri") or "")
        safe = entity_artifact_name(label)
        hints = load_entity_hints(doi_folder, safe)
        if not hints.strip():
            print(f"  [WARN] No hints for {label}; skipping KG for this entity")
            skipped += 1
            continue
        dest = entity_ttl_path(
            doi_folder,
            ontology_name=ontology_name,
            entity_label=label,
            config=config,
        )
        if should_reuse_published_ttl(dest, doi_folder=doi_folder, entity_safe=safe):
            print(f"  [SKIP] {dest.name} already published")
            published += 1
            continue
        if dest.is_file():
            print(
                f"  [INFO] Rebuilding {dest.name}: "
                "extraction hints are newer than the published TTL"
            )
        prompt = build_kg_prompt(
            hints=hints,
            doi=doi_hash,
            entity_label=label,
            entity_uri=uri,
            known_top_entities=entities,
            doi_folder=doi_folder,
            entity_safe=safe,
            protocol=str(config.get("experiment_protocol") or "").strip() or None,
            ontology=ontology_from_config(config),
            mcp_tools=kg_mcp_binding(config)[1],
        )
        reply = ""
        metadata: dict[str, Any] = {}
        last_error = ""
        for attempt in range(1, 4):
            try:
                reply, metadata = _run_kg_agent_for_entity(
                    prompt,
                    config,
                    doi_hash=doi_hash,
                    entity_label=label,
                    entity_uri=uri,
                    dump_dir=dump_dir,
                )
                last_error = ""
                break
            except Exception as exc:
                last_error = str(exc)
                print(
                    f"  [WARN] KG agent failed for {label} "
                    f"(attempt {attempt}/3): {exc}"
                )
        if last_error:
            print(f"  [WARN] skipping this entity after retries: {label}")
            skipped += 1
            continue
        write_main_kg_token_trace(
            doi_folder=doi_folder,
            entity_label=label,
            entity_uri=uri,
            metadata=metadata,
        )
        produced = _memory_candidate(doi_folder, label, uri)
        if produced is None:
            print(f"  [WARN] Generated MCP wrote no TTL for {label}; skipping this entity")
            print(f"    tools: {metadata.get('tool_activity', {}).get('executed_tool_name_set')}")
            print(f"    reply preview: {(reply or '')[:160]}")
            skipped += 1
            continue
        publish_entity_ttl(produced, dest)
        if not graph_is_parseable(dest):
            print(f"  [WARN] Published TTL is not parseable: {dest}; skipping this entity")
            skipped += 1
            continue
        published += 1
        print(f"  [OK] Published {dest}")
    marker = doi_folder / ".main_kg_building_done"
    if published <= 0:
        print(f"[WARN] Main KG Building published nothing for {doi_hash}; continuing")
        return True
    if skipped:
        print(
            f"[WARN] Main KG Building partial for {doi_hash}: "
            f"{published} published, {skipped} skipped"
        )
        return True
    marker.write_text("completed\n", encoding="utf-8")
    print(f"[OK] Main KG Building completed: {doi_hash}")
    return True
