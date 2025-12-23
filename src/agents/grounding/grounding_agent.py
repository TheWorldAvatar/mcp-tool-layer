"""
Grounding agent: map IRIs in a TTL file to canonical OntoSpecies IRIs via the OntoSpecies MCP lookup server.

This implementation is intentionally minimal and deterministic:
- Hardcodes the example TTL: `evaluation/data/merged_tll/0e299eb4/0e299eb4.ttl`
- Uses the OntoSpecies MCP stdio server config at: `configs/grounding.json`
- Produces a stable JSON mapping (sorted keys + deterministic tie-breaking)

The core output is a JSON object mapping *source TTL IRIs* -> *OntoSpecies IRIs* (or null if not found),
plus a detailed list explaining how each mapping was chosen.
"""

from __future__ import annotations

import asyncio
import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from rdflib import Graph, Namespace, RDF, RDFS, URIRef
from rdflib.term import BNode, Literal
from rdflib.namespace import OWL

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXAMPLE_TTL_PATH = Path("evaluation/data/merged_tll/0e299eb4/0e299eb4.ttl")
MCP_CONFIG_PATH = Path("configs/grounding.json")
MCP_SERVER_KEY = "ontospecies"

# Namespaces used by the example TTLs
ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOSPECIES = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")

@dataclass(frozen=True)
class Candidate:
    source_iri: str
    labels: Tuple[str, ...]  # ordered candidates (exact -> alt)


