"""Resolve pipeline iter3 hint ledgers (same priority as extraction scoring)."""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from src.pipelines.utils.top_entity_identity import entity_artifact_name, entity_scope_name
from top_entities import load_top_entities

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)

DEFAULT_HINT_RUNS = [
    "20260822_eval30_last6-v4-e2e",
    "20260822_eval30_problem2-v4-atm-e2e",
    "20260821_eval30_next12-v4-e2e",
    "20260821_eval30_next6-kg1-e2e",
    "20260820_eval30_pubchem-dedup-e2e",
    "20260820_eval30_presence-e2e-p6",
    "20260819_eval30_ontosyn-kg-queue",
]
HINT_RUNS = list(DEFAULT_HINT_RUNS)


def set_hint_runs(runs: list[str] | None = None) -> list[str]:
    """Replace the hint-run priority list in place so all importers see it."""
    HINT_RUNS[:] = list(runs) if runs else list(DEFAULT_HINT_RUNS)
    return list(HINT_RUNS)


@dataclass(frozen=True)
class HintEntity:
    key: str
    label: str
    path: Path
    run: str
    text: str
    token_budget: int = 0
    budget_detail: dict | None = None
    uri: str = ""
    identity_dossier: dict | None = None

    @property
    def slug(self) -> str:
        digits = "".join(ch for ch in self.key if ch.isdigit()) or "0"
        return f"cs{digits}"

    def iter_layers(self) -> list[tuple[int, Path, str, int | None]]:
        """Yield (layer, path, text, layer_budget) for iter2 → iter3 → iter4."""
        suffix = self.path.name
        for prefix in ("iter3_hints_", "iter2_hints_", "iter4_hints_"):
            if suffix.startswith(prefix):
                suffix = suffix[len(prefix) :]
                break
        by_dir = (self.budget_detail or {}).get("by_dir") or {}
        folder = {
            2: "iter2_kg_building",
            3: "iter3_kg_building",
            4: "iter4_kg_building",
        }
        layers: list[tuple[int, Path, str, int | None]] = []
        for layer in (2, 3, 4):
            path = self.path.parent / f"iter{layer}_hints_{suffix}"
            if not path.exists():
                continue
            budget = int(by_dir.get(folder[layer]) or 0) or None
            layers.append((layer, path, path.read_text(encoding="utf-8"), budget))
        if layers:
            return layers
        return [(3, self.path, self.text, self.token_budget or None)]

    def full_hints(self) -> "FullHints":
        """Combine every available KG-building ledger into one stable input."""
        layers = self.iter_layers()
        sections = []
        for layer, path, text, _budget in layers:
            source = (
                path.name
                if os.environ.get("ONTOSYN_STABLE_HINT_SOURCE", "").strip() == "1"
                else str(path)
            )
            sections.append(
                f"=== ITER{layer} SEMANTIC_HINTS ===\n"
                f"Source: {source}\n"
                f"{text.strip()}"
            )
        return FullHints(
            text="\n\n".join(sections) + "\n",
            layers=tuple(layer for layer, _path, _text, _budget in layers),
            paths=tuple(path for _layer, path, _text, _budget in layers),
        )


@dataclass(frozen=True)
class FullHints:
    """All iteration-owned hint ledgers for one ChemicalSynthesis."""

    text: str
    layers: tuple[int, ...]
    paths: tuple[Path, ...]


def _label_from_hint_name(path: Path) -> str:
    stem = path.name
    for prefix in ("iter3_hints_",):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    if stem.endswith(".txt"):
        stem = stem[: -len(".txt")]
    return stem.replace("_", " ").strip()


def hint_dir(hash_id: str) -> tuple[Path, str] | tuple[None, None]:
    for run in HINT_RUNS:
        path = REPO_ROOT / "scenarios" / "mops" / "runs" / run / "runtime" / hash_id / "mcp_run"
        files = [
            item
            for item in path.glob("iter3_hints_*.txt")
            if ".pre_size_dedup" not in item.name
        ]
        if files:
            return path, run
    return None, None


