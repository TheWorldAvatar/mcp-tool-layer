"""Mechanically replay audited initial-generation patches into a checkpoint."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from src.agents.scripts_and_prompts_generation.unified_diff_editor import (
    apply_llm_unified_diff,
)
from src.agents.scripts_and_prompts_generation.exact_edit_editor import (
    apply_exact_edit_payload,
)


def _generation_patch_records(
    summary: dict[str, Any], *, include_package_synthesis: bool = True
) -> list[dict[str, Any]]:
    reports = summary.get("reports") or []
    if len(reports) != 1:
        raise ValueError("Checkpoint replay requires exactly one ontology report")
    llm_run = (reports[0] or {}).get("llm_agent_run") or {}
    records: list[dict[str, Any]] = []
    for item in llm_run.get("history") or []:
        if item.get("mode") == "per_file_initial_generation":
            for record in item.get("files") or []:
                records.append({"kind": "initial", **record})
                stage_repair = record.get("stage_repair") or {}
                stage_patch = stage_repair.get("patch") or {}
                if stage_repair.get("accepted") and stage_patch.get("ok"):
                    records.append(
                        {
                            "kind": "integration",
                            "target": record.get("target"),
                            "patch": stage_patch,
                        }
                    )
        elif include_package_synthesis and item.get("mode") == "package_synthesis":
            patch_report = item.get("patch") or {}
            if patch_report.get("ok"):
                records.append({"kind": "package", "patch": patch_report})
        elif item.get("mode") == "focused_package_integration":
            patch_report = item.get("patch") or {}
            if item.get("accepted") and patch_report.get("ok"):
                records.append({"kind": "integration", "patch": patch_report})
    if not records:
        raise ValueError("Summary has no audited generation patch records")
    return records


def replay_generation_checkpoint(
    *,
    summary_path: Path,
    output_root: Path,
    include_package_synthesis: bool = True,
) -> dict[str, Any]:
    """Replay only accepted initial diffs; no LLM or semantic fallback is used."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = _generation_patch_records(
        summary, include_package_synthesis=include_package_synthesis
    )
    applied: list[dict[str, Any]] = []
    root = output_root.resolve()
    for record in records:
        patch_report = record.get("patch") or {}
        patch = patch_report.get("patch_unified_diff")
        replay_protocol = patch_report.get("replay_protocol")
        changed = patch_report.get("changed_files") or []
        kind = record.get("kind")
        if (
            not patch_report.get("ok")
            or (
                replay_protocol not in {"exact-edits.v1"}
                and not isinstance(patch, str)
            )
            or not changed
            or (kind == "initial" and len(changed) != 1)
        ):
            raise ValueError(f"Generation patch record is not replayable: {record.get('target')}")
        targets: list[Path] = []
        for raw_path in changed:
            target = (root / str(raw_path)).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"Checkpoint target escapes output root: {raw_path}")
            if kind == "initial":
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text("", encoding="utf-8")
                if not target.is_file() or target.stat().st_size:
                    raise ValueError(f"Checkpoint target must be an empty file: {target}")
            elif not target.is_file():
                raise FileNotFoundError(
                    f"Integration target does not exist after initial replay: {target}"
                )
            targets.append(target)
        if replay_protocol == "exact-edits.v1":
            payload = patch_report.get("edit_payload")
            if not isinstance(payload, dict):
                raise ValueError("Exact checkpoint record has no canonical edit payload")
            declared_files = {
                str(item.get("path") or ""): item
                for item in (patch_report.get("files") or [])
                if isinstance(item, dict)
            }
            if set(declared_files) != set(map(str, changed)):
                raise ValueError("Exact checkpoint file inventory mismatch")
            for relative, item in declared_files.items():
                current_hash = _sha256(root / relative)
                if current_hash != str(item.get("before_sha256") or ""):
                    raise ValueError(f"Exact checkpoint before hash mismatch: {relative}")
            report = apply_exact_edit_payload(
                output_root=root,
                targets=targets,
                edit_payload=payload,
            )
            if report.get("ok"):
                for relative, item in declared_files.items():
                    if _sha256(root / relative) != str(item.get("after_sha256") or ""):
                        raise ValueError(
                            f"Exact checkpoint after hash mismatch: {relative}"
                        )
        elif replay_protocol in {None, "unified-diff.v1"}:
            replay_patch = "\n".join(
                line
                for line in patch.splitlines()
                if line.strip()
                not in {"*** Begin Patch", "*** End Patch", "*** End Patch?"}
            )
            report = apply_llm_unified_diff(
                output_root=root,
                targets=targets,
                patch_unified_diff=replay_patch,
            )
        else:
            raise ValueError(f"Unknown checkpoint replay protocol: {replay_protocol}")
        if not report.get("ok"):
            raise RuntimeError(
                f"Checkpoint patch replay failed for {changed}: {report.get('failures')}"
            )
        applied.append({"kind": kind, "targets": list(changed), "report": report})
    return {
        "ok": True,
        "source_summary": str(summary_path),
        "output_root": str(output_root),
        "applied": applied,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_artifacts(root: Path, ontology_name: str) -> dict[str, Path]:
    script_dir = root / "scripts" / ontology_name
    prompt_dir = root / "prompts" / ontology_name
    files = [
        path
        for path in sorted(script_dir.glob("*.py"))
        if path.name not in {"__init__.py", "_fixed_om2_runtime.py"}
        and not path.name.startswith("main_part_")
        and "_attempt_" not in path.name
    ]
    files.extend(sorted(prompt_dir.glob("*.md")))
    return {path.relative_to(root).as_posix(): path for path in files}


def copy_generation_checkpoint(
    *,
    checkpoint_root: Path,
    output_root: Path,
    ontology_name: str,
    expected_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """Copy an immutable generation checkpoint into a fresh working directory."""
    source = checkpoint_root.resolve()
    destination = output_root.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("checkpoint source and destination must be separate trees")
    if not source.is_dir():
        raise FileNotFoundError(f"checkpoint directory does not exist: {source}")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("checkpoint destination must not exist or must be empty")

    artifacts = _checkpoint_artifacts(source, ontology_name)
    expected = set(expected_artifacts or artifacts)
    if not expected or set(artifacts) != expected:
        raise ValueError(
            "checkpoint artifact inventory mismatch: "
            f"missing={sorted(expected - set(artifacts))}, "
            f"extra={sorted(set(artifacts) - expected)}"
        )
    for relative, path in artifacts.items():
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"invalid checkpoint artifact: {relative}")

    source_hashes = {relative: _sha256(path) for relative, path in artifacts.items()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.checkpoint-", dir=destination.parent)
    )
    try:
        for relative, path in artifacts.items():
            copied = temp_root / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copied)
        copied_hashes = {
            relative: _sha256(temp_root / relative) for relative in artifacts
        }
        if copied_hashes != source_hashes:
            raise RuntimeError("checkpoint copy hash verification failed")
        manifest = {
            "schema_version": 1,
            "kind": "generation_checkpoint_import",
            "ontology": ontology_name,
            "source_root": str(source),
            "artifact_inventory": sorted(artifacts),
            "artifact_hashes": source_hashes,
        }
        reports = temp_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "checkpoint_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if destination.exists():
            destination.rmdir()
        os.replace(temp_root, destination)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    return {
        "ok": True,
        "source_root": str(source),
        "output_root": str(destination),
        **manifest,
    }