def _stable_unique(seq: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for s in seq:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def _lit_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


def _extract_labels(g: Graph, subj: URIRef) -> Tuple[str, ...]:
    """
    Deterministically produce a list of candidate labels for grounding:
    - rdfs:label first
    - then any ontosyn:hasAlternativeNames literals (sorted for stability)
    """
    labels: List[str] = []
    primary = _lit_str(g.value(subj, RDFS.label))
    if primary:
        labels.append(primary)

    # OntoSyn alternative names appear as repeated string literals.
    alt_pred = ONTOSYN.hasAlternativeNames
    alts = sorted({str(o).strip() for o in g.objects(subj, alt_pred) if str(o).strip()})
    labels.extend(alts)

    return tuple(_stable_unique(labels))


def extract_candidates_from_ttl(ttl_path: Path) -> List[Candidate]:
    g = Graph()
    g.parse(str(ttl_path), format="turtle")
    return extract_candidates_from_graph(g)


def extract_candidates_from_graph(g: Graph) -> List[Candidate]:
    """
    Generic candidate extraction:
    - Any URIRef subject with rdfs:label OR OntoSyn hasAlternativeNames
    - No ontology-specific rdf:type assumptions.
    """
    candidates: List[Candidate] = []

    labeled_subjects: set[URIRef] = set()
    for s in g.subjects(RDFS.label, None):
        if isinstance(s, URIRef):
            labeled_subjects.add(s)
    for s in g.subjects(ONTOSYN.hasAlternativeNames, None):
        if isinstance(s, URIRef):
            labeled_subjects.add(s)

    for subj in sorted(labeled_subjects, key=lambda x: str(x)):
        labels = _extract_labels(g, subj)
        if labels:
            candidates.append(Candidate(source_iri=str(subj), labels=labels))

    candidates.sort(key=lambda c: c.source_iri)
    return candidates


def _pick_best_iri(iris: Sequence[str]) -> Optional[str]:
    if not iris:
        return None
    # Deterministic: pick lexicographically smallest IRI.
    return sorted(set(map(str, iris)))[0]


def _pick_best_fuzzy(rows: Sequence[Dict[str, Any]]) -> Optional[str]:
    """
    rows: list of dict(label, iri, score) (from OntoSpecies MCP server)
    Deterministic: highest score, then lexicographically smallest iri.
    """
    best: Tuple[float, str] | None = None
    for r in rows:
        iri = str(r.get("iri") or "").strip()
        if not iri:
            continue
        try:
            score = float(r.get("score", 0.0))
        except Exception:
            score = 0.0
        key = (score, iri)
        if best is None or key[0] > best[0] or (key[0] == best[0] and key[1] < best[1]):
            best = key
    return best[1] if best else None


def _pick_best_fuzzy_with_score(rows: Sequence[Dict[str, Any]]) -> Optional[Tuple[str, float]]:
    """
    rows: list of dict(label, iri, score)
    Returns: (best_iri, best_score) with deterministic tie-breaking.
    """
    best: Tuple[float, str] | None = None
    for r in rows:
        iri = str(r.get("iri") or "").strip()
        if not iri:
            continue
        try:
            score = float(r.get("score", 0.0))
        except Exception:
            score = 0.0
        key = (score, iri)
        if best is None or key[0] > best[0] or (key[0] == best[0] and key[1] < best[1]):
            best = key
    if not best:
        return None
    return (best[1], float(best[0]))


class OntoSpeciesLookupClient:
    """
    Thin MCP stdio client for OntoSpecies lookup tools.
    Reads `configs/grounding.json` by default and spawns the configured stdio server.
    """

    def __init__(self, *, mcp_config_path: Path = MCP_CONFIG_PATH, server_key: str = MCP_SERVER_KEY):
        cfg = json.loads(Path(mcp_config_path).read_text(encoding="utf-8"))
        if server_key not in cfg:
            raise KeyError(f"Missing MCP server key '{server_key}' in {mcp_config_path}")
        self._server_cfg = cfg[server_key]

    async def __aenter__(self) -> "OntoSpeciesLookupClient":
        params = StdioServerParameters(
            command=self._server_cfg["command"],
            args=self._server_cfg.get("args", []),
        )
        self._stdio_ctx = stdio_client(params)
        self._read, self._write = await self._stdio_ctx.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Best-effort cleanup.
        try:
            if hasattr(self, "_session"):
                await self._session.__aexit__(exc_type, exc, tb)
        finally:
            if hasattr(self, "_stdio_ctx"):
                await self._stdio_ctx.__aexit__(exc_type, exc, tb)

    async def _call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        res = await self._session.call_tool(name, args)
        if getattr(res, "isError", False):
            raise RuntimeError(f"MCP tool '{name}' errored: {res}")
        structured = getattr(res, "structuredContent", None)
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        # Fallback to parsing the first text blob.
        content = getattr(res, "content", None) or []
        if content and hasattr(content[0], "text"):
            txt = content[0].text
            try:
                return json.loads(txt)
            except Exception:
                return txt
        return None

    async def execute_sparql(self, query: str, *, timeout: int = 30) -> Dict[str, Any]:
        # Server wrapper returns raw SPARQL JSON.
        return dict(await self._call_tool("execute_sparql", {"query": query, "timeout": int(timeout)}) or {})

    async def fuzzy_lookup_classes(self) -> List[str]:
        res = await self._call_tool("fuzzy_lookup_classes", {})
        if isinstance(res, list):
            return [str(x) for x in res]
        return []

    async def fuzzy_lookup(self, class_local_name: str, query: str, *, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
        tool = f"fuzzy_lookup_{class_local_name}"
        return list(await self._call_tool(tool, {"query": query, "limit": limit, "cutoff": cutoff}) or [])


async def ground_example_ttl_to_ontospecies(
    *,
    ttl_path: Path = EXAMPLE_TTL_PATH,
    mcp_config_path: Path = MCP_CONFIG_PATH,
    server_key: str = MCP_SERVER_KEY,
    fuzzy_cutoff: float = 0.6,
    enable_fuzzy: bool = True,
) -> Dict[str, Any]:
    """
    Ground the hardcoded example TTL and return a deterministic JSON-serializable dict.
    """
    ttl_path = Path(ttl_path)
    if not ttl_path.exists():
        raise FileNotFoundError(f"TTL not found: {ttl_path}")

    async with OntoSpeciesLookupClient(mcp_config_path=mcp_config_path, server_key=server_key) as client:
        return await ground_ttl_with_client(
            ttl_path=ttl_path,
            client=client,
            mcp_config_path=mcp_config_path,
            server_key=server_key,
            fuzzy_cutoff=fuzzy_cutoff,
            enable_fuzzy=enable_fuzzy,
        )

async def ground_ttl_with_client(
    *,
    ttl_path: Path,
    client: OntoSpeciesLookupClient,
    mcp_config_path: Path = MCP_CONFIG_PATH,
    server_key: str = MCP_SERVER_KEY,
    fuzzy_cutoff: float = 0.6,
    enable_fuzzy: bool = True,
) -> Dict[str, Any]:
    """
    Ground a TTL using an *already-open* OntoSpeciesLookupClient session.
    This is used for batch mode so we don't spawn a new stdio MCP server per TTL.
    """
    ttl_path = Path(ttl_path)
    g = Graph()
    g.parse(str(ttl_path), format="turtle")
    return await ground_graph_with_client(
        graph=g,
        ttl_path=ttl_path,
        client=client,
        mcp_config_path=mcp_config_path,
        server_key=server_key,
        fuzzy_cutoff=fuzzy_cutoff,
        enable_fuzzy=enable_fuzzy,
    )


async def ground_graph_with_client(
    *,
    graph: Graph,
    ttl_path: Path,
    client: OntoSpeciesLookupClient,
    mcp_config_path: Path = MCP_CONFIG_PATH,
    server_key: str = MCP_SERVER_KEY,
    fuzzy_cutoff: float = 0.6,
    enable_fuzzy: bool = True,
) -> Dict[str, Any]:
    """
    Ground an in-memory graph using an *already-open* OntoSpeciesLookupClient session.
    Used in batch mode after internal-merge canonicalization to avoid re-reading files.
    """
    ttl_path = Path(ttl_path)
    candidates = extract_candidates_from_graph(graph)

    details: List[Dict[str, Any]] = []
    mapping: Dict[str, Optional[str]] = {}

    classes = await client.fuzzy_lookup_classes()
    classes = sorted(set(classes))  # stable

    for c in candidates:
        chosen: Optional[str] = None
        chosen_class: Optional[str] = None
        chosen_score: Optional[float] = None
        method = "unresolved"
        tried: List[Dict[str, Any]] = []

        # Always fuzzy (this is the intended design).
        if enable_fuzzy and classes and c.labels:
            # Deterministic: use labels in priority order; within each label evaluate all classes.
            best_overall: Optional[Tuple[float, str, str]] = None  # (score, iri, class)
            for label in c.labels:
                per_label_rows: Dict[str, List[Dict[str, Any]]] = {}
                for cls in classes:
                    rows = await client.fuzzy_lookup(cls, label, limit=10, cutoff=fuzzy_cutoff)
                    per_label_rows[cls] = rows
                    best = _pick_best_fuzzy_with_score(rows)
                    if best:
                        iri, score = best
                        key = (score, iri, cls)
                        if best_overall is None or key[0] > best_overall[0] or (
                            key[0] == best_overall[0] and (key[1], key[2]) < (best_overall[1], best_overall[2])
                        ):
                            best_overall = key
                tried.append({"label": label, "fuzzy_rows_by_class": per_label_rows})
                if best_overall:
                    # If we got a hit for this label, accept it (labels are in priority order).
                    break

            if best_overall:
                chosen_score, chosen, chosen_class = best_overall
                method = "fuzzy"

        mapping[c.source_iri] = chosen
        details.append(
            {
                "source_iri": c.source_iri,
                "labels": list(c.labels),
                "grounded_iri": chosen,
                "grounded_class": chosen_class,
                "grounded_score": chosen_score,
                "method": method,
                "tried": tried,
            }
        )

    # Stable output
    mapping = {k: mapping[k] for k in sorted(mapping.keys())}
    details.sort(key=lambda d: d["source_iri"])

    return {
        "ttl_path": str(ttl_path.as_posix()),
        "mcp_config": str(Path(mcp_config_path).as_posix()),
        "server_key": server_key,
        "mapping": mapping,
        "details": details,
    }


def iter_ttl_files(batch_dir: Path) -> Iterator[Path]:
    """
    Recursively yield TTL files under batch_dir, skipping already-grounded outputs.
    """
    batch_dir = Path(batch_dir)
    for p in sorted(batch_dir.rglob("*.ttl")):
        if p.name.endswith("_grounded.ttl"):
            continue
        # Ignore link.ttl / *_link.ttl files
        name = p.name.lower()
        if name == "link.ttl" or name.endswith("_link.ttl") or name.endswith("link.ttl"):
            continue
        yield p


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _default_grounded_ttl_path(ttl_path: Path) -> Path:
    ttl_path = Path(ttl_path)
    return ttl_path.with_name(f"{ttl_path.stem}_grounded{ttl_path.suffix}")


def materialize_grounding_into_ttl(
    *,
    ttl_path: Path,
    mapping: Dict[str, Optional[str]],
    out_ttl_path: Optional[Path] = None,
    mode: str = "replace",
) -> Path:
    """
    Apply a grounding mapping to a copy of a TTL file.

    Modes:
    - sameas (default): keep the original IRIs but add `<old> owl:sameAs <new>` triples for grounded nodes
    - replace: replace occurrences of mapped IRIs in all triples (subject/predicate/object) with the grounded IRI
    """
    ttl_path = Path(ttl_path)
    if out_ttl_path is None:
        out_ttl_path = _default_grounded_ttl_path(ttl_path)
    out_ttl_path = Path(out_ttl_path)

    mode = (mode or "").strip().lower()
    if mode not in {"sameas", "replace"}:
        raise ValueError(f"Unknown mode '{mode}'. Expected 'sameas' or 'replace'.")

    g = Graph()
    g.parse(str(ttl_path), format="turtle")

    if mode == "sameas":
        for old, new in mapping.items():
            if not new:
                continue
            g.add((URIRef(old), OWL.sameAs, URIRef(new)))
    else:
        # Replace across all triples. Note: rdflib doesn't allow in-place term mutation.
        g2 = Graph()
        g2.namespace_manager = g.namespace_manager

        def _map_term(t: Any) -> Any:
            if isinstance(t, URIRef):
                m = mapping.get(str(t))
                if m:
                    return URIRef(m)
            return t

        for s, p, o in g.triples((None, None, None)):
            g2.add((_map_term(s), _map_term(p), _map_term(o)))
        g = g2

    out_ttl_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out_ttl_path), format="turtle")
    return out_ttl_path