def load_hint_entities(hash_id: str) -> list[HintEntity]:
    directory, run = hint_dir(hash_id)
    if directory is None or run is None:
        raise FileNotFoundError(f"No iter3_hints_*.txt for {hash_id} in HINT_RUNS")
    files = {
        item.name: item
        for item in directory.glob("iter3_hints_*.txt")
        if ".pre_size_dedup" not in item.name
    }
    if not files:
        raise FileNotFoundError(f"Empty hint inventory for {hash_id} in {directory}")
    try:
        tops = load_top_entities(directory.parent)
    except (FileNotFoundError, ValueError):
        tops = []
    entities: list[HintEntity] = []
    used: set[str] = set()
    for top in tops:
        artifact = entity_artifact_name(top.label)
        filename = f"iter3_hints_{artifact}.txt"
        path = files.get(filename)
        if path is None:
            continue
        used.add(filename)
        entities.append(
            HintEntity(
                key=top.key,
                label=top.label,
                path=path,
                run=run,
                text=path.read_text(encoding="utf-8"),
            )
        )
    leftovers = [path for name, path in sorted(files.items()) if name not in used]
    for index, path in enumerate(leftovers, start=len(entities) + 1):
        entities.append(
            HintEntity(
                key=f"ChemicalSynthesis-{index}",
                label=_label_from_hint_name(path),
                path=path,
                run=run,
                text=path.read_text(encoding="utf-8"),
            )
        )
    force_budget = os.environ.get("ONTOSYN_FORCE_BUDGET_RUN", "").strip()
    if force_budget:
        return _apply_forced_budget_run(hash_id, entities, force_budget)
    return [_with_kg_budget(hash_id, entity) for entity in entities]


