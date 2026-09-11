"""Shorten conversion markdown by deleting T-Box-irrelevant blocks.

This is the only official slim path. After PDF→MD, one LLM call copies
kept text verbatim and drops chrome. The result is `{hash}_slim.md`.
Extraction and KG load that file as the paper body and do not append SI
again.

The call uses the domain `models.runtime_extraction` model — the same name
as extraction, not the KG model. T-Boxes are `tbox.primary` plus
`tbox.supporting`, except unit vocabularies such as OM2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_runtime.domain_binding import (
    RuntimeDomain,
    runtime_domain_from_config,
)
from src.extraction_runtime.llm import invoke_text, strip_code_fences
from src.extraction_runtime.models_map import get_extraction_model
from models.locked_llm import LOCKED_EXTRACTION_MODEL, canonicalize_chat_model

SLIM_NAME = "{hash}_slim.md"
META_NAME = "{hash}_slim.meta.json"
UNIT_VOCAB_FILENAMES = frozenset({"om2.ttl"})

# 0827 chemistry slim assembled exactly these four files, in this order.
CHEMISTRY_0827_SOURCE_GROUPS = (
    ("main manuscript", ("{h}.md",)),
    ("supporting information", ("{h}_si.md",)),
    ("main tables", ("{h}_tables.md",)),
    ("SI tables", ("{h}_si_tables.md",)),
)

# Medical conversion writes vision markdown as the authoritative body.
MEDICAL_SOURCE_GROUPS = (
    ("main manuscript", ("{h}_vision.md", "{h}.md", "{h}_text.md")),
    ("supporting information", ("{h}_si.md", "{h}_si_text.md", "{h}_si_vision.md")),
    ("main tables", ("{h}_tables.md",)),
    ("SI tables", ("{h}_si_tables.md",)),
)

_SOURCE_GROUPS = CHEMISTRY_0827_SOURCE_GROUPS

PROMPT_BODY = """You receive a scientific paper in markdown and {tbox_count} T-Box ontolog{tbox_noun}.

Task: shorten the paper by DELETING blocks only.

Rules:
- Copy kept text VERBATIM. Do not rewrite, paraphrase, translate, normalize units, merge sentences, or fix OCR.
- Do not add headings, commentary, ellipses, or summaries that were not in the source.
- Keep a block if it could instantiate or fill any class or property in ANY of the T-Boxes.
- A block that is relevant to one T-Box must be kept even if another T-Box excludes it.
- Delete blocks that cannot support any T-Box fact (journal chrome, received/accepted dates, keywords-only lines, references, acknowledgements, author addresses, unrelated application discussion, crystal-refinement tables, plot-only dumps).
- Prefer deleting whole paragraphs or sections. Do not delete words inside a kept sentence.
- Keep the original markup of every kept block: headings, lists, markdown pipe tables, and line-broken key-value dumps.
- Do not restyle a kept block (do not turn a pipe table into a list, or a list into a table).
- If the source has more than one layout of the same table or block, treat those layouts as one block: keep all of them or delete all of them. If keeping, do not drop the markdown pipe-table layout.
- If unsure, keep the block.