def materialize_grounding_into_graph(*, graph: Graph, mapping: Dict[str, Optional[str]], mode: str = "sameas") -> Graph:
    """
    Like materialize_grounding_into_ttl, but operates in-memory and returns the updated graph.
    """
    mode = (mode or "").strip().lower()
    if mode not in {"sameas", "replace"}:
        raise ValueError(f"Unknown mode '{mode}'. Expected 'sameas' or 'replace'.")

    if mode == "sameas":
        for old, new in mapping.items():
            if not new:
                continue
            graph.add((URIRef(old), OWL.sameAs, URIRef(new)))
        return graph

    # replace mode
    g2 = Graph()
    g2.namespace_manager = graph.namespace_manager

    def _map_term(t: Any) -> Any:
        if isinstance(t, URIRef):
            m = mapping.get(str(t))
            if m:
                return URIRef(m)
        return t

    for s, p, o in graph.triples((None, None, None)):
        g2.add((_map_term(s), _map_term(p), _map_term(o)))
    return g2


def write_graph_ttl(graph: Graph, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(out_path), format="turtle")


def compute_internal_merge_mapping(
    graph: Graph,
    *,
    max_rounds: int = 10,
) -> Dict[str, str]:
    """
    Compute a canonicalization mapping for subjects that are identical *except IRI*.

    Definition of "identical" here:
    - Same set of outgoing triples (predicate, object) for the subject
    - Subject IRI itself is ignored
    - URIRef objects are compared using the current canonical representative (fixpoint iteration)

    Returns: old_iri -> canonical_iri (only for IRIs that change).
    """
    # Union-find over URIRefs (subjects only; but can be pointed-to by object and still be canonicalized)
    parents: Dict[str, str] = {}

    def _init(x: str) -> None:
        if x not in parents:
            parents[x] = x

    def find(x: str) -> str:
        _init(x)
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x

    def union(a: str, b: str) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        # deterministic: attach larger to smaller
        if ra < rb:
            parents[rb] = ra
        else:
            parents[ra] = rb
        return True

    # Collect URIRef subjects (skip blank nodes)
    subjects: List[str] = sorted({str(s) for s in graph.subjects() if isinstance(s, URIRef)})
    for s in subjects:
        _init(s)

    def norm_term(t: Union[URIRef, BNode, Literal]) -> Tuple[str, str]:
        if isinstance(t, URIRef):
            return ("uri", find(str(t)))
        if isinstance(t, BNode):
            # BNodes are scoped to a parse; treat by N3 for stability within this merged graph
            return ("bnode", t.n3())
        # Literal
        return ("lit", t.n3())

    def signature_for(subj_iri: str) -> Tuple[Tuple[Tuple[str, str], Tuple[str, str]], ...]:
        subj = URIRef(subj_iri)
        pairs: List[Tuple[Tuple[str, str], Tuple[str, str]]] = []
        for p, o in graph.predicate_objects(subj):
            # Normalize predicate too (rarely needed, but safe)
            p_kind, p_val = norm_term(p) if isinstance(p, (URIRef, BNode, Literal)) else ("other", str(p))
            o_kind, o_val = norm_term(o) if isinstance(o, (URIRef, BNode, Literal)) else ("other", str(o))
            pairs.append(((p_kind, p_val), (o_kind, o_val)))
        pairs.sort()
        return tuple(pairs)

    # Fixpoint: signatures depend on object canonicalization
    for _round in range(max_rounds):
        groups: Dict[Tuple[Tuple[Tuple[str, str], Tuple[str, str]], ...], List[str]] = {}
        for s in subjects:
            sig = signature_for(find(s))
            groups.setdefault(sig, []).append(find(s))

        changed = False
        for sig, members in groups.items():
            uniq = sorted(set(members))
            if len(uniq) <= 1:
                continue
            canon = uniq[0]
            for other in uniq[1:]:
                changed = union(canon, other) or changed
        if not changed:
            break

    # Emit mapping for subjects that change
    out: Dict[str, str] = {}
    for s in subjects:
        r = find(s)
        if r != s:
            out[s] = r
    return out