def _apply_forced_budget_run(
    hash_id: str,
    entities: list[HintEntity],
    budget_run: str,
) -> list[HintEntity]:
    """Pin KG token budget to one pipeline run.

    Exact label match is used when every hint entity exists in that run.
    Otherwise the paper-level 0827-style envelope is split equally, because
    later extractions often rename or split the same syntheses.
    """
    from kg_token_budget import entity_kg_building_budget, paper_kg_building_budget

    paper = paper_kg_building_budget(hash_id, run=budget_run)
    total = int(paper["total_tokens"])
    exact: list[HintEntity] = []
    try:
        for entity in entities:
            detail = dict(entity_kg_building_budget(hash_id, entity.label, run=budget_run))
            detail["allocation"] = "label_match"
            exact.append(_entity_with_budget(entity, detail))
        if sum(item.token_budget for item in exact) == total:
            return exact
    except FileNotFoundError:
        pass
    n = len(entities)
    if n <= 0:
        raise FileNotFoundError(f"No hint entities to receive forced budget for {hash_id}")
    shares = [total // n] * n
    shares[-1] += total - sum(shares)
    assigned: list[HintEntity] = []
    for entity, share in zip(entities, shares):
        detail = {
            "hash": hash_id,
            "label": entity.label,
            "run": budget_run,
            "scope": None,
            "allocation": "paper_split",
            "paper_total_tokens": total,
            "n_hint_entities": n,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": share,
            "llm_calls": 0,
            "trace_files": [],
            "by_dir": {},
        }
        assigned.append(_entity_with_budget(entity, detail))
    return assigned


def _entity_with_budget(entity: HintEntity, detail: dict) -> HintEntity:
    return HintEntity(
        key=entity.key,
        label=entity.label,
        path=entity.path,
        run=entity.run,
        text=entity.text,
        token_budget=int(detail["total_tokens"]),
        budget_detail=detail,
        uri=entity.uri,
        identity_dossier=entity.identity_dossier,
    )


def _with_kg_budget(hash_id: str, entity: HintEntity) -> HintEntity:
    from kg_token_budget import HINT_RUNS as BUDGET_RUNS
    from kg_token_budget import entity_kg_building_budget

    errors: list[str] = []
    for run in dict.fromkeys((entity.run, *BUDGET_RUNS)):
        try:
            detail = entity_kg_building_budget(hash_id, entity.label, run=run)
            break
        except FileNotFoundError as exc:
            errors.append(str(exc))
    else:
        raise FileNotFoundError(
            f"No KG-building token budget for {hash_id} / {entity.label!r} "
            f"across configured runs: {'; '.join(errors)}"
        )
    return HintEntity(
        key=entity.key,
        label=entity.label,
        path=entity.path,
        run=entity.run,
        text=entity.text,
        token_budget=int(detail["total_tokens"]),
        budget_detail=detail,
        uri=entity.uri,
        identity_dossier=entity.identity_dossier,
    )


def distill_extension_hint(prompt_text: str, domain: str = "ontospecies") -> str:
    """Keep the ref-entity-relations JSON; drop MCP tool choreography."""
    ledger = ""
    for match in _JSON_FENCE.finditer(prompt_text):
        body = match.group(1)
        if '"entities"' in body and '"class"' in body:
            ledger = body.strip()
            break
    if not ledger:
        return prompt_text.strip()
    title = (
        "ONTO SPECIES EXTRACTION LEDGER"
        if domain == "ontospecies"
        else "ONTO MOPS EXTRACTION LEDGER"
    )
    return (
        f"{title} (ref-entity-relations.v1).\n"
        "Materialize only these entities and relations. Do not invent extras.\n\n"
        f"{ledger}\n"
    )


def distill_ontospecies_hint(prompt_text: str) -> str:
    return distill_extension_hint(prompt_text, "ontospecies")


def _full_safe_label(label: str) -> str:
    """Label artifact without the 64-char hash cap, so NDBDC/ADBDC/DCPP stay distinct."""
    normalized = unicodedata.normalize("NFKC", str(label or "entity"))
    for character in [":", "：", "﹕", "∶", "꞉", "︰", "\uf03a"]:
        normalized = normalized.replace(character, ":")
    normalized = (
        normalized.replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
        .replace("Α", "Alpha")
        .replace("Β", "Beta")
        .replace("Γ", "Gamma")
        .replace("Δ", "Delta")
    )
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    return re.sub(r"_+", "_", safe).strip("_") or "entity"


def _hint_prefix_score(stem: str, label: str) -> int:
    """Score a prompt filename against the full untruncated label stem."""
    target = _full_safe_label(label)
    candidate = stem.split("--")[0]
    if not candidate or not (
        target.startswith(candidate) or candidate.startswith(target[: len(candidate)])
    ):
        return -1
    score = 0
    for left, right in zip(candidate, target):
        if left != right:
            break
        score += 1
    return score if score >= 8 else -1


def extension_hint_path(
    hash_id: str,
    label: str,
    domain: str = "ontospecies",
    uri: str = "",
) -> Path | None:
    directory, _run = hint_dir(hash_id)
    if directory is None:
        return None
    folder = directory.parent / "prompts" / f"{domain}_kg_building"
    if not folder.is_dir():
        return None
    artifact = entity_artifact_name(label)
    exact = folder / f"{artifact}.md"
    if exact.exists():
        return exact
    if uri:
        scoped = folder / f"{entity_scope_name(label, uri)}.md"
        if scoped.exists():
            return scoped
    ranked: list[tuple[int, int, Path]] = []
    for path in folder.glob("*.md"):
        score = _hint_prefix_score(path.stem, label)
        if score < 0:
            continue
        ranked.append((score, len(path.stem.split("--")[0]), path))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, best_len, best_path = ranked[0]
    ties = [item for item in ranked if item[0] == best_score and item[1] == best_len]
    return best_path if len(ties) == 1 else None


def load_extension_ledger(
    hash_id: str,
    label: str,
    domain: str = "ontospecies",
    uri: str = "",
) -> tuple[Path | None, str]:
    path = extension_hint_path(hash_id, label, domain, uri=uri)
    if path is None:
        return None, ""
    raw = path.read_text(encoding="utf-8")
    return path, distill_extension_hint(raw, domain)


def bound_chemical_outputs(graph) -> list[dict[str, str]]:
    """Existing ChemicalSynthesis → ChemicalOutput edges to reuse as Species."""
    if graph is None:
        return []
    by_id = {node.id: node for node in graph.nodes}
    rows = []
    for rel in graph.relationships:
        if rel.type != "ontosyn:hasChemicalOutput":
            continue
        target = by_id.get(rel.target.id)
        source = by_id.get(rel.source.id)
        if target is None or source is None:
            continue
        rows.append(
            {
                "synthesis_id": source.id,
                "output_id": target.id,
                "label": str((target.properties or {}).get("rdfs:label") or target.id),
            }
        )
    return rows