Output ONLY the shortened markdown. No preface and no closing remark.
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def source_groups_for_domain(
    domain: RuntimeDomain | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if domain is not None and domain.vision_required:
        return MEDICAL_SOURCE_GROUPS
    return CHEMISTRY_0827_SOURCE_GROUPS


def assemble_conversion_source(
    doi_hash: str,
    doi_dir: Path,
    *,
    groups: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Concatenate conversion markdown the same way the 0827 slim batch did."""
    parts: list[dict[str, Any]] = []
    blob = ""
    for label, names in groups or CHEMISTRY_0827_SOURCE_GROUPS:
        chosen: Path | None = None
        text = ""
        for name in names:
            path = doi_dir / name.format(h=doi_hash)
            candidate = _read(path).strip()
            if not candidate:
                continue
            chosen = path
            text = candidate
            break
        if chosen is None:
            parts.append({"label": label, "path": "", "bytes": 0, "used": False})
            continue
        size = chosen.stat().st_size
        if text in blob:
            parts.append(
                {
                    "label": label,
                    "path": str(chosen),
                    "bytes": size,
                    "used": False,
                    "skipped": "already inside earlier file",
                }
            )
            continue
        blob += f"\n\n===== SOURCE: {label} =====\n{text}\n"
        parts.append(
            {
                "label": label,
                "path": str(chosen),
                "bytes": size,
                "used": True,
            }
        )
    return blob.lstrip() + ("\n" if blob.strip() else ""), parts


def slim_tboxes(domain: RuntimeDomain) -> list[tuple[str, Path]]:
    """Primary + supporting T-Boxes, omitting unit vocabularies."""
    boxes: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for path in (domain.config.primary_tbox, *domain.config.supporting_tboxes):
        resolved = path.resolve()
        if resolved in seen or path.name.lower() in UNIT_VOCAB_FILENAMES:
            continue
        if not path.is_file():
            continue
        seen.add(resolved)
        boxes.append((path.stem, path))
    return boxes


def runtime_extraction_model(config: dict[str, Any]) -> str:
    explicit = str(config.get("extraction_model") or "").strip()
    if explicit:
        return canonicalize_chat_model(explicit)
    domain = runtime_domain_from_config(config)
    if domain is not None:
        named = str(domain.config.models.get("runtime_extraction") or "").strip()
        if named:
            return named
    return get_extraction_model("iter1_hints", default=LOCKED_EXTRACTION_MODEL)


def build_slim_prompt(source: str, tboxes: list[tuple[str, Path]]) -> str:
    count = len(tboxes)
    instruction = PROMPT_BODY.format(
        tbox_count=count,
        tbox_noun="y" if count == 1 else "ies",
    )
    chunks = [
        f"===== T-BOX: {name} =====\n{_read(path).rstrip()}\n" for name, path in tboxes
    ]
    return (
        instruction
        + "\n\n"
        + "\n".join(chunks)
        + "\n\n===== PAPER MARKDOWN =====\n"
        + source
    )


def _source_paths(parts: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for item in parts:
        raw = str(item.get("path") or "").strip()
        if item.get("used") and raw:
            paths.append(Path(raw))
    return paths


def slim_is_current(slim_path: Path, source_paths: list[Path]) -> bool:
    if not _usable(slim_path):
        return False
    slim_mtime = slim_path.stat().st_mtime
    return all(path.stat().st_mtime <= slim_mtime for path in source_paths)


def run_step(doi_hash: str, config: dict[str, Any]) -> bool:
    data_dir = str(config.get("data_dir") or "data")
    doi_dir = Path(data_dir) / doi_hash
    print(f">> T-Box slim: {doi_hash}")
    domain = runtime_domain_from_config(config)
    if domain is None:
        print("[WARN] Domain binding is missing; continuing without slim")
        return True

    source, parts = assemble_conversion_source(
        doi_hash,
        doi_dir,
        groups=source_groups_for_domain(domain),
    )
    if not source.strip():
        print("[WARN] No conversion markdown to slim; continuing")
        return True

    tboxes = slim_tboxes(domain)
    if not tboxes:
        print("[WARN] No T-Boxes available for slim; continuing")
        return True

    slim_path = doi_dir / SLIM_NAME.format(hash=doi_hash)
    meta_path = doi_dir / META_NAME.format(hash=doi_hash)
    source_paths = _source_paths(parts)
    if slim_is_current(slim_path, source_paths):
        print(f"  [SKIP] {slim_path.name} already exists (up-to-date)")
        return True

    model = runtime_extraction_model(config)
    prompt = build_slim_prompt(source, tboxes)
    try:
        raw = invoke_text(prompt, model_name=model)
    except Exception as exc:
        print(f"[WARN] T-Box slim LLM call failed: {exc}; continuing")
        return True
    slim = strip_code_fences(raw)
    if not slim:
        print("[WARN] T-Box slim returned empty text; keeping unslimmed paper")
        return True

    slim_path.write_text(slim + "\n", encoding="utf-8")
    keep_ratio = len((slim + "\n").encode("utf-8")) / max(len(source.encode("utf-8")), 1)
    meta = {
        "schema_version": "tbox_slim.v1",
        "hash": doi_hash,
        "model": model,
        "tboxes": [
            {"name": name, "path": str(path), "bytes": path.stat().st_size}
            for name, path in tboxes
        ],
        "source": parts,
        "source_bytes": len(source.encode("utf-8")),
        "slim_bytes": slim_path.stat().st_size,
        "keep_ratio_vs_full": round(keep_ratio, 4),
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"  [OK] Wrote {slim_path.name} "
        f"(keep {meta['keep_ratio_vs_full']:.0%}, model {model})"
    )
    return True