def apply_iri_mapping_to_graph(graph: Graph, iri_mapping: Dict[str, str]) -> Graph:
    """
    Replace IRIs throughout a graph according to iri_mapping (subject/predicate/object).
    """
    if not iri_mapping:
        return graph
    g2 = Graph()
    g2.namespace_manager = graph.namespace_manager

    def _map_term(t: Any) -> Any:
        if isinstance(t, URIRef):
            m = iri_mapping.get(str(t))
            if m:
                return URIRef(m)
        return t

    for s, p, o in graph.triples((None, None, None)):
        g2.add((_map_term(s), _map_term(p), _map_term(o)))
    return g2


def main() -> None:
    p = argparse.ArgumentParser(description="Ground a TTL file to OntoSpecies and optionally write *_grounded.ttl.")
    p.add_argument("--ttl", default=str(EXAMPLE_TTL_PATH), help="Input TTL path (default: hardcoded example)")
    p.add_argument(
        "--batch-dir",
        default=None,
        help="If set, process all *.ttl under this directory recursively (skips *_grounded.ttl).",
    )
    p.add_argument("--mcp-config", default=str(MCP_CONFIG_PATH), help="MCP config JSON (default: configs/grounding.json)")
    p.add_argument("--server-key", default=MCP_SERVER_KEY, help="Server key in MCP config (default: ontospecies)")
    # Fuzzy is the intended/expected behavior. Keep flags for convenience/back-compat.
    p.add_argument(
        "--enable-fuzzy",
        dest="enable_fuzzy",
        action="store_true",
        default=True,
        help="Enable fuzzy lookup (default: enabled).",
    )
    p.add_argument(
        "--no-fuzzy",
        dest="enable_fuzzy",
        action="store_false",
        help="Disable fuzzy lookup (not recommended; mostly for debugging).",
    )
    p.add_argument(
        "--write-grounded-ttl",
        action="store_true",
        help="Write a grounded TTL copy (default output: <stem>_grounded.ttl)",
    )
    p.add_argument(
        "--grounded-ttl-out",
        default=None,
        help="Output TTL path for grounded copy (default: <stem>_grounded.ttl next to input)",
    )
    p.add_argument(
        "--grounding-mode",
        choices=["sameas", "replace"],
        default="replace",
        help="How to apply mappings to TTL: add owl:sameAs (sameas) or replace IRIs (replace)",
    )
    p.add_argument(
        "--overwrite-grounded",
        action="store_true",
        help="In batch mode, overwrite existing *_grounded.ttl files (default: skip).",
    )
    p.add_argument(
        "--no-internal-merge",
        action="store_true",
        help="Disable internal merge across TTLs in batch mode (default: enabled).",
    )
    args = p.parse_args()

    ttl_path = Path(args.ttl)
    mcp_cfg = Path(args.mcp_config)

    # Batch mode
    if args.batch_dir:
        batch_dir = Path(args.batch_dir)

        async def _run_batch() -> List[Dict[str, Any]]:
            results: List[Dict[str, Any]] = []
            ttl_files = list(iter_ttl_files(batch_dir))
            graphs: Dict[str, Graph] = {}
            merged = Graph()

            # 1) Load + merge all TTLs in-memory
            for pth in ttl_files:
                g = Graph()
                g.parse(str(pth), format="turtle")
                graphs[str(pth)] = g
                merged += g

            # 2) Internal merge (canonicalize duplicate entities across the whole merged graph)
            internal_map: Dict[str, str] = {}
            if not bool(args.no_internal_merge):
                internal_map = compute_internal_merge_mapping(merged)
                if internal_map:
                    # Apply to per-file graphs
                    for k, g in list(graphs.items()):
                        graphs[k] = apply_iri_mapping_to_graph(g, internal_map)

            # 3) Ground each (now-internally-merged) graph, reusing one MCP session
            async with OntoSpeciesLookupClient(mcp_config_path=mcp_cfg, server_key=str(args.server_key)) as client:
                for in_ttl in ttl_files:
                    g = graphs[str(in_ttl)]
                    per = await ground_graph_with_client(
                        graph=g,
                        ttl_path=in_ttl,
                        client=client,
                        mcp_config_path=mcp_cfg,
                        server_key=str(args.server_key),
                        enable_fuzzy=bool(args.enable_fuzzy),
                    )
                    per["internal_merge_changes"] = len(internal_map)

                    if args.write_grounded_ttl:
                        out_path = _default_grounded_ttl_path(in_ttl)
                        if out_path.exists() and not args.overwrite_grounded:
                            per["grounded_ttl_path"] = str(out_path.as_posix())
                            per["grounded_ttl_status"] = "skipped_exists"
                        else:
                            g_out = materialize_grounding_into_graph(
                                graph=g, mapping=dict(per.get("mapping") or {}), mode=str(args.grounding_mode)
                            )
                            write_graph_ttl(g_out, out_path)
                            per["grounded_ttl_path"] = str(out_path.as_posix())
                            per["grounded_ttl_status"] = "written"

                    results.append(per)

            # Attach internal mapping summary as the first "meta" entry (stable + small)
            results.insert(
                0,
                {
                    "batch_internal_merge": {
                        "enabled": not bool(args.no_internal_merge),
                        "num_input_ttls": len(ttl_files),
                        "num_iri_rewrites": len(internal_map),
                    }
                },
            )
            return results

        batch_results = asyncio.run(_run_batch())
        print(_stable_json({"batch_dir": str(Path(args.batch_dir).as_posix()), "results": batch_results}))
        return

    # Single-file mode (default)
    result = asyncio.run(
        ground_example_ttl_to_ontospecies(
            ttl_path=ttl_path,
            mcp_config_path=mcp_cfg,
            server_key=str(args.server_key),
            enable_fuzzy=bool(args.enable_fuzzy),
        )
    )

    if args.write_grounded_ttl:
        out = materialize_grounding_into_ttl(
            ttl_path=ttl_path,
            mapping=dict(result.get("mapping") or {}),
            out_ttl_path=Path(args.grounded_ttl_out) if args.grounded_ttl_out else None,
            mode=str(args.grounding_mode),
        )
        result["grounded_ttl_path"] = str(out.as_posix())

    print(_stable_json(result))


if __name__ == "__main__":
    main()
