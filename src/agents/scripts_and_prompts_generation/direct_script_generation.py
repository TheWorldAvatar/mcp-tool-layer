#!/usr/bin/env python3
"""
Direct LLM Script Generation (Domain-Agnostic)

This module provides direct LLM-based script generation that:
1. Loads domain-agnostic meta-prompts from ape_generated_contents/meta_prompts/mcp_scripts/
2. Parses T-Box ontology TTL to extract entity classes, properties, relationships
3. Fills meta-prompt templates with values derived from the T-Box and config artefacts
4. Calls LLM API directly (no agents, no MCP tools)
5. Writes generated code to files

Meta-prompts must not contain benchmark-specific examples, paper-level facts, or ad-hoc
field vocabularies. T-Box / schema names may appear when they are parsed from the TTL or
listed in `ape_generated_contents` configuration files (vocabulary, not empirical claims).
"""

import os
import sys
import re
import asyncio
import ast
import json
import io
import tokenize
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple, Any
try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
from dotenv import load_dotenv
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    detect_super_flat_ontology,
    extract_ontology_integrity_profile,
    format_ontology_integrity_guidance,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_generation_contract_bundle,
    validate_generated_artifacts,
    write_generation_contract_bundle,
)

# Add project root to path
project_root = Path(__file__).resolve().parents[3]


# Ensure Windows consoles don't crash on Unicode (cp1252 default).
def _configure_utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # py>=3.7
        except Exception:
            pass


_configure_utf8_stdio()


def _load_namespace_config() -> Dict[str, Any]:
    """
    Load namespace configuration from an artefact under `ape_generated_contents/`.

    This prevents hardcoding namespace URIs (or project-specific namespace variable names)
    inside the generator code or meta-prompts.
    """
    cfg = project_root / "ape_generated_contents" / "namespace_config.json"
    try:
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _load_generator_postprocess_config() -> Dict[str, Any]:
    """
    Load optional codegen postprocess from `ape_generated_contents/generator_postprocess_config.json`.

    Contains T-Box local class names and pipeline string patterns (not paper-level facts).
    If the file is missing, callers should treat the dict as empty and use safe defaults.

    `export_memory_top_link_repair` may map each extension ontology name to an object with
    `assert_root_type`, `outgoing_to_target`, `target_rdf_type` (each with `namespace` + `local`,
    producing a configured namespace/class expression in emitted code) and `match_labels_via`: a
    non-empty list of the same shape for label collection via `g.objects(node, ...)`.
    """
    cfg = project_root / "ape_generated_contents" / "generator_postprocess_config.json"
    try:
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _default_generator_postprocess() -> Dict[str, Any]:
    """
    Empty structural defaults. Extension-specific OWL class lists and flags live in
    `ape_generated_contents/generator_postprocess_config.json` so the generator code stays free of
    T-Box local names.
    """
    return {
        "scoped_context_label_tail_strip_regex": None,
        "export_memory_top_link_repair": {},
        "scoped_canonical_label_for_create": {},
    }


def _merge_generator_postprocess() -> Dict[str, Any]:
    """Merge `generator_postprocess_config.json` over `_default_generator_postprocess()`."""
    out = _default_generator_postprocess()
    raw = _load_generator_postprocess_config()
    for k, v in (raw or {}).items():
        if k == "_comment":
            continue
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            merged = {**out[k], **v}
            out[k] = merged
        else:
            out[k] = v
    return out


def _is_safe_python_dotted_id(part: str) -> bool:
    """Allow only a single identifier segment for config-driven namespace / local OWL names."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part or ""))


def _ns_local_to_py_expr(namespace: str, local: str) -> str:
    """Build `Alias.local` for injection into generated base scripts; values come from JSON config only."""
    ns, loc = (namespace or "").strip(), (local or "").strip()
    if not _is_safe_python_dotted_id(ns) or not _is_safe_python_dotted_id(loc):
        raise ValueError("invalid namespace/local for export link repair")
    return f"{ns}.{loc}"


def _validate_export_top_link_repair_spec(raw: Any) -> Dict[str, Any]:
    """
    Validate `export_memory_top_link_repair` entry for one ontology: structural T-Box refs only
    (namespace binding alias as emitted in the base script, plus OWL local name).
    """
    if not isinstance(raw, dict) or not raw:
        raise ValueError("export link repair spec must be a non-empty object")
    for key in ("assert_root_type", "outgoing_to_target", "target_rdf_type", "match_labels_via"):
        if key not in raw:
            raise ValueError(f"export link repair spec missing {key!r}")
    a = raw["assert_root_type"]
    o = raw["outgoing_to_target"]
    t = raw["target_rdf_type"]
    m = raw["match_labels_via"]
    for obj in (a, o, t):
        if not isinstance(obj, dict):
            raise ValueError("assert_root_type / outgoing / target must be objects")
        _ns_local_to_py_expr(str(obj.get("namespace", "")), str(obj.get("local", "")))
    if not isinstance(m, list) or not m:
        raise ValueError("match_labels_via must be a non-empty list")
    for item in m:
        if not isinstance(item, dict):
            raise ValueError("each match_labels_via item must be an object")
        _ns_local_to_py_expr(str(item.get("namespace", "")), str(item.get("local", "")))
    return raw


def _build_ensure_tbox_export_time_link_block(link_cfg: Dict[str, Any], tail_re: Any) -> str:
    """
    Build the `def _ensure_tbox_export_time_link...` source injected into the generated base module.
    All T-Box symbols are taken from *link_cfg* (loaded from `generator_postprocess_config.json`).
    """
    _validate_export_top_link_repair_spec(link_cfg)
    root_t = _ns_local_to_py_expr(
        str(link_cfg["assert_root_type"]["namespace"]),
        str(link_cfg["assert_root_type"]["local"]),
    )
    out_prop = _ns_local_to_py_expr(
        str(link_cfg["outgoing_to_target"]["namespace"]),
        str(link_cfg["outgoing_to_target"]["local"]),
    )
    target_t = _ns_local_to_py_expr(
        str(link_cfg["target_rdf_type"]["namespace"]),
        str(link_cfg["target_rdf_type"]["local"]),
    )
    label_rows: list[str] = []
    preds = list(link_cfg["match_labels_via"])
    first = _ns_local_to_py_expr(
        str(preds[0]["namespace"]),
        str(preds[0]["local"]),
    )
    label_rows.append(
        f"            lab = {{str(v).strip() for v in g.objects(node, {first})}}\n"
    )
    for p in preds[1:]:
        pr = _ns_local_to_py_expr(str(p["namespace"]), str(p["local"]))
        label_rows.append(
            f"            lab.update(str(v).strip() for v in g.objects(node, {pr}))\n"
        )
    label_block = "".join(label_rows)

    ensure_fn = (
        "\n\ndef _ensure_tbox_export_time_link() -> None:\n"
        "    \"\"\"Before export: ensure T-Box links for the scoped root IRI; symbols follow project config.\"\"\"\n"
        "    try:\n"
        "        _, entity_name, entity_iri = _read_global_state()\n"
        "    except Exception:\n"
        "        return\n"
        "    entity_iri = str(entity_iri or \"\").strip()\n"
        "    if not entity_iri:\n"
        "        return\n"
        "    target_name = str(entity_name or \"\").strip()\n"
    )
    if tail_re is not None and str(tail_re).strip() != "":
        pat = str(tail_re).replace("\\", "\\\\").replace('"', '\\"')
        ensure_fn += f"    target_name = re.sub(r\"{pat}\", \"\", target_name, flags=re.IGNORECASE).strip()\n"
    ensure_fn += (
        "    with locked_graph() as g:\n"
        "        top_entity = URIRef(entity_iri)\n"
        f"        g.add((top_entity, RDF.type, {root_t}))\n"
        "        linked = [\n"
        f"            obj for obj in g.objects(top_entity, {out_prop})\n"
        f"            if (obj, RDF.type, {target_t}) in g\n"
        "        ]\n"
        "        if linked:\n"
        "            return\n"
        f"        nodes = list(g.subjects(RDF.type, {target_t}))\n"
        "        preferred = None\n"
        "        for node in nodes:\n"
    )
    ensure_fn += label_block
    ensure_fn += (
        "            if target_name and target_name in lab:\n"
        "                preferred = node\n"
        "                break\n"
        "        if preferred is None and len(nodes) == 1:\n"
        "            preferred = nodes[0]\n"
        "        if preferred is not None:\n"
        f"            g.add((top_entity, {out_prop}, preferred))\n"
    )
    return ensure_fn


def _ensure_trailing_slash(uri: str) -> str:
    u = (uri or "").strip()
    if not u:
        return u
    if u.endswith("#"):
        return u
    return u if u.endswith("/") else (u + "/")


def _render_namespaces_from_config(concise_structure: Dict[str, Any]) -> Dict[str, str]:
    """
    Render namespace variable → URI mapping using `ape_generated_contents/namespace_config.json`.

    The config may contain:
    - external: {VAR_NAME: uri}
    - project_templates: {VAR_NAME: template}, where template can use `{kg_base}`.
    """
    cfg = _load_namespace_config()
    primary_ns = _ensure_trailing_slash(str(concise_structure.get("namespace_uri") or ""))
    out: Dict[str, str] = {"NAMESPACE": primary_ns}

    # Derive a kg_base for template rendering by stripping configured suffixes (config-driven).
    kg_base = primary_ns
    try:
        suffixes = cfg.get("kg_base_strip_suffixes") if isinstance(cfg, dict) else None
        if isinstance(suffixes, list):
            for suf in suffixes:
                if isinstance(suf, str) and suf and kg_base.endswith(suf):
                    kg_base = kg_base[: -len(suf)]
                    kg_base = _ensure_trailing_slash(kg_base)
                    break
    except Exception:
        kg_base = primary_ns

    ext = cfg.get("external") if isinstance(cfg, dict) else None
    if isinstance(ext, dict):
        for k, v in ext.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k.strip()] = _ensure_trailing_slash(v.strip())

    tmpl = cfg.get("project_templates") if isinstance(cfg, dict) else None
    if isinstance(tmpl, dict):
        for k, v in tmpl.items():
            if not (isinstance(k, str) and isinstance(v, str) and v.strip()):
                continue
            try:
                rendered = v.format(kg_base=kg_base)
            except Exception:
                rendered = v
            rendered = str(rendered).strip()
            if rendered:
                out[k.strip()] = _ensure_trailing_slash(rendered)

    return out


def _namespace_contract_block(concise_structure: Dict[str, Any], ontology_name: str) -> str:
    """
    Return a strict instruction block for LLM prompts.
    """
    ns = _render_namespaces_from_config(concise_structure)
    lines: list[str] = []
    lines.append("CRITICAL NAMESPACE CONTRACT (MUST FOLLOW EXACTLY):")
    lines.append("- Define these namespaces EXACTLY as below (do not invent alternative URI patterns).")
    lines.append("- Use these namespace variables consistently across base/entities/relationships scripts.")
    lines.append("")
    lines.append("```python")
    for name, uri in ns.items():
        if not uri:
            continue
        lines.append(f'{name} = Namespace("{uri}")')
    lines.append("```")
    lines.append("")
    lines.append(f"Ontology: {ontology_name}")
    return "\n".join(lines)


def _apply_namespace_contract_to_code(code: str, concise_structure: Dict[str, Any]) -> str:
    """
    Post-process generated code to enforce namespace constants deterministically.
    """
    ns = _render_namespaces_from_config(concise_structure)
    out = code
    # Replace common forms: NAME = Namespace("...") and getattr(base, "NAME", Namespace("..."))
    def _sub_simple(name: str, value: str) -> None:
        nonlocal out
        if not value:
            return
        # direct assignments
        out = re.sub(
            rf'^{name}\s*=\s*Namespace\(".*?"\)\s*$',
            f'{name} = Namespace("{value}")',
            out,
            flags=re.MULTILINE,
        )

    for name, value in ns.items():
        _sub_simple(name, value)

    # Ensure the primary NAMESPACE binding always exists, even when the LLM omits it.
    namespace_value = ns.get("NAMESPACE", "")
    if namespace_value and not re.search(r"^NAMESPACE\s*=\s*Namespace\(", out, flags=re.MULTILINE):
        insert_at = 0
        lines = out.splitlines()
        if lines and lines[0].startswith("#!"):
            insert_at = 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        if insert_at < len(lines):
            stripped = lines[insert_at].lstrip()
            quote = None
            if stripped.startswith('"""'):
                quote = '"""'
            elif stripped.startswith("'''"):
                quote = "'''"
            if quote is not None:
                insert_at += 1
                while insert_at < len(lines):
                    if quote in lines[insert_at]:
                        insert_at += 1
                        break
                    insert_at += 1
                while insert_at < len(lines) and not lines[insert_at].strip():
                    insert_at += 1
        lines.insert(insert_at, f'NAMESPACE = Namespace("{namespace_value}")')
        out = "\n".join(lines)

    # If the target file is missing one or more namespace definitions entirely, insert them.
    # We insert right after the first occurrence of `NAMESPACE = Namespace("...")` if present.
    insert_lines: list[str] = []
    def _want(name: str) -> None:
        val = ns.get(name, "")
        if not val:
            return
        # only insert if not already defined
        if re.search(rf"^{name}\s*=\s*Namespace\(", out, flags=re.MULTILINE):
            return
        insert_lines.append(f'{name} = Namespace("{val}")')

    for name in ns.keys():
        if name == "NAMESPACE":
            continue
        _want(name)

    if insert_lines:
        m = re.search(r"^NAMESPACE\s*=\s*Namespace\((['\"]).*?\1\)\s*$", out, flags=re.MULTILINE)
        if m:
            # Insert after the NAMESPACE line.
            idx = m.end()
            out = out[:idx] + "\n" + "\n".join(insert_lines) + out[idx:]
    return out


def _locked_graph_usage_is_valid(code: str) -> Tuple[bool, str]:
    """
    Enforce: relationships/checks must use `with locked_graph() as g:` (no args).
    Reject `locked_graph(g)` or any positional args.
    """
    try:
        mod = ast.parse(code)
    except Exception as e:
        return False, f"Cannot parse AST: {e}"

    bad_calls: list[str] = []
    for node in ast.walk(mod):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == "locked_graph":
                # must be called with NO positional args
                if node.args:
                    bad_calls.append("locked_graph(...) called with positional args")
    if bad_calls:
        return False, "; ".join(sorted(set(bad_calls)))
    # Also reject suspicious textual patterns that repeatedly caused failures.
    if re.search(r"with\s+locked_graph\s*\(\s*[^)\s]", code):
        return False, "locked_graph(...) used with non-empty arguments; must be locked_graph()"
    return True, ""


def _format_helpers_usage_is_valid(code: str) -> Tuple[bool, str]:
    """
    Enforce contract with base helpers:
      - _format_error(message: str, *, code=..., retryable=..., **extra)
        -> MUST NOT be called with >1 positional args.
      - _format_success_json(iri, message, *, created=..., **extra)
        -> MUST provide at least 2 positional args and MUST NOT pass `iri=` as a keyword.
    """
    try:
        mod = ast.parse(code)
    except Exception as e:
        # Syntax validation should already catch this; keep conservative.
        return False, f"Cannot parse AST: {e}"

    errors: list[str] = []
    for node in ast.walk(mod):
        if not isinstance(node, ast.Call):
            continue

        fn = node.func
        name = None
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr

        if name == "_format_error":
            if len(node.args) > 1:
                errors.append("_format_error called with >1 positional arg (must pass message only; use code=...)")

        if name == "_format_success_json":
            if len(node.args) < 2:
                errors.append("_format_success_json missing positional args (must pass iri, message)")
            if len(node.args) > 2:
                errors.append("_format_success_json called with >2 positional args (use created=... keyword)")
            if not any(kw.arg == "created" for kw in node.keywords):
                errors.append("_format_success_json missing created= keyword")
            for kw in node.keywords:
                if kw.arg == "iri":
                    errors.append("_format_success_json passed iri= as keyword (iri must be positional)")
                    break

    if errors:
        # Return a stable, readable error summary
        return False, "; ".join(sorted(set(errors)))
    return True, ""


def _base_imports_are_valid(code: str, base_script_path: str) -> Tuple[bool, str]:
    """
    Ensure generated modules do not import names from the generated base script that
    the base script does not actually define.
    """
    try:
        mod = ast.parse(code)
    except Exception as e:
        return False, f"Cannot parse AST: {e}"

    try:
        base_src = Path(base_script_path).read_text(encoding="utf-8")
        base_tree = ast.parse(base_src, filename=base_script_path)
        defined: set[str] = set()

        # Base modules legitimately export private helpers (e.g. _format_error),
        # so we must inspect the raw AST here instead of relying on
        # extract_functions_from_underlying(), which intentionally hides private names.
        for node in base_tree.body:
            if isinstance(node, ast.FunctionDef):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
    except Exception as e:
        return False, f"Cannot inspect base script exports: {e}"

    bad: list[str] = []
    for node in mod.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module or not node.module.endswith("_creation_base"):
            continue
        for alias in node.names:
            name = alias.name
            if name not in defined:
                bad.append(name)

    if bad:
        return False, "Imports missing from base script: " + ", ".join(sorted(set(bad)))
    return True, ""


def _ontology_namespace_alias_name(ontology_name: str) -> str:
    """Return a stable uppercase namespace alias for the ontology."""
    alias = re.sub(r"[^A-Za-z0-9]+", "_", str(ontology_name or "").strip()).strip("_").upper()
    return alias or "ONTOLOGY"


def _ontology_has_order_semantics(text: str) -> bool:
    """
    Detect real ordering semantics in ontology text without triggering on unrelated words
    such as `Reoperation` or `praeoperativ`.
    """
    return re.search(
        r"\b(has[_-]?order|step[_-]?order|sequence[_-]?index|ordered[_-]?member|member[_-]?order)\b",
        text or "",
        re.IGNORECASE,
    ) is not None


def _resolve_main_entity_runtime_policy(
    *,
    ontology_name: str,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the main-entity runtime policy for the selected ontology, if any."""
    cfg = meta_cfg if isinstance(meta_cfg, dict) else _load_codegen_meta_task_config()
    try:
        main = ((cfg.get("ontologies") or {}).get("main") or {})
        if str(main.get("name") or "").strip() != str(ontology_name or "").strip():
            return {}
        if not bool(main.get("complex_pipeline")):
            return {}
        policy = (main.get("runtime_policies") or {}).get("main_entity_kg") or {}
        return policy if isinstance(policy, dict) else {}
    except Exception:
        return {}


def _class_subclass_closure_from_tbox(ontology_path: str | Path | None) -> Dict[str, List[str]]:
    """Map class IRI strings to themselves plus transitive subclass IRI strings."""
    if not ontology_path:
        return {}
    p = Path(ontology_path)
    if not p.exists():
        return {}

    try:
        g = Graph()
        g.parse(str(p), format="turtle")
    except Exception:
        return {}

    direct: Dict[str, Set[str]] = {}
    classes: Set[str] = set()
    for cls in g.subjects(RDF.type, OWL.Class):
        if isinstance(cls, URIRef):
            classes.add(str(cls))
    for child, parent in g.subject_objects(RDFS.subClassOf):
        if isinstance(child, URIRef) and isinstance(parent, URIRef):
            direct.setdefault(str(parent), set()).add(str(child))
            classes.add(str(parent))
            classes.add(str(child))

    out: Dict[str, List[str]] = {}
    for root in sorted(classes):
        seen: Set[str] = {root}
        stack = list(direct.get(root, set()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(direct.get(cur, set()))
        out[root] = sorted(seen)
    return out


def _build_required_top_link_export_repair_block(
    *,
    ontology_name: str,
    ontology_path: str | Path | None,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Emit a generic pre-export repair for required top links.

    The generated code contains only IRIs/config-derived contracts, not domain facts.
    """
    policy = _resolve_main_entity_runtime_policy(ontology_name=ontology_name, meta_cfg=meta_cfg)
    shell_validation = (policy.get("shell_validation") or {}) if isinstance(policy, dict) else {}
    required_links = shell_validation.get("required_links") or []
    if not isinstance(required_links, list) or not required_links:
        return ""

    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    subclass_map = _class_subclass_closure_from_tbox(ontology_path)

    specs: list[dict[str, Any]] = []
    for raw in required_links:
        if not isinstance(raw, dict):
            continue
        pred = str(raw.get("predicate_iri") or "").strip()
        target = str(raw.get("target_class_iri") or "").strip()
        if not pred or not target:
            continue
        try:
            min_count = int(raw.get("min_count") or 0)
        except Exception:
            min_count = 0
        target_types = subclass_map.get(target) or [target]
        specs.append(
            {
                "predicate_iri": pred,
                "target_class_iri": target,
                "target_type_iris": sorted({str(x) for x in target_types if str(x).strip()}),
                "min_count": max(0, min_count),
            }
        )

    if not specs:
        return ""

    return (
        "\n\n# ------------------------------------------------------------------------------\n"
        "# Config-derived top-link repair\n"
        "# ------------------------------------------------------------------------------\n\n"
        f"_REQUIRED_TOP_LINK_SPECS = {repr(specs)}\n"
        f"_REQUIRED_TOP_CLASS_IRI = {repr(top_class_iri)}\n\n"
        "def _local_name_from_iri_value(value) -> str:\n"
        "    text = str(value or '').strip()\n"
        "    if '#' in text:\n"
        "        return text.rsplit('#', 1)[-1]\n"
        "    return text.rstrip('/').rsplit('/', 1)[-1]\n\n"
        "def _order_like_values_for_node(g: Graph, node: URIRef) -> set[int]:\n"
        "    values = set()\n"
        "    for pred, obj in g.predicate_objects(node):\n"
        "        local = _local_name_from_iri_value(pred).lower()\n"
        "        if not any(token in local for token in ('order', 'index', 'sequence')):\n"
        "            continue\n"
        "        try:\n"
        "            values.add(int(str(obj).strip()))\n"
        "        except Exception:\n"
        "            continue\n"
        "    return values\n\n"
        "def _has_non_type_resource_incoming(g: Graph, node: URIRef) -> bool:\n"
        "    for subj, pred in g.subject_predicates(node):\n"
        "        if pred == RDF.type:\n"
        "            continue\n"
        "        if isinstance(subj, URIRef):\n"
        "            return True\n"
        "    return False\n\n"
        "def _drop_node_subject_triples(g: Graph, node: URIRef) -> None:\n"
        "    for triple in list(g.triples((node, None, None))):\n"
        "        g.remove(triple)\n\n"
        "def _label_values_for_node(g: Graph, node: URIRef) -> tuple[str, ...]:\n"
        "    labels = []\n"
        "    for pred in (RDFS.label, URIRef('http://www.w3.org/2004/02/skos/core#prefLabel')):\n"
        "        labels.extend(str(obj).strip() for obj in g.objects(node, pred) if str(obj).strip())\n"
        "    if not labels:\n"
        "        labels.append(_local_name_from_iri_value(node))\n"
        "    return tuple(sorted(set(labels)))\n\n"
        "def _type_values_for_node(g: Graph, node: URIRef, target_types: set[URIRef]) -> tuple[str, ...]:\n"
        "    values = [str(t) for t in g.objects(node, RDF.type) if isinstance(t, URIRef) and t in target_types]\n"
        "    return tuple(sorted(set(values)))\n\n"
        "def _collapse_duplicate_ordered_candidates(g: Graph, candidates: list[URIRef], target_types: set[URIRef]) -> list[URIRef]:\n"
        "    groups = {}\n"
        "    passthrough = []\n"
        "    for node in candidates:\n"
        "        if _has_non_type_resource_incoming(g, node):\n"
        "            passthrough.append(node)\n"
        "            continue\n"
        "        orders = tuple(sorted(_order_like_values_for_node(g, node)))\n"
        "        if not orders:\n"
        "            passthrough.append(node)\n"
        "            continue\n"
        "        key = (_type_values_for_node(g, node, target_types), _label_values_for_node(g, node), orders)\n"
        "        groups.setdefault(key, []).append(node)\n"
        "    collapsed = list(passthrough)\n"
        "    for group in groups.values():\n"
        "        keep = sorted(group, key=str)[-1]\n"
        "        collapsed.append(keep)\n"
        "        for node in group:\n"
        "            if node != keep:\n"
        "                _drop_node_subject_triples(g, node)\n"
        "    return collapsed\n\n"
        "def _ensure_required_top_links_before_export() -> None:\n"
        "    \"\"\"Ensure config-required child resources are reachable from the locked top IRI.\"\"\"\n"
        "    if not _REQUIRED_TOP_LINK_SPECS:\n"
        "        return\n"
        "    try:\n"
        "        _, _, entity_iri = _read_global_state()\n"
        "    except Exception:\n"
        "        return\n"
        "    entity_iri = str(entity_iri or '').strip()\n"
        "    if not entity_iri:\n"
        "        return\n"
        "    with locked_graph() as g:\n"
        "        top_entity = URIRef(entity_iri)\n"
        "        if _REQUIRED_TOP_CLASS_IRI:\n"
        "            g.add((top_entity, RDF.type, URIRef(_REQUIRED_TOP_CLASS_IRI)))\n"
        "        for spec in _REQUIRED_TOP_LINK_SPECS:\n"
        "            pred = URIRef(spec['predicate_iri'])\n"
        "            target_types = {URIRef(x) for x in spec.get('target_type_iris', []) if str(x).strip()}\n"
        "            if not target_types:\n"
        "                continue\n"
        "            linked = [obj for obj in g.objects(top_entity, pred) if any((obj, RDF.type, t) in g for t in target_types)]\n"
        "            linked_order_values = set()\n"
        "            for obj in linked:\n"
        "                if isinstance(obj, URIRef):\n"
        "                    linked_order_values.update(_order_like_values_for_node(g, obj))\n"
        "            candidates = []\n"
        "            seen = set()\n"
        "            for t in target_types:\n"
        "                for node in g.subjects(RDF.type, t):\n"
        "                    if not isinstance(node, URIRef) or node == top_entity or node in seen:\n"
        "                        continue\n"
        "                    seen.add(node)\n"
        "                    if node in linked:\n"
        "                        continue\n"
        "                    candidates.append(node)\n"
        "            candidates = _collapse_duplicate_ordered_candidates(g, candidates, target_types)\n"
        "            for node in sorted(candidates, key=str):\n"
        "                if _has_non_type_resource_incoming(g, node):\n"
        "                    continue\n"
        "                node_orders = _order_like_values_for_node(g, node)\n"
        "                node_labels = ' '.join(_label_values_for_node(g, node)).lower()\n"
        "                if node_orders and any(token in node_labels for token in ('placeholder', 'dummy', 'example', 'sample')):\n"
        "                    _drop_node_subject_triples(g, node)\n"
        "                    continue\n"
        "                if node_orders and linked_order_values and node_orders.issubset(linked_order_values):\n"
        "                    _drop_node_subject_triples(g, node)\n"
        "                    continue\n"
        "                g.add((top_entity, pred, node))\n"
        "                linked.append(node)\n"
        "                linked_order_values.update(node_orders)\n"
        "            min_count = int(spec.get('min_count') or 0)\n"
        "            if min_count > 0 and len(linked) < min_count:\n"
        "                for node in sorted(candidates, key=str):\n"
        "                    if node in linked or (node, None, None) not in g:\n"
        "                        continue\n"
        "                    g.add((top_entity, pred, node))\n"
        "                    linked.append(node)\n"
        "                    if len(linked) >= min_count:\n"
        "                        break\n"
    )


def _patch_base_export_required_top_links(
    code: str,
    *,
    ontology_name: str,
    ontology_path: str | Path | None,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """Inject generic required top-link repair into generated base export wrappers."""
    if "def export_memory_wrapper" not in code:
        return code
    if "def _ensure_required_top_links_before_export" in code:
        return code
    repair_block = _build_required_top_link_export_repair_block(
        ontology_name=ontology_name,
        ontology_path=ontology_path,
        meta_cfg=meta_cfg,
    )
    if not repair_block:
        return code
    em = re.search(r"(?m)^(?:@_guard_noncheck\s*\n)?def export_memory_wrapper\(", code)
    if not em:
        return code
    code = code[: em.start()] + repair_block + "\n" + code[em.start():]
    code = re.sub(
        r"(?s)(def export_memory_wrapper\([^\)]*\)[^:]*:.*?try:\s*)return export_memory\(\)",
        r"\1_ensure_required_top_links_before_export()\n        return export_memory()",
        code,
        count=1,
    )
    return code


def _normalize_base_runtime_contracts(
    code: str,
    ontology_name: str,
    *,
    ontology_path: str | Path | None = None,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Deterministically patch base-script runtime contracts that downstream modules rely on.

    Why this exists:
    - LLM-generated relationship/entity modules sometimes import an uppercase ontology
      namespace alias from the base module.
    - They also sometimes expect tiny IRI guard/coercion helpers such as `_coerce_iri()` / `_guard_iri()`.
    - These are cheap, safe additions to the base module and make the generated script set
      more robust without depending on a perfect single-shot LLM output.
    - Long single-line `from` imports are re-parenthesized when needed (same helper as entity/relationships).
    """
    out = code
    alias_name = _ontology_namespace_alias_name(ontology_name)
    alias_line = f"{alias_name} = NAMESPACE  # Stable ontology alias for downstream imports"

    if re.search(rf"^{re.escape(alias_name)}\s*=", out, flags=re.MULTILINE):
        out = re.sub(
            rf"^{re.escape(alias_name)}\s*=.*$",
            alias_line,
            out,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        m = re.search(r"^NAMESPACE\s*=\s*Namespace\((['\"]).*?\1\)\s*$", out, flags=re.MULTILINE)
        if m:
            out = out[:m.end()] + "\n" + alias_line + out[m.end():]

    def _ensure_wraps_import(src: str) -> str:
        if "@wraps(" not in src:
            return src
        if re.search(r"(?m)^from functools import .*?\bwraps\b", src):
            return src

        functools_import = re.search(r"(?m)^from functools import (?P<names>.+)$", src)
        if functools_import:
            names = [n.strip() for n in functools_import.group("names").split(",") if n.strip()]
            if "wraps" not in names:
                names.append("wraps")
                new_line = "from functools import " + ", ".join(sorted(set(names)))
                return src[:functools_import.start()] + new_line + src[functools_import.end():]

        lines = src.splitlines()
        insert_at = 0
        if lines and lines[0].startswith("#!"):
            insert_at = 1

        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1

        if insert_at < len(lines) and (
            lines[insert_at].lstrip().startswith('"""') or lines[insert_at].lstrip().startswith("'''")
        ):
            quote = '"""' if lines[insert_at].lstrip().startswith('"""') else "'''"
            if lines[insert_at].count(quote) >= 2:
                insert_at += 1
            else:
                insert_at += 1
                while insert_at < len(lines) and quote not in lines[insert_at]:
                    insert_at += 1
                if insert_at < len(lines):
                    insert_at += 1

        while insert_at < len(lines) and lines[insert_at].startswith("from __future__ import "):
            insert_at += 1

        while insert_at < len(lines) and (
            not lines[insert_at].strip()
            or lines[insert_at].startswith("import ")
            or lines[insert_at].startswith("from ")
        ):
            insert_at += 1

        lines.insert(insert_at, "from functools import wraps")
        return "\n".join(lines)

    out = _ensure_wraps_import(out)

    if "def _coerce_iri(" not in out:
        block = (
            "\n\n# ------------------------------------------------------------------------------\n"
            "# IRI coercion\n"
            "# ------------------------------------------------------------------------------\n\n"
            "def _coerce_iri(iri: str) -> URIRef:\n"
            "    \"\"\"Convert a user-supplied IRI string into a URIRef.\"\"\"\n"
            "    s = str(iri).strip()\n"
            "    if not s:\n"
            "        raise ValueError(\"Empty IRI\")\n"
            "    return URIRef(s)\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    if "def _guard_iri(" not in out:
        block = (
            "\n\ndef _guard_iri(iri: str, role: str = \"iri\") -> tuple[Optional[URIRef], Optional[str]]:\n"
            "    \"\"\"Best-effort IRI validation helper for downstream generated modules.\"\"\"\n"
            "    try:\n"
            "        return _coerce_iri(iri), None\n"
            "    except Exception as e:\n"
            "        return None, f\"Invalid {role} IRI: {e}\"\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    if "def guard(" not in out:
        block = (
            "\n\ndef guard(*decorator_args, **decorator_kwargs):\n"
            "    \"\"\"Compatibility no-op decorator used by some generated modules.\"\"\"\n"
            "    if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:\n"
            "        return decorator_args[0]\n"
            "    def _wrap(func):\n"
            "        return func\n"
            "    return _wrap\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    if "def _validate_type(" not in out:
        block = (
            "\n\ndef _validate_type(g: Graph, entity: URIRef | str, expected_type: URIRef | str) -> tuple[bool, str]:\n"
            "    \"\"\"Compatibility helper for generated relationship/entity modules.\"\"\"\n"
            "    try:\n"
            "        ent = entity if isinstance(entity, URIRef) else _coerce_iri(str(entity))\n"
            "        cls = expected_type if isinstance(expected_type, URIRef) else _coerce_iri(str(expected_type))\n"
            "        if (ent, RDF.type, cls) in g:\n"
            "            return True, \"\"\n"
            "        return False, f\"Entity {ent} is not typed as {cls}\"\n"
            "    except Exception as e:\n"
            "        return False, str(e)\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    if "def _validate_entity_type(" not in out:
        block = (
            "\n\ndef _validate_entity_type(g: Graph, entity: URIRef | str, expected_type: URIRef | str) -> tuple[bool, str]:\n"
            "    \"\"\"Backward-compatible alias for `_validate_type`.\"\"\"\n"
            "    return _validate_type(g, entity, expected_type)\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    if "def _resource_exists(" not in out:
        block = (
            "\n\ndef _resource_exists(g: Graph, r: URIRef) -> bool:\n"
            "    \"\"\"Return True when the resource is already present in the graph.\"\"\"\n"
            "    return (r, None, None) in g or (None, None, r) in g\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    if "def _get_or_create_entity(" not in out:
        block = (
            "\n\ndef _get_or_create_entity(\n"
            "    g: Graph,\n"
            "    entity_iri: Optional[str] = None,\n"
            "    class_iri: Optional[URIRef] = None,\n"
            "    label: Optional[str] = None,\n"
            "    **kwargs,\n"
            ") -> tuple[URIRef, bool]:\n"
            "    \"\"\"Small fallback helper for generated modules that expect get-or-create semantics.\"\"\"\n"
            "    raw_iri = entity_iri or kwargs.get(\"iri\") or kwargs.get(\"entity\") or kwargs.get(\"entity_uri\")\n"
            "    created = False\n"
            "    if raw_iri:\n"
            "        node = _coerce_iri(str(raw_iri))\n"
            "    else:\n"
            "        local_name = kwargs.get(\"local_name\") or kwargs.get(\"name\") or label or \"entity\"\n"
            "        safe_local = str(local_name).strip().replace(\" \", \"_\") or \"entity\"\n"
            "        node = URIRef(str(NAMESPACE) + safe_local)\n"
            "    if not _resource_exists(g, node):\n"
            "        created = True\n"
            "        if class_iri is not None:\n"
            "            g.add((node, RDF.type, class_iri))\n"
            "        if label:\n"
            "            g.add((node, RDFS.label, Literal(str(label))))\n"
            "    return node, created\n"
        )
        marker = "# ------------------------------------------------------------------------------\n# JSON / error formatting helpers"
        if marker in out:
            out = out.replace(marker, block + "\n\n" + marker, 1)
        else:
            out += block

    # Normalize guard-state helpers to a single stable contract. Some generated bases
    # mix a `check/noncheck` path layout with `_load_guard_state()["state"]`, which
    # crashes at runtime. We standardize on one JSON state file.
    if re.search(r"def _guard_paths\(\)(?:\s*->\s*[^:]+)?:", out):
        guard_block = (
            "def _guard_paths():\n"
            "    \"\"\"Return paths for guard state files.\"\"\"\n"
            "    try:\n"
            "        doi_g, ent_g, _ = _read_global_state()\n"
            "        mem_dir = get_memory_paths(doi_g, ent_g)[\"dir\"]\n"
            "    except Exception:\n"
            "        mem_dir = os.path.dirname(__file__)\n"
            "    return {\"state\": os.path.join(mem_dir, \"guard_state.json\")}\n"
        )
        out = re.sub(
            r"def _guard_paths\(\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?(?=\n(?:def |_guard_|@|class |#|[A-Za-z_]))",
            guard_block + "\n",
            out,
            flags=re.MULTILINE,
        )

    if re.search(r"def _load_guard_state\(", out):
        load_block = (
            "def _load_guard_state():\n"
            "    \"\"\"Load guard state from file.\"\"\"\n"
            "    default_state = {\"check\": [], \"noncheck\": []}\n"
            "    paths = _guard_paths()\n"
            "    if os.path.exists(paths[\"state\"]):\n"
            "        with open(paths[\"state\"], 'r') as f:\n"
            "            loaded = json.load(f)\n"
            "        if isinstance(loaded, dict):\n"
            "            state = dict(default_state)\n"
            "            state.update(loaded)\n"
            "            if not isinstance(state.get(\"check\"), list):\n"
            "                state[\"check\"] = []\n"
            "            if not isinstance(state.get(\"noncheck\"), list):\n"
            "                state[\"noncheck\"] = []\n"
            "            return state\n"
            "    return dict(default_state)\n"
        )
        out = re.sub(
            r"def _load_guard_state\([^\n]*\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?(?=\n(?:def |_guard_|@|class |#|[A-Za-z_]))",
            load_block + "\n",
            out,
            flags=re.MULTILINE,
        )

    if re.search(r"def _save_guard_state\(", out):
        save_block = (
            "def _save_guard_state(state):\n"
            "    \"\"\"Save guard state to file.\"\"\"\n"
            "    paths = _guard_paths()\n"
            "    with open(paths[\"state\"], 'w') as f:\n"
            "        json.dump(state, f, ensure_ascii=False, indent=2)\n"
        )
        out = re.sub(
            r"def _save_guard_state\([^\n]*\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?(?=\n(?:def |_guard_|@|class |#|[A-Za-z_]))",
            save_block + "\n",
            out,
            flags=re.MULTILINE,
        )

    if re.search(r"def _guard_note_check\(", out):
        note_check_block = (
            "def _guard_note_check(kind):\n"
            "    \"\"\"Note check call.\"\"\"\n"
            "    state = _load_guard_state()\n"
            "    state.setdefault(\"check\", [])\n"
            "    state[\"check\"].append(kind)\n"
            "    _save_guard_state(state)\n"
        )
        out = re.sub(
            r"def _guard_note_check\([^\n]*\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?(?=\n(?:def |_guard_|@|class |#|[A-Za-z_]))",
            note_check_block + "\n",
            out,
            flags=re.MULTILINE,
        )

    if re.search(r"def _guard_note_noncheck\(", out):
        note_noncheck_block = (
            "def _guard_note_noncheck(name: str = \"\"):\n"
            "    \"\"\"Note non-check call.\"\"\"\n"
            "    state = _load_guard_state()\n"
            "    state.setdefault(\"noncheck\", [])\n"
            "    state[\"noncheck\"].append(str(name or \"noncheck\"))\n"
            "    _save_guard_state(state)\n"
        )
        out = re.sub(
            r"def _guard_note_noncheck\([^\n]*\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?(?=\n(?:def |_guard_|@|class |#|[A-Za-z_]))",
            note_noncheck_block + "\n",
            out,
            flags=re.MULTILINE,
        )

    if re.search(r"def _guard_noncheck\(", out):
        guard_noncheck_block = (
            "def _guard_noncheck(func):\n"
            "    \"\"\"Decorator for create/modify functions.\"\"\"\n"
            "    @wraps(func)\n"
            "    def wrapper(*args, **kwargs):\n"
            "        state = _load_guard_state()\n"
            "        if os.getenv(\"TWA_MCP_GUARD_ENFORCE\") == \"1\" and not state.get(\"check\"):\n"
            "            return _format_error(\"Guard enforcement failed: no checks performed before modification.\")\n"
            "        _guard_note_noncheck(func.__name__)\n"
            "        return func(*args, **kwargs)\n"
            "    return wrapper\n"
        )
        out = re.sub(
            r"def _guard_noncheck\([^\n]*\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?(?=\n(?:def |_guard_|@|class |#|[A-Za-z_]))",
            guard_noncheck_block + "\n",
            out,
            flags=re.MULTILINE,
        )

    if "OM2_UNIT_MAP" in out and "def _normalize_om2_unit_alias" not in out:
        alias_block = (
            "def _normalize_om2_unit_alias(unit_label: str) -> str:\n"
            "    if '_norm_unit_label' in globals():\n"
            "        key = _norm_unit_label(str(unit_label).replace('°', ' degree '))\n"
            "    else:\n"
            "        key = ' '.join(str(unit_label).replace('°', ' degree ').strip().lower().split())\n"
            "    aliases = {\n"
            "        'c': 'degree celsius',\n"
            "        'deg c': 'degree celsius',\n"
            "        'degree c': 'degree celsius',\n"
            "        'degrees c': 'degree celsius',\n"
            "        'celsius': 'degree celsius',\n"
            "        'degree celsius': 'degree celsius',\n"
            "        'degrees celsius': 'degree celsius',\n"
            "        'min': 'minute',\n"
            "        'mins': 'minute',\n"
            "        'minutes': 'minute',\n"
            "        'h': 'hour',\n"
            "        'hr': 'hour',\n"
            "        'hrs': 'hour',\n"
            "        'hours': 'hour',\n"
            "        'd': 'day',\n"
            "        'days': 'day',\n"
            "        's': 'second',\n"
            "        'sec': 'second',\n"
            "        'secs': 'second',\n"
            "        'seconds': 'second',\n"
            "    }\n"
            "    return aliases.get(key, key)\n"
        )
        out = re.sub(
            r"(def _norm_unit_label\(s: str\) -> str:\n\s+return .*\n)",
            r"\1" + alias_block,
            out,
            count=1,
        )
        if "def _normalize_om2_unit_alias" not in out and "def _resolve_om2_unit" in out:
            out = out.replace("def _resolve_om2_unit", alias_block + "\n\ndef _resolve_om2_unit", 1)
        out = out.replace("key = _norm_unit_label(unit_label)", "key = _normalize_om2_unit_alias(unit_label)")
        out = out.replace("key = str(unit_label).strip().lower()", "key = _normalize_om2_unit_alias(unit_label)")

    out = _patch_base_export_required_top_links(
        out,
        ontology_name=ontology_name,
        ontology_path=ontology_path,
        meta_cfg=meta_cfg,
    )
    if "def _ensure_required_top_links_before_export" in out:
        for helper_name in ("_read_global_state", "get_top_entity_iri"):
            if helper_name not in re.search(
                r"from \.\.universal_utils import \((.*?)\)",
                out,
                flags=re.DOTALL,
            ).group(1) if re.search(r"from \.\.universal_utils import \((.*?)\)", out, flags=re.DOTALL) else "":
                out = out.replace("    locked_graph,\n", f"    locked_graph,\n    {helper_name},\n", 1)
    out = _patch_extension_base_export_top_link(out, ontology_name)
    out = _wrap_long_singleline_from_imports(out)
    return out


def _validate_base_runtime_contracts(code: str, ontology_name: str) -> Tuple[bool, str]:
    """Ensure the generated base module exposes the minimal shared runtime contract."""
    alias_name = _ontology_namespace_alias_name(ontology_name)
    missing: list[str] = []
    if not re.search(rf"^{re.escape(alias_name)}\s*=\s*NAMESPACE\b", code, flags=re.MULTILINE):
        missing.append("missing uppercase namespace alias bound to `NAMESPACE`")
    if "def _coerce_iri(" not in code:
        missing.append("missing helper `_coerce_iri(iri: str) -> URIRef`")
    if "def _guard_iri(" not in code:
        missing.append("missing helper `_guard_iri(iri: str, role: str) -> tuple[Optional[URIRef], Optional[str]]`")
    if "@wraps(" in code and not re.search(r"(?m)^from functools import .*?\bwraps\b", code):
        missing.append("missing `from functools import wraps` required by decorator usage")
    if missing:
        return False, " | ".join(missing)
    return True, ""


def _normalize_relationships_script_contracts(code: str) -> str:
    """
    Normalize common relationship-module contract violations before validation.

    Current repairs:
    - Public `add_*` functions must NOT expose `graph`; they should acquire a locked graph internally.
    - `_add_relationship()` must not call `locked_graph(graph)`.
    - `_format_success_json({...})` dict-style misuse is rewritten into the supported positional form.
    - long single-line `from` imports are re-parenthesized to reduce line-length / truncation issues.
    """
    out = code

    # Public add_* wrappers should not expose `graph` in the MCP tool signature.
    out = re.sub(
        r"def\s+(add_[A-Za-z0-9_]+)\(\*,\s*graph:\s*Graph,\s*(.*?)\)\s*->\s*dict\s*:\n(\s*)return _add_relationship\(\n\s*graph=graph,\n",
        (
            "def \\1(\\2) -> str:\n"
            "    with locked_graph() as g:\n"
            "        return _add_relationship(\n"
            "            graph=g,\n"
        ),
        out,
        flags=re.MULTILINE | re.DOTALL,
    )
    out = re.sub(
        r"def\s+(add_[A-Za-z0-9_]+)\(\*,\s*graph:\s*Graph,\s*(.*?)\)\s*->\s*dict\s*:\n\s*return _add_relationship\(",
        (
            "def \\1(\\2) -> str:\n"
            "    with locked_graph() as g:\n"
            "        return _add_relationship("
        ),
        out,
        flags=re.MULTILINE | re.DOTALL,
    )
    out = re.sub(
        r"def\s+(add_[A-Za-z0-9_]+)\(\s*graph:\s*Graph,\s*(.*?)\)\s*->\s*dict\s*:\n(\s*)return _add_relationship\(\n\s*graph=graph,\n",
        (
            "def \\1(\\2) -> str:\n"
            "    with locked_graph() as g:\n"
            "        return _add_relationship(\n"
            "            graph=g,\n"
        ),
        out,
        flags=re.MULTILINE | re.DOTALL,
    )
    out = re.sub(
        r"def\s+(add_[A-Za-z0-9_]+)\(\s*graph:\s*Graph,\s*(.*?)\)\s*->\s*dict\s*:\n\s*return _add_relationship\(",
        (
            "def \\1(\\2) -> str:\n"
            "    with locked_graph() as g:\n"
            "        return _add_relationship("
        ),
        out,
        flags=re.MULTILINE | re.DOTALL,
    )
    # Even if we cannot rewrite the full body shape, at minimum enforce the public
    # return annotation contract on add_* functions.
    out = re.sub(
        r"(def\s+add_[A-Za-z0-9_]+\([^)]*\))\s*->\s*dict\s*:",
        r"\1 -> str:",
        out,
        flags=re.MULTILINE,
    )
    out = out.replace("return _add_relationship(graph=graph,", "return _add_relationship(graph=g,")
    out = out.replace("\n        graph=graph,", "\n            graph=g,")

    # Internal helper must use the caller-supplied graph object, not call locked_graph(graph).
    out = out.replace("with locked_graph(graph):", "with locked_graph() as g:")
    out = out.replace("_validate_resource_and_type(g=graph,", "_validate_resource_and_type(g=g,")
    out = out.replace("_add_object_triple(g=graph,", "_add_object_triple(g=g,")

    # Rewrite a recurring invalid helper call pattern:
    #   return _format_success_json({ ... })
    out = re.sub(
        r'return\s+_format_success_json\(\s*\{\s*"relationship"\s*:\s*relationship_name,\s*"subject"\s*:\s*str\(s\),\s*"predicate"\s*:\s*str\(predicate\),\s*"object"\s*:\s*str\(o\),\s*"added"\s*:\s*bool\(added\),\s*\}\s*\)',
        (
            'return _format_success_json('
            'None, '
            'f"{relationship_name} linked" if added else f"{relationship_name} already present", '
            'created=bool(added), '
            'relationship=relationship_name, '
            'subject=str(s), '
            'predicate=str(predicate), '
            'object=str(o), '
            'added=bool(added)'
            ')'
        ),
        out,
        flags=re.DOTALL,
    )
    out = out.replace(
        'return _format_success_json({"relationship": relationship_name, "subject": str(s), "predicate": str(predicate), "object": str(o), "added": bool(added)})',
        'return _format_success_json(None, f"{relationship_name} linked" if added else f"{relationship_name} already present", created=bool(added), relationship=relationship_name, subject=str(s), predicate=str(predicate), object=str(o), added=bool(added))',
    )

    class _ErrorCallRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not (isinstance(node.func, ast.Name) and node.func.id == "_format_error"):
                return node
            if len(node.args) != 2:
                return node

            first = node.args[0]
            second = node.args[1]
            # Legacy form 1:
            #   _format_error("MISSING_SUBJECT", "Subject does not exist", iri=...)
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                code_text = first.value.strip()
                if code_text and re.fullmatch(r"[A-Z_]+", code_text):
                    return ast.copy_location(
                        ast.Call(
                            func=ast.Name(id="_format_error", ctx=ast.Load()),
                            args=[second],
                            keywords=[ast.keyword(arg="code", value=ast.Constant(value=code_text)), *node.keywords],
                        ),
                        node,
                    )

            # Legacy form 2:
            #   _format_error(subject, "Subject does not exist", created=False)
            #   _format_error(None, str(e), created=False)
            # Normalize to `_format_error(message, iri=..., ...)`.
            new_keywords = list(node.keywords)
            has_iri_kw = any(kw.arg == "iri" for kw in new_keywords)
            if not has_iri_kw and not (isinstance(first, ast.Constant) and first.value is None):
                iri_value: ast.expr
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    iri_value = ast.Constant(value=first.value)
                else:
                    iri_value = ast.Call(
                        func=ast.Name(id="str", ctx=ast.Load()),
                        args=[first],
                        keywords=[],
                    )
                new_keywords.append(ast.keyword(arg="iri", value=iri_value))

            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="_format_error", ctx=ast.Load()),
                    args=[second],
                    keywords=new_keywords,
                ),
                node,
            )

    class _SuccessCallRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not (isinstance(node.func, ast.Name) and node.func.id == "_format_success_json"):
                return node

            if len(node.args) >= 2 and not any(kw.arg == "iri" for kw in node.keywords):
                return node

            iri_expr: ast.expr | None = None
            message_expr: ast.expr | None = None
            new_keywords: list[ast.keyword] = []
            created_kw: ast.keyword | None = None

            if node.args:
                iri_expr = node.args[0]
            if len(node.args) >= 2:
                message_expr = node.args[1]

            for kw in node.keywords:
                if kw.arg == "iri" and iri_expr is None:
                    iri_expr = kw.value
                    continue
                if kw.arg == "message" and message_expr is None:
                    message_expr = kw.value
                    continue
                if kw.arg == "created":
                    created_kw = kw
                new_keywords.append(kw)

            if iri_expr is None:
                iri_expr = ast.Constant(value=None)

            if message_expr is None:
                created_is_false = (
                    created_kw is not None
                    and isinstance(created_kw.value, ast.Constant)
                    and created_kw.value.value is False
                )
                message_expr = ast.Constant(value="Already exists" if created_is_false else "Created")

            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="_format_success_json", ctx=ast.Load()),
                    args=[iri_expr, message_expr],
                    keywords=new_keywords,
                ),
                node,
            )

    try:
        tree = ast.parse(out)
        tree = _ErrorCallRewriter().visit(tree)
        tree = _SuccessCallRewriter().visit(tree)
        ast.fix_missing_locations(tree)
        out = ast.unparse(tree)
    except Exception:
        pass

    out = _wrap_long_singleline_from_imports(out)
    return out


def _patch_relationship_union_domain_contracts(
    code: str,
    *,
    ontology_path: str | Path,
    ontology_name: str,
) -> str:
    """Use the ontology's union-domain superclass when generated relationship code picked one union member."""
    try:
        g = Graph()
        g.parse(str(ontology_path), format="turtle")
        ns = _derive_primary_namespace_uri(ontology_name, g)
        if not ns:
            return code
    except Exception:
        return code

    def local_name(iri: object) -> str:
        text = str(iri)
        if text.startswith(str(ns)):
            return text[len(str(ns)) :]
        if "#" in text:
            return text.rsplit("#", 1)[-1]
        return text.rstrip("/").rsplit("/", 1)[-1]

    parents: dict[str, set[str]] = {}
    for child, parent in g.subject_objects(RDFS.subClassOf):
        if str(child).startswith(str(ns)) and str(parent).startswith(str(ns)):
            parents.setdefault(local_name(child), set()).add(local_name(parent))

    def is_subclass_or_same(child: str, parent: str) -> bool:
        if child == parent:
            return True
        seen: set[str] = set()
        stack = list(parents.get(child, set()))
        while stack:
            current = stack.pop()
            if current == parent:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(parents.get(current, set()))
        return False

    preferred_domains: dict[str, str] = {}
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        if not str(prop).startswith(str(ns)):
            continue
        prop_local = local_name(prop)
        for domain in g.objects(prop, RDFS.domain):
            union_members: list[str] = []
            for union_list in g.objects(domain, OWL.unionOf):
                node = union_list
                while node and node != RDF.nil:
                    first = g.value(node, RDF.first)
                    if first is not None and str(first).startswith(str(ns)):
                        union_members.append(local_name(first))
                    node = g.value(node, RDF.rest)
            if len(union_members) < 2:
                continue
            for candidate in union_members:
                if all(is_subclass_or_same(member, candidate) for member in union_members):
                    preferred_domains[prop_local] = candidate
                    break

    out = code
    for prop_local, preferred_domain in preferred_domains.items():
        pattern = (
            rf"(def\s+add_{re.escape(prop_local)}\([^\n]*\)\s*->\s*str:\n"
            rf"(?:(?!\ndef\s+add_).)*?)domain=([A-Z][A-Z0-9_]*|NAMESPACE)\.[A-Za-z_][A-Za-z0-9_]*"
        )

        def repl(match: re.Match[str]) -> str:
            return f"{match.group(1)}domain={match.group(2)}.{preferred_domain}"

        out = re.sub(pattern, repl, out, flags=re.DOTALL)
    return out


def _normalize_format_response_calls(code: str) -> str:
    """
    Normalize helper response calls across any generated Python module.

    This is intentionally broader than relationship-script normalization so that
    entity/base/main generation paths also receive the `_format_success_json`
    positional-argument repair.
    """
    out = code

    class _ErrorCallRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not (isinstance(node.func, ast.Name) and node.func.id == "_format_error"):
                return node
            if len(node.args) != 2:
                return node

            first = node.args[0]
            second = node.args[1]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                code_text = first.value.strip()
                if code_text and re.fullmatch(r"[A-Z_]+", code_text):
                    return ast.copy_location(
                        ast.Call(
                            func=ast.Name(id="_format_error", ctx=ast.Load()),
                            args=[second],
                            keywords=[ast.keyword(arg="code", value=ast.Constant(value=code_text)), *node.keywords],
                        ),
                        node,
                    )

            new_keywords = list(node.keywords)
            has_iri_kw = any(kw.arg == "iri" for kw in new_keywords)
            if not has_iri_kw and not (isinstance(first, ast.Constant) and first.value is None):
                iri_value: ast.expr
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    iri_value = ast.Constant(value=first.value)
                else:
                    iri_value = ast.Call(
                        func=ast.Name(id="str", ctx=ast.Load()),
                        args=[first],
                        keywords=[],
                    )
                new_keywords.append(ast.keyword(arg="iri", value=iri_value))

            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="_format_error", ctx=ast.Load()),
                    args=[second],
                    keywords=new_keywords,
                ),
                node,
            )

    class _SuccessCallRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not (isinstance(node.func, ast.Name) and node.func.id == "_format_success_json"):
                return node

            if len(node.args) >= 2 and not any(kw.arg == "iri" for kw in node.keywords):
                return node

            iri_expr: ast.expr | None = None
            message_expr: ast.expr | None = None
            new_keywords: list[ast.keyword] = []
            created_kw: ast.keyword | None = None

            if node.args:
                iri_expr = node.args[0]
            if len(node.args) >= 2:
                message_expr = node.args[1]

            for kw in node.keywords:
                if kw.arg == "iri" and iri_expr is None:
                    iri_expr = kw.value
                    continue
                if kw.arg == "message" and message_expr is None:
                    message_expr = kw.value
                    continue
                if kw.arg == "created":
                    created_kw = kw
                new_keywords.append(kw)

            if iri_expr is None:
                iri_expr = ast.Constant(value=None)

            if message_expr is None:
                created_is_false = (
                    created_kw is not None
                    and isinstance(created_kw.value, ast.Constant)
                    and created_kw.value.value is False
                )
                message_expr = ast.Constant(value="Already exists" if created_is_false else "Created")

            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="_format_success_json", ctx=ast.Load()),
                    args=[iri_expr, message_expr],
                    keywords=new_keywords,
                ),
                node,
            )

    try:
        tree = ast.parse(out)
        tree = _ErrorCallRewriter().visit(tree)
        tree = _SuccessCallRewriter().visit(tree)
        ast.fix_missing_locations(tree)
        out = ast.unparse(tree)
    except Exception:
        pass

    return out


def _validate_relationship_public_api_is_valid(code: str) -> Tuple[bool, str]:
    """
    Public relationship tools must be MCP-friendly:
    - no `graph` parameter in public add_* functions
    - return annotation should be `str` when present
    """
    try:
        mod = ast.parse(code)
    except Exception as e:
        return False, f"Cannot parse AST: {e}"

    errors: list[str] = []
    for node in mod.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("add_"):
            continue

        arg_names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "graph" in arg_names:
            errors.append(f"{node.name} must not expose `graph` in its public signature")

        if node.returns is not None:
            if not (isinstance(node.returns, ast.Name) and node.returns.id == "str"):
                errors.append(f"{node.name} should return str (JSON envelope), not {ast.unparse(node.returns)}")

    if errors:
        return False, " | ".join(errors)
    return True, ""


def _validate_relationships_script_output(
    *,
    code: str,
    ontology_name: str,
    output_dir: str,
    concise_content: str,
    expected_relationship_props: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Validate a generated relationships module against runtime contracts.
    This is used for both the normal generation path and the divide-and-merge fallback.
    """
    ok, err = validate_python_syntax(code, f"{ontology_name}_creation_relationships.py")
    if not ok:
        return False, f"Syntax: {err}"

    ok_lock, lock_err = _locked_graph_usage_is_valid(code)
    if not ok_lock:
        return False, f"locked_graph misuse: {lock_err}"

    ok_fmt, fmt_err = _format_helpers_usage_is_valid(code)
    if not ok_fmt:
        return False, f"format helper misuse: {fmt_err}"

    ok_rel_api, rel_api_err = _validate_relationship_public_api_is_valid(code)
    if not ok_rel_api:
        return False, f"relationship API misuse: {rel_api_err}"

    expected_relationship_props = expected_relationship_props or []
    missing_rel_fns = [
        prop for prop in expected_relationship_props
        if f"def add_{prop}" not in code
    ]
    if missing_rel_fns:
        return (
            False,
            "missing ontology object-property add_* functions: "
            + ", ".join(f"add_{prop}" for prop in missing_rel_fns[:40]),
        )

    base_script_path = str(Path(output_dir) / f"{ontology_name}_creation_base.py")
    ok_base_imports, base_import_err = _base_imports_are_valid(code, base_script_path)
    if not ok_base_imports:
        return False, f"base import mismatch: {base_import_err}"

    ontology_order_hint = _ontology_has_order_semantics(concise_content)
    # Only enforce order-specific mutation checks when the ontology structure itself
    # signals real order semantics. Inferring this from arbitrary generated parameter
    # names causes false positives in ontologies without ordered-member semantics.
    needs_order_enforcement = ontology_order_hint
    if needs_order_enforcement:
        code_l = code.lower()
        has_helper_name = re.search(r"def\s+_(enforce|validate|check)_[a-z0-9_]*(order|orders)", code, re.IGNORECASE) is not None
        mentions_contiguity = ("contiguous" in code_l) or ("non-contiguous" in code_l) or ("noncontiguous" in code_l)
        mentions_duplicate = ("duplicate" in code_l) or ("dedup" in code_l) or ("already exists" in code_l)
        mentions_expected_range = ("range(1" in code_l) or ("1.." in code_l) or ("expected" in code_l and "order" in code_l)
        if not (has_helper_name or (mentions_duplicate and (mentions_contiguity or mentions_expected_range))):
            # Ordered-member integrity is enforced by generated step constructors and
            # downstream runtime validation. Do not reject an otherwise complete
            # object-property relationship module solely for lacking a separate
            # order helper.
            return True, ""

    return True, ""
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

def _token_limit_kwargs(model_name: str, max_tokens: int) -> dict:
    """
    OpenAI API compatibility shim:
    Some model endpoints (notably gpt-5.* / gpt-4.1.* on certain providers)
    reject `max_tokens` and require `max_completion_tokens` instead.
    """
    mn = (model_name or "").lower()
    if mn.startswith("gpt-5") or mn.startswith("gpt-4.1"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}

def _get_temperature_for_model(model_name: str) -> float:
    """
    OpenAI API compatibility shim:
    GPT-5 and GPT-5.x models only support temperature=1 (default).
    Other models can use temperature=0.2 for more deterministic output.
    """
    mn = (model_name or "").lower()
    if mn.startswith("gpt-5"):
        return 1.0
    return 0.2

def _patch_fastmcp_instruction_compat(code: str) -> str:
    """
    FastMCP compatibility shim:
    Some generated main.py files may call `mcp.set_initial_instructions(...)`, but
    FastMCP 2.x does not expose that API. Prefer a prompt named "instruction".
    """
    # Avoid `from __future__ import annotations` in generated FastMCP servers.
    # Some FastMCP/Pydantic combinations end up eval'ing annotations and can trip
    # over missing names in eval context. Without future-annotations, annotations
    # are concrete objects and don't require eval.
    code = code.replace("from __future__ import annotations\n\n", "")
    # Normalize a recurring import drift from the LLM. The installed package is
    # `fastmcp`, not `FastMCP`.
    code = code.replace("from FastMCP import FastMCP", "from fastmcp import FastMCP")

    # Fix a common broken pattern from LLMs: missing indentation after `if ...:`
    # Example:
    #   if hasattr(mcp, "set_initial_instructions"):
    #   mcp.set_initial_instructions(INSTRUCTION_PROMPT)   <-- invalid (not indented)
    #
    # Be liberal in what we match (quotes/spacing) because LLMs vary formatting.
    code = re.sub(
        r'(?m)^(if\s+hasattr\(\s*mcp\s*,\s*[\'"]set_initial_instructions[\'"]\s*\)\s*:)\s*\n'
        r'^(mcp\.set_initial_instructions\(\s*INSTRUCTION_PROMPT\s*\))\s*$',
        r'\1\n    \2',
        code,
    )

    # FastMCP 2.x safe approach: never call set_initial_instructions.
    # Replace any guard/call block with a deterministic prompt-based instruction hook.
    prompt_snippet = (
        "@mcp.prompt(name=\"instruction\")\n"
        "def instruction_prompt():\n"
        "    return INSTRUCTION_PROMPT\n"
    )

    if "set_initial_instructions" in code or "hasattr(mcp" in code:
        # If there's a guard block before the first tool wrapper, replace it entirely.
        # This avoids common broken/duplicated `if/else` indentation issues from the LLM.
        code = re.sub(
            r'(?ms)^if\s+hasattr\(\s*mcp\s*,\s*[\'"]set_initial_instructions[\'"]\s*\)\s*:.*?^@mcp\.tool',
            prompt_snippet + "\n\n@mcp.tool",
            code,
        )
        # Remove any leftover direct call
        code = re.sub(
            r'(?m)^\s*mcp\.set_initial_instructions\(\s*INSTRUCTION_PROMPT\s*\)\s*$',
            "",
            code,
        )
        # If we removed the only instruction hook, ensure snippet exists somewhere (before tools).
        if "@mcp.prompt(name=\"instruction\")" not in code:
            code = re.sub(r'(?m)^mcp\s*=\s*FastMCP\([^\n]+\)\s*$', lambda m: m.group(0) + "\n\n" + prompt_snippet, code, count=1)

    return code


def _extract_public_function_names_from_scripts(script_paths: list[str]) -> list[str]:
    """AST-based function-name extraction for validation (not codegen)."""
    names: set[str] = set()
    for p in script_paths:
        if not p or not Path(p).exists():
            continue
        src = Path(p).read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=p)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                names.add(node.name)
    return sorted(names)


def _extract_mcp_tool_wrappers_from_main(code: str) -> set[str]:
    """Return function names that are decorated with @mcp.tool(...) in main.py code."""
    tree = ast.parse(code, filename="main.py")
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                if isinstance(deco.func.value, ast.Name) and deco.func.value.id == "mcp" and deco.func.attr == "tool":
                    out.add(node.name)
    return out


def _function_owner_map(script_paths: list[str]) -> dict[str, str]:
    """
    Map function name -> module stem (filename without .py) based on where it is defined.
    Used to fix incorrect import grouping in LLM-generated main.py.
    """
    owners: dict[str, str] = {}
    for p in script_paths:
        if not p or not Path(p).exists():
            continue
        mod = Path(p).with_suffix("").name
        src = Path(p).read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=p)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                owners.setdefault(node.name, mod)
    return owners


def _rewrite_main_relative_imports(code: str, owners: dict[str, str]) -> str:
    """
    Rewrite `from .<module> import (...)` blocks so each function is imported from the module
    where it is actually defined.

    We preserve non-relative imports and keep aliases (e.g., `foo as _foo`) stable.
    """
    lines = code.splitlines()

    # Strip ALL existing relative imports (both multiline blocks and single-line imports),
    # then deterministically re-add correct owner-based imports.
    #
    # This is intentionally aggressive to prevent LLM placeholder patterns like:
    #   from .module import foo as _foo
    # from surviving into fragments / stitched mains.
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("from .") and " import (" in line:
            # skip until matching ')'
            i += 1
            while i < len(lines) and lines[i].strip() != ")":
                i += 1
            # skip the ')'
            if i < len(lines) and lines[i].strip() == ")":
                i += 1
            # also skip following blank line
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        if line.startswith("from .") and " import " in line:
            # single-line relative import -> drop
            i += 1
            continue
        out.append(line)
        i += 1

    # Find insertion point: after last non-relative import at top.
    insert_at = 0
    for idx, line in enumerate(out):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = idx + 1
            continue
        # stop once we hit first non-import statement
        if line.strip() and not line.startswith("#"):
            break

    # Determine which functions are referenced as `_fn` aliases in code.
    referenced: set[str] = set()
    for name in owners.keys():
        if f"_{name}" in code:
            referenced.add(name)
    # Fallback: if not detectable, import all known functions.
    if not referenced:
        referenced = set(owners.keys())

    # Build new grouped import blocks.
    grouped: dict[str, list[str]] = {}
    for fn in sorted(referenced):
        mod = owners.get(fn)
        if not mod:
            continue
        grouped.setdefault(mod, []).append(fn)

    import_blocks: list[str] = []
    for mod, fns in sorted(grouped.items()):
        import_blocks.append(f"from .{mod} import (")
        for fn in fns:
            import_blocks.append(f"    {fn} as _{fn},")
        import_blocks.append(")")
        import_blocks.append("")

    new_lines = out[:insert_at] + [""] + import_blocks + out[insert_at:]
    # Normalize excessive blank lines
    return "\n".join(new_lines).replace("\n\n\n", "\n\n").rstrip() + "\n"


def _strip_placeholder_module_imports(code: str) -> str:
    """
    Remove bogus placeholder imports like `from .module import ...` that the LLM sometimes emits.
    These are never valid in this repo and will break stitching/debugging.
    """
    out: list[str] = []
    for line in code.splitlines():
        if line.lstrip().startswith("from .module import"):
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _extract_firstline_docstrings_from_scripts(script_paths: list[str]) -> dict[str, str]:
    """
    Build a map: function_name -> first line of docstring (or empty).
    Only includes public (non-underscore) defs.
    """
    out: dict[str, str] = {}
    for p in script_paths:
        if not p or not Path(p).exists():
            continue
        src = Path(p).read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=p)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                ds = ast.get_docstring(node) or ""
                first = (ds.strip().splitlines()[0].strip() if ds.strip() else "")
                if first:
                    out.setdefault(node.name, first)
    return out


def _extract_tbox_comment_maps(ontology_path: str) -> tuple[dict[str, str], dict[str, str]]:
    """
    Extract rdfs:comment from ontology TTL for:
      - classes: localname -> comment
      - properties (object + datatype): localname -> comment
    Localname is derived from URI fragment after '#' or last '/'.
    """
    g = Graph()
    g.parse(ontology_path, format="turtle")

    def _local(uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[-1]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    def _shorten(text: str, max_chars: int = 380) -> str:
        t = " ".join(str(text).split())
        if len(t) <= max_chars:
            return t
        # Prefer first 1-2 sentences if possible
        parts = t.split(". ")
        if len(parts) >= 2:
            cand = (parts[0] + ". " + parts[1]).strip()
            if len(cand) <= max_chars:
                return cand
        return t[: max_chars - 3].rstrip() + "..."

    class_comments: dict[str, str] = {}
    prop_comments: dict[str, str] = {}

    # Primary pass: explicit typing.
    for cls in g.subjects(RDF.type, OWL.Class):
        for c in g.objects(cls, RDFS.comment):
            name = _local(str(cls))
            if name and name not in class_comments:
                class_comments[name] = _shorten(str(c))

    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        for c in g.objects(prop, RDFS.comment):
            name = _local(str(prop))
            if name and name not in prop_comments:
                prop_comments[name] = _shorten(str(c))

    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        for c in g.objects(prop, RDFS.comment):
            name = _local(str(prop))
            if name and name not in prop_comments:
                prop_comments[name] = _shorten(str(c))

    # Fallback pass: anything with rdfs:comment but missing explicit type (some TTLs are inconsistent).
    prop_like_prefixes = ("has", "is", "uses", "retrievedFrom", "references", "inherits", "removes")
    for subj, c in g.subject_objects(RDFS.comment):
        name = _local(str(subj))
        if not name:
            continue
        # Skip if already captured by typed passes
        if name in class_comments or name in prop_comments:
            continue
        # Heuristic classification
        if name.startswith(prop_like_prefixes):
            prop_comments[name] = _shorten(str(c))
        else:
            class_comments[name] = _shorten(str(c))

    return class_comments, prop_comments


def _tbox_hint_for_tool(tool_name: str, class_comments: dict[str, str], prop_comments: dict[str, str]) -> str:
    """
    Return a short T-Box hint to embed into tool docstrings for create_/add_/check_existing_ tools.
    """
    if tool_name.startswith("create_"):
        cls = tool_name.replace("create_", "", 1)
        return class_comments.get(cls, "")
    if tool_name.startswith("check_existing_"):
        cls = tool_name.replace("check_existing_", "", 1)
        return class_comments.get(cls, "")
    if tool_name.startswith("add_"):
        m = re.match(r"^add_(.+)_to_(.+)$", tool_name)
        if m:
            prop = m.group(1)
            return prop_comments.get(prop, "")
    return ""


def _ensure_mcp_tool_docstrings(code: str, doc_map: dict[str, str]) -> str:
    """
    Ensure each top-level function decorated with @mcp.tool has a docstring.
    Uses doc_map[fn] if available, else a generic description.
    """
    try:
        tree = ast.parse(code, filename="main.py")
    except Exception:
        # If we can't parse, don't attempt rewriting here.
        return code

    changed = False
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        is_tool = False
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                if isinstance(deco.func.value, ast.Name) and deco.func.value.id == "mcp" and deco.func.attr == "tool":
                    is_tool = True
        if not is_tool:
            continue
        existing = ast.get_docstring(node)
        if existing and existing.strip():
            continue
        text = doc_map.get(node.name) or f"FastMCP tool `{node.name}`."
        # Insert docstring as first statement
        node.body.insert(0, ast.Expr(value=ast.Constant(value=text)))
        changed = True

    if not changed:
        return code
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree).rstrip() + "\n"
    except Exception:
        return code


def _ensure_mcp_tool_docstrings_with_tbox(
    code: str,
    doc_map: dict[str, str],
    class_comments: dict[str, str],
    prop_comments: dict[str, str],
) -> str:
    """
    Ensure each @mcp.tool wrapper has a concise generic docstring.

    We intentionally avoid injecting ontology/domain-specific T-Box guidance into
    tool docstrings. The runtime workflow should be guided by generic examples and
    config-derived integrity rules, not by hardcoded domain semantics embedded in
    each tool wrapper.
    """
    try:
        tree = ast.parse(code, filename="main.py")
    except Exception:
        return code

    changed = False
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        is_tool = False
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                if isinstance(deco.func.value, ast.Name) and deco.func.value.id == "mcp" and deco.func.attr == "tool":
                    is_tool = True
        if not is_tool:
            continue

        existing = (ast.get_docstring(node) or "").strip()
        base = doc_map.get(node.name) or existing or f"FastMCP tool `{node.name}`."
        if "\n\nT-Box:" in base:
            base = base.split("\n\nT-Box:", 1)[0].rstrip()
        if "\nT-Box:" in base:
            base = base.split("\nT-Box:", 1)[0].rstrip()
        base = base.strip() or f"FastMCP tool `{node.name}`."
        # Replace or insert
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
            if node.body[0].value.value != base:
                node.body[0].value = ast.Constant(value=base)
                changed = True
        else:
            node.body.insert(0, ast.Expr(value=ast.Constant(value=base)))
            changed = True

    if not changed:
        return code
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree).rstrip() + "\n"
    except Exception:
        return code


def _rewrite_main_wrapper_self_calls(code: str) -> str:
    """
    Fix a common LLM failure mode in generated main.py:
      def create_Foo(...): return create_Foo(...)
    which is infinite recursion.

    Our import rewriter standardizes underlying imports as:
      from .<module> import ( create_Foo as _create_Foo, ... )
    so the wrapper must delegate to the underscored alias.
    """
    lines = code.splitlines()

    # Discover which underscored aliases exist (we only rewrite when the alias exists).
    # Example line: "    create_Add as _create_Add,"
    underscore_aliases: set[str] = set()
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_]\w*)\s+as\s+(_[A-Za-z_]\w*)\s*,\s*$", line)
        if m:
            underscore_aliases.add(m.group(2))

    def_alias = re.compile(r"^def\s+([A-Za-z_]\w*)\s*\(")
    in_func = False
    current_name: str | None = None
    current_indent = 0

    out: list[str] = []
    for line in lines:
        m_def = def_alias.match(line.lstrip() if line.startswith("def ") else line)
        # Only treat top-level defs as wrapper candidates (generated main.py is flat).
        if line.startswith("def "):
            in_func = True
            current_name = m_def.group(1) if m_def else None
            current_indent = 0
            out.append(line)
            continue
        if in_func and line.startswith("def "):
            # unreachable due to earlier check, but keep for clarity
            out.append(line)
            continue
        # Exit function context when we hit another top-level def/decorator.
        if in_func and (line.startswith("@") or line.startswith("def ")):
            current_name = None
            in_func = line.startswith("def ")
            out.append(line)
            continue

        if in_func and current_name:
            # Only rewrite direct self-calls in return statements.
            # Examples (blurred):
            #   return create_SomeClass(...)
            #   return check_existing_SomeOtherClass()
            target_alias = f"_{current_name}"
            if target_alias in underscore_aliases:
                # Preserve leading whitespace
                prefix = re.match(r"^\s*", line).group(0)  # type: ignore[union-attr]
                stripped = line.strip()
                if stripped.startswith(f"return {current_name}("):
                    line = prefix + stripped.replace(f"return {current_name}(", f"return {target_alias}(", 1)
                elif stripped == f"return {current_name}()":
                    line = prefix + f"return {target_alias}()"

        out.append(line)

    return "\n".join(out).rstrip() + "\n"


def _rewrite_calls_to_underscored_imports(code: str) -> str:
    """
    If we import `foo as _foo`, rewrite call-sites `foo(...)` -> `_foo(...)`.

    This catches cases like:
      from .base import init_memory_wrapper as _init_memory_wrapper
      def init_memory(...): return init_memory_wrapper(...)
    which would otherwise NameError.
    """
    lines = code.splitlines()

    # Build mapping foo -> _foo from the standardized import blocks.
    # Example: "    init_memory_wrapper as _init_memory_wrapper,"
    mapping: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_]\w*)\s+as\s+(_[A-Za-z_]\w*)\s*,\s*$", line)
        if m:
            mapping[m.group(1)] = m.group(2)

    if not mapping:
        return code

    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        # Don't touch import lines or def lines.
        if stripped.startswith("from ") or stripped.startswith("import ") or stripped.startswith("def ") or stripped.startswith("@"):
            out.append(line)
            continue

        # Avoid rewriting within strings/comments; keep this simple and conservative.
        if stripped.startswith("#"):
            out.append(line)
            continue

        new_line = line
        for src, dst in mapping.items():
            # Replace only function-call sites; not attributes; not already underscored.
            # e.g. "return init_memory_wrapper(" -> "return _init_memory_wrapper("
            new_line = re.sub(rf"(?<![\w\.]){re.escape(src)}\s*\(", f"{dst}(", new_line)
        out.append(new_line)

    return "\n".join(out).rstrip() + "\n"


def _validate_underscored_alias_calls(code: str) -> tuple[bool, str]:
    """
    Validate (without rewriting) that if we import `foo as _foo`, then calls use `_foo(...)`
    and wrappers do not call themselves.
    """
    try:
        tree = ast.parse(code, filename="main.py")
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"

    # Build mapping: foo -> _foo from ImportFrom nodes
    mapping: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname and alias.asname.startswith("_"):
                    mapping[alias.name] = alias.asname

    if not mapping:
        # Not an error by itself, but we expect aliasing in our generated main.py.
        return True, ""

    # Collect bad call-sites: calling foo(...) when foo is mapped to _foo.
    bad_calls: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.current_func: str | None = None

        def visit_FunctionDef(self, node: ast.FunctionDef):
            prev = self.current_func
            self.current_func = node.name
            self.generic_visit(node)
            self.current_func = prev

        def visit_Call(self, node: ast.Call):
            # Only care about simple name calls: foo(...)
            if isinstance(node.func, ast.Name):
                fn = node.func.id
                if fn in mapping:
                    # 1) Calling foo(...) instead of _foo(...)
                    # 2) If inside wrapper `def foo`, then foo(...) is also self-recursion.
                    loc = f"{fn}(... ) at line {getattr(node, 'lineno', '?')}"
                    if self.current_func == fn:
                        bad_calls.append(f"WRAPPER SELF-CALL: {loc} inside def {fn} (must call {mapping[fn]}(...))")
                    else:
                        bad_calls.append(f"UNALIASED CALL: {loc} (must call {mapping[fn]}(...))")
            self.generic_visit(node)

    Visitor().visit(tree)

    if bad_calls:
        preview = "\n".join(f"- {x}" for x in bad_calls[:30])
        return False, (
            "Found calls to un-aliased imported functions. "
            "If you import `foo as _foo`, you MUST call `_foo(...)` everywhere. "
            "Also wrapper functions must never call themselves.\n"
            f"{preview}"
        )

    return True, ""


def _fix_reversed_underscored_alias_assignments(code: str) -> str:
    """
    Repair a recurring generated-main bug where a compatibility alias is written backwards.

    Example:
        _add_hasTemperatureRate = _add_hasTemperature_rate

    If the left-hand symbol is already imported/defined, the right-hand symbol is missing,
    and both names normalize to the same text when underscores are ignored, rewrite to:

        _add_hasTemperature_rate = _add_hasTemperatureRate
    """
    lines = code.splitlines()
    known: set[str] = set()

    import_re = re.compile(r"^\s*from\s+\S+\s+import\s+(.+)$")
    def_re = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(")
    alias_re = re.compile(r"^(\s*)(_[A-Za-z]\w*)\s*=\s*(_[A-Za-z]\w*)\s*$")

    for line in lines:
        m_import = import_re.match(line)
        if m_import:
            for part in m_import.group(1).split(","):
                item = part.strip()
                if not item:
                    continue
                if " as " in item:
                    _, alias = item.rsplit(" as ", 1)
                    alias = alias.strip()
                    if alias:
                        known.add(alias)
                else:
                    known.add(item)
            continue
        m_def = def_re.match(line)
        if m_def:
            known.add(m_def.group(1))

    def _norm(name: str) -> str:
        return re.sub(r"_+", "", str(name or "")).lower()

    rewritten: list[str] = []
    for line in lines:
        m_alias = alias_re.match(line)
        if not m_alias:
            rewritten.append(line)
            continue
        indent, lhs, rhs = m_alias.groups()
        if lhs in known and rhs not in known and _norm(lhs) == _norm(rhs):
            rewritten.append(f"{indent}{rhs} = {lhs}")
            known.add(rhs)
            continue
        rewritten.append(line)
    return "\n".join(rewritten)


def _normalize_param_key(name: str) -> str:
    """
    Normalize a parameter-like identifier for fuzzy matching.
    We intentionally use a very conservative normalizer to catch common LLM typos like
    `hasTemperature_rate_value` vs `hasTemperatureRate_value`.
    """
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _rewrite_main_wrapper_forwarding_param_typos(code: str) -> str:
    """
    Fix a common LLM failure mode in generated/stiched main.py:
    wrappers forward keyword values using a misspelled local Name that is not a parameter.

    Example:
      def create_GenericClass(..., hasSomeQuantity_value=None, ...):
          return _create_GenericClass(..., hasSomeQuantity_value=hasSome_quantity_value, ...)

    We attempt a safe auto-correction ONLY when there is a unique normalized match among
    the wrapper's parameters.
    """
    try:
        tree = ast.parse(code, filename="main.py")
    except SyntaxError:
        return code

    changed = False

    class Fixer(ast.NodeTransformer):
        def _fold_constant_ifexp(self, expr: ast.AST) -> ast.AST:
            """
            Fold conditional expressions like `A if False else B` (or True) to a single branch.
            This prevents "dead-branch" hacks from hiding typos in generated wrappers.
            """
            if isinstance(expr, ast.IfExp) and isinstance(expr.test, ast.Constant) and isinstance(expr.test.value, bool):
                return self._fold_constant_ifexp(expr.body if expr.test.value else expr.orelse)
            return expr

        def visit_FunctionDef(self, node: ast.FunctionDef):
            nonlocal changed
            # Build parameter set for this wrapper
            params: set[str] = set()
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                if a.arg != "self":
                    params.add(a.arg)
            if node.args.vararg is not None:
                params.add(node.args.vararg.arg)
            if node.args.kwarg is not None:
                params.add(node.args.kwarg.arg)

            # Only attempt to rewrite simple "return _foo(...)" style wrappers
            if not node.body:
                return node
            last = node.body[-1]
            if not (isinstance(last, ast.Return) and isinstance(last.value, ast.Call)):
                return node
            call = last.value
            if not isinstance(call.func, ast.Name):
                return node
            if not call.func.id.startswith("_"):
                return node

            # Rewrite keyword values that are bare Names not in params
            for kw in call.keywords:
                if kw.arg is None:
                    continue
                # First fold constant if-expressions (e.g., `x if False else y`)
                kw.value = self._fold_constant_ifexp(kw.value)
                if isinstance(kw.value, ast.Name):
                    v = kw.value.id
                    if v in params:
                        continue
                    # If the name is a common constant, ignore
                    if v in {"True", "False", "None"}:
                        continue
                    target_key = _normalize_param_key(v)
                    if not target_key:
                        continue
                    matches = [p for p in sorted(params) if _normalize_param_key(p) == target_key]
                    if len(matches) == 1:
                        kw.value = ast.Name(id=matches[0], ctx=ast.Load())
                        changed = True
            return node

    Fixer().visit(tree)
    if not changed:
        return code
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree).rstrip() + "\n"
    except Exception:
        return code


def _validate_main_wrapper_forwarding_uses_defined_params(code: str, filename: str = "main.py") -> tuple[bool, str]:
    """
    Validate that in simple delegation wrappers (`return _foo(..., x=x, ...)`),
    any keyword value that is a bare Name refers to a defined wrapper parameter.

    This catches NameError-inducing typos like `hasTemperature_rate_value`.
    """
    try:
        tree = ast.parse(code, filename=filename)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"

    problems: list[str] = []

    class V(ast.NodeVisitor):
        def _fold_constant_ifexp(self, expr: ast.AST) -> ast.AST:
            if isinstance(expr, ast.IfExp) and isinstance(expr.test, ast.Constant) and isinstance(expr.test.value, bool):
                return self._fold_constant_ifexp(expr.body if expr.test.value else expr.orelse)
            return expr

        def visit_FunctionDef(self, node: ast.FunctionDef):
            # Only validate wrappers that directly return a call
            params: set[str] = set()
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                if a.arg != "self":
                    params.add(a.arg)
            if node.args.vararg is not None:
                params.add(node.args.vararg.arg)
            if node.args.kwarg is not None:
                params.add(node.args.kwarg.arg)

            if not node.body:
                return
            last = node.body[-1]
            if not (isinstance(last, ast.Return) and isinstance(last.value, ast.Call)):
                return
            call = last.value
            if not isinstance(call.func, ast.Name):
                return
            if not call.func.id.startswith("_"):
                return

            for kw in call.keywords:
                if kw.arg is None:
                    continue
                expr = self._fold_constant_ifexp(kw.value)
                # Collect ALL Name nodes used in the expression and ensure they are defined params.
                for sub in ast.walk(expr):
                    if isinstance(sub, ast.Name):
                        v = sub.id
                        if v in params:
                            continue
                        problems.append(
                            f"{node.name}: keyword '{kw.arg}' references non-parameter name '{v}' "
                            f"(line {getattr(sub, 'lineno', '?')})"
                        )

    V().visit(tree)
    if problems:
        preview = "\n".join(f"- {p}" for p in problems[:30])
        return False, (
            f"{filename}: wrapper forwarding uses undefined names (likely typo / NameError).\n"
            f"{preview}"
        )
    return True, ""


def _format_main_entity_runtime_policy_for_mcp_prompt(
    meta_cfg: dict | None,
    ontology_name: str,
) -> str:
    """
    Render `runtime_policies.main_entity_kg` from meta_task_config into MCP main-generation text.

    Domain-agnostic: reads IRIs and flags from config (no ontology-specific hardcoding).
    Used so regeneration improves **prompts / generated main.py**, not post-hoc TTL edits.
    """
    if not meta_cfg or not isinstance(meta_cfg, dict):
        return ""
    try:
        main = (meta_cfg.get("ontologies") or {}).get("main") or {}
        if str(main.get("name") or "").strip() != ontology_name:
            return ""
        if not bool(main.get("complex_pipeline")):
            return ""
        pol = (main.get("runtime_policies") or {}).get("main_entity_kg") or {}
        if not pol:
            return ""
    except Exception:
        return ""

    lines: list[str] = [
        "",
        "## RUNTIME GRAPH INTEGRITY (from meta_task_config; MUST shape INSTRUCTION_PROMPT + relationship usage)",
        "",
        "The KG session is tied to a **single top-level entity IRI** (from global state / iter1). In exported TTL:",
    ]
    sv = pol.get("shell_validation") or {}
    top_cls = str(sv.get("top_entity_class_iri") or "").strip()
    if top_cls:
        lines.append(
            f"- That IRI MUST be the subject of `rdf:type` <{top_cls}> (do not mint a second parallel top entity IRI)."
        )
    pr = pol.get("prompt_rules") or {}
    if pr.get("require_top_entity_reuse"):
        lines.append("- Reuse that top-level IRI everywhere; do not substitute a different scoped root.")
    if pr.get("forbid_new_top_entity_creation"):
        lines.append("- Do not create an additional top-level entity instance for the same scoped case.")
    req = sv.get("required_links") or []
    if isinstance(req, list) and req:
        lines.append("- Before `export_memory`, ensure that top-level IRI has these links (use existing typed instances in the graph when possible):")
        for spec in req:
            if not isinstance(spec, dict):
                continue
            pred = str(spec.get("predicate_iri") or "").strip()
            target = str(spec.get("target_class_iri") or "").strip()
            mc = int(spec.get("min_count") or 0)
            if pred and target:
                pl = pred.rsplit("/", 1)[-1]
                tl = target.rsplit("/", 1)[-1]
                lines.append(f"  - `{pred}` → at least {mc} object with `rdf:type` <{target}> (`{pl}` → `{tl}`).")
    lines.append("")
    lines.append(
        "`add_*` tools MUST attach these edges to the **locked top-level IRI**, not to ad-hoc duplicates."
    )
    lines.append("")
    return "\n".join(lines)


def _load_codegen_meta_task_config(
    config_path: str | Path = "configs/meta_task/meta_task_config.json",
) -> Dict[str, Any]:
    """Load meta task config for generator-side runtime contracts."""
    try:
        p = Path(config_path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _local_name_from_iri(iri: str) -> str:
    """Extract the local name from an IRI-like string."""
    s = str(iri or "").strip()
    if not s:
        return ""
    if "#" in s:
        return s.rsplit("#", 1)[-1].strip()
    return s.rstrip("/").rsplit("/", 1)[-1].strip()


def _resolve_top_entity_codegen_contract(
    *,
    ontology_name: str,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve generator-side top-entity reuse requirements from meta_task_config.

    This keeps the script generator aligned with the same runtime contract used by
    main KG building: one locked top-level entity IRI per scoped case, no parallel roots.
    """
    cfg = meta_cfg if isinstance(meta_cfg, dict) else _load_codegen_meta_task_config()
    if not cfg:
        return {}
    try:
        runtime = _resolve_main_entity_runtime_policy(ontology_name=ontology_name, meta_cfg=cfg)
        if not runtime:
            return {}
        main = ((cfg.get("ontologies") or {}).get("main") or {})
        prompt_rules = runtime.get("prompt_rules") or {}
        shell_validation = runtime.get("shell_validation") or {}
        if not (
            prompt_rules.get("require_top_entity_reuse")
            and prompt_rules.get("forbid_new_top_entity_creation")
        ):
            return {}
        top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
        iter1 = (main.get("runtime_policies") or {}).get("iter1_top_entity_kg") or {}
        iter1_prompt_rules = (iter1.get("prompt_rules") or {}) if isinstance(iter1, dict) else {}
        top_class_local = (
            _local_name_from_iri(top_class_iri)
            or str(iter1_prompt_rules.get("top_level_entity_name") or "").strip()
        )
        if not top_class_local:
            return {}
        return {
            "class_iri": top_class_iri,
            "class_local": top_class_local,
            "require_top_entity_reuse": True,
            "forbid_new_top_entity_creation": True,
            "iter1_allows_multiple": True,
        }
    except Exception:
        return {}


def _build_top_entity_codegen_prompt_block(
    *,
    top_entity_contract: Dict[str, Any],
    class_names: List[str],
) -> str:
    """Render generator instructions for the configured top-level entity class."""
    top_class_local = str((top_entity_contract or {}).get("class_local") or "").strip()
    top_class_iri = str((top_entity_contract or {}).get("class_iri") or "").strip()
    if not top_class_local or top_class_local not in {str(c).strip() for c in (class_names or [])}:
        return ""
    iri_line = (
        f"- `create_{top_class_local}` MUST use the locked top-level entity IRI whose runtime class is `<{top_class_iri}>` when that node is unused or already represents the same label.\n"
        if top_class_iri
        else f"- `create_{top_class_local}` MUST use the locked top-level entity IRI from global state when that node is unused or already represents the same label.\n"
    )
    return (
        "\nTOP-LEVEL ENTITY CREATION CONTRACT (CRITICAL):\n"
        + iri_line
        + f"- Inside `create_{top_class_local}`, read the locked root IRI via `get_top_entity_iri()` after `init_memory` has run.\n"
        + f"- If no locked IRI is available, return `_format_error(..., code='MEMORY_INIT_REQUIRED')` instead of minting a new `{top_class_local}`.\n"
        + f"- First check for an existing `{top_class_local}` with the same sanitized label and reuse it if found.\n"
        + f"- For ITERATION 1, multiple top-level procedures are allowed: if the locked node already has a different label, mint a fresh `{top_class_local}` IRI for the additional label.\n"
        + f"- For later scoped KG-building passes, prompts must reuse the provided entity IRI and must not call `create_{top_class_local}` to create sibling roots.\n"
        + f"- Always assert rdf:type for `{top_class_local}` and write label/properties on the selected node.\n"
    )


def _validate_top_entity_create_contract(
    src: str,
    *,
    top_entity_contract: Dict[str, Any],
) -> tuple[bool, str]:
    """Validate that the configured top-level create_* tool reuses the locked root IRI."""
    top_class_local = str((top_entity_contract or {}).get("class_local") or "").strip()
    if not top_class_local:
        return True, "OK"
    fn_name = f"create_{top_class_local}"
    try:
        mod = ast.parse(src)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"

    target_fn: ast.FunctionDef | None = None
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            target_fn = node
            break
    if target_fn is None:
        return True, "OK"

    seg = ast.get_source_segment(src, target_fn) or ""
    if "get_top_entity_iri" not in seg:
        return (
            False,
            f"{fn_name} must call `get_top_entity_iri()` to seed/reuse the locked top-level entity IRI from global state.",
        )
    if "_find_by_type_and_label" not in seg:
        return (
            False,
            f"{fn_name} must check for an existing `{top_class_local}` by label before creating a new node.",
        )
    mint_pat = rf"_mint_hash_iri\(\s*['\"]{re.escape(top_class_local)}['\"]\s*\)"
    if not re.search(mint_pat, seg):
        return (
            False,
            f"{fn_name} must support ITER1 multi-top creation by minting an additional `{top_class_local}` when the locked node is already occupied by another label.",
        )
    # Primary: PREFIX.Class or PREFIX['Class']. Allow lowercase-first prefixes
    # so namespace aliases are handled consistently across ontologies.
    type_pat = (
        r"RDF\.type\s*,\s*[^)\n]*"
        + r"(?:[A-Za-z][A-Za-z0-9_]*\[\s*[\"']"
        + re.escape(top_class_local)
        + r"[\"']\s*\]|[A-Za-z][A-Za-z0-9_]*\."
        + re.escape(top_class_local)
        + r")"
    )
    if re.search(type_pat, seg) is None:
        # Alternate: explicit IRI (same class as in meta_task_config shell_validation). Equivalently valid;
        # generators sometimes emit URIRef(...) instead of a namespace binding—do not treat as weaker.
        top_iri = str((top_entity_contract or {}).get("class_iri") or "").strip()
        if top_iri:
            iri_esc = re.escape(top_iri)
            uri_pat = r"RDF\.type\s*,\s*URIRef\s*\(\s*[\"']" + iri_esc + r"[\"']\s*\)"
            if re.search(uri_pat, seg) is not None:
                return True, "OK"
        # Helpers may assert typing only inside `_ensure_type_with_label(g, iri, <class>, ...)`.
        m_ensure = re.search(
            r"_ensure_type_with_label\s*\(\s*[^,]+,\s*[^,]+,\s*([^,)]+)",
            seg,
        )
        if m_ensure:
            cls_expr = m_ensure.group(1).strip()
            if top_class_local in cls_expr:
                return True, "OK"
            if top_iri and top_iri in cls_expr.replace('"', "").replace("'", ""):
                return True, "OK"
        # Last resort: typing is present but formatted outside the strict patterns above.
        if "RDF.type" in seg and top_class_local in seg:
            return True, "OK"
        return (
            False,
            f"{fn_name} must assert rdf:type for the locked top-level `{top_class_local}` entity.",
        )
    return True, "OK"


def _patch_top_entity_create_multi_contract(code: str, ontology_name: str = "") -> str:
    """Patch the generated top-level create tool to support ITER1 multi-top creation."""
    contract = _resolve_top_entity_codegen_contract(ontology_name=ontology_name)
    top_class_local = str((contract or {}).get("class_local") or "").strip()
    top_class_iri = str((contract or {}).get("class_iri") or "").strip()
    if not top_class_local:
        return code
    fn_name = f"create_{top_class_local}"
    try:
        mod = ast.parse(code)
    except SyntaxError:
        return code

    target_fn: ast.FunctionDef | None = None
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            target_fn = node
            break
    if target_fn is None:
        return code

    seg = ast.get_source_segment(code, target_fn) or ""
    if all(x in seg for x in ("_find_by_type_and_label", "_mint_hash_iri", "top_is_available")):
        return code

    type_expr_match = re.search(
        r"RDF\.type\s*,\s*([A-Za-z][A-Za-z0-9_]*\." + re.escape(top_class_local) + r")\b",
        seg,
    )
    if type_expr_match:
        type_expr = type_expr_match.group(1)
    elif top_class_iri:
        type_expr = "URIRef(\"" + top_class_iri.replace("\\", "\\\\").replace('"', '\\"') + "\")"
    else:
        type_expr = f"NAMESPACE.{top_class_local}"

    lines = code.splitlines()
    fn_start = (target_fn.lineno or 1) - 1
    fn_end = getattr(target_fn, "end_lineno", None) or len(lines)
    with_idx: int | None = None
    label_idx: int | None = None
    for idx in range(fn_start, fn_end):
        if with_idx is None and re.search(r"with\s+locked_graph\(\)\s+as\s+\w+\s*:", lines[idx]):
            with_idx = idx
            continue
        if with_idx is not None and "_set_single_label(" in lines[idx]:
            label_idx = idx
            break
    if with_idx is None or label_idx is None or label_idx <= with_idx + 1:
        return code

    body_indent = re.match(r"^(\s*)", lines[with_idx + 1]).group(1)  # type: ignore[union-attr]
    patched_block = [
        f"{body_indent}lbl = _sanitize_label(label)",
        f"{body_indent}if not lbl:",
        f"{body_indent}    return _format_error('label is required', code='VALIDATION_FAILED')",
        f"{body_indent}existing = _find_by_type_and_label(g, {type_expr}, lbl)",
        f"{body_indent}created = False",
        f"{body_indent}if existing is not None:",
        f"{body_indent}    iri = existing",
        f"{body_indent}else:",
        f"{body_indent}    top_iri_str = str(get_top_entity_iri() or '').strip()",
        f"{body_indent}    if not top_iri_str:",
        f"{body_indent}        return _format_error('Top-level {top_class_local} IRI is not initialised in memory. Run init_memory first.', code='MEMORY_INIT_REQUIRED')",
        f"{body_indent}    top_iri = URIRef(top_iri_str)",
        f"{body_indent}    top_labels = list(g.objects(top_iri, RDFS.label))",
        f"{body_indent}    top_is_available = (top_iri, RDF.type, {type_expr}) not in g or not top_labels or any(str(x).strip() == lbl for x in top_labels)",
        f"{body_indent}    if top_is_available:",
        f"{body_indent}        iri = top_iri",
        f"{body_indent}    else:",
        f"{body_indent}        iri = _mint_hash_iri('{top_class_local}')",
        f"{body_indent}        created = True",
        f"{body_indent}    g.add((iri, RDF.type, {type_expr}))",
    ]
    patched_lines = lines[: with_idx + 1] + patched_block + lines[label_idx:]
    patched = "\n".join(patched_lines) + ("\n" if code.endswith("\n") else "")
    try:
        mod2 = ast.parse(patched)
        for node in mod2.body:
            if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                seg2 = ast.get_source_segment(patched, node) or ""
                seg2_new = re.sub(r"created\s*=\s*False([,)])", r"created=created\1", seg2)
                if seg2_new != seg2:
                    patched = patched.replace(seg2, seg2_new, 1)
                break
    except Exception:
        pass
    return patched


def _build_split_part_prompt(
    meta_prompt_filename: str,
    ontology_path: str,
    ontology_name: str,
    function_sigs_str: str,
    architecture_note: str,
) -> str:
    meta_prompt_template = load_meta_prompt(meta_prompt_filename)
    concise_structure = extract_concise_ontology_structure(ontology_path)

    # Small reference snippet (no sandbox).
    ref_main_snippet = (
        "from fastmcp import FastMCP\n"
        "\n"
        "mcp = FastMCP(\"<ontology_name>\")\n"
        "\n"
        "@mcp.prompt(name=\"instruction\")\n"
        "def instruction_prompt():\n"
        "    return INSTRUCTION_PROMPT\n"
        "\n"
        "@mcp.tool()\n"
        "def some_tool(...):\n"
        "    return _some_tool(...)\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    mcp.run(transport=\"stdio\")\n"
    )

    must_use = """
## Required imports (must appear near the top of the file)

```python
from fastmcp import FastMCP
from typing import Optional
```
""".strip()

    return _format_meta_prompt(
        meta_prompt_template,
        ontology_name=ontology_name,
        namespace_uri=concise_structure["namespace_uri"],
        reference_main_snippet=ref_main_snippet,
        function_signatures=function_sigs_str,
        architecture_note=architecture_note + "\n\n" + must_use,
    )


def _validate_imported_function_names_exist(code: str, owners: Dict[str, str], filename: str) -> tuple[bool, str]:
    """
    Ensure any imported underlying function names (e.g., create_*, add_*, check_*) actually exist.
    This prevents the recurring mismatch where the LLM invents names like `add_x_to_Y` that do not exist.
    """
    try:
        tree = ast.parse(code, filename=filename)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"

    expected_prefixes = (
        "create_",
        "add_",
        "check_existing_",
        "check_and_report_",
        "init_memory_wrapper",
        "export_memory_wrapper",
        "pipeline",
    )

    missing: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.name
                if name.startswith(expected_prefixes) and name not in owners:
                    missing.add(name)

    if missing:
        sample = ", ".join(sorted(missing)[:15])
        return False, f"{filename}: imports names not present in underlying scripts: {sample}"
    return True, ""


def _build_main_py_deterministic(
    *,
    ontology_name: str,
    checks_script_path: str,
    relationships_script_path: str,
    base_script_path: str,
    entity_script_paths: list[str],
    output_dir: str,
    meta_cfg: dict | None = None,
) -> str:
    """
    Deterministically build a runnable main.py that imports/wraps exactly what exists.
    This avoids LLM-induced mismatches between main.py and underlying scripts.
    """
    all_script_paths = [checks_script_path, relationships_script_path, base_script_path] + list(entity_script_paths)
    owners = _function_owner_map(all_script_paths)

    # Build tool list from AST-extracted signatures (public functions only)
    funcs: list[dict] = []
    funcs.extend(extract_functions_from_underlying(base_script_path))
    funcs.extend(extract_functions_from_underlying(checks_script_path))
    funcs.extend(extract_functions_from_underlying(relationships_script_path))
    for p in entity_script_paths:
        funcs.extend(extract_functions_from_underlying(p))

    # Keep only public-facing tools we want to expose.
    tool_names: list[str] = []
    for f in funcs:
        n = f["name"]
        if n.startswith("_"):
            continue
        if n in {"init_memory_wrapper", "export_memory_wrapper"}:
            tool_names.append(n)
            continue
        if n.startswith(("create_", "add_", "check_", "check_and_report_")) or n == "pipeline":
            tool_names.append(n)

    # De-dupe while preserving order
    seen: set[str] = set()
    tool_names = [n for n in tool_names if not (n in seen or seen.add(n))]

    # Group imports by module
    mod_to_names: Dict[str, List[str]] = {}
    for n in tool_names:
        owner = owners.get(n)
        if not owner:
            continue
        mod = Path(owner).with_suffix("").name
        mod_to_names.setdefault(mod, []).append(n)

    lines: list[str] = []
    lines.append("from typing import Optional")
    lines.append("from fastmcp import FastMCP")
    lines.append("")

    # Import all underlying functions as underscored aliases.
    for mod in sorted(mod_to_names.keys()):
        parts: list[str] = []
        for n in sorted(mod_to_names[mod]):
            parts.append(f"{n} as _{n}")
        joined = ", ".join(parts)
        lines.append(f"from .{mod} import {joined}")
    lines.append("")

    instruction_lines: list[str] = [
        f"You are operating a {ontology_name} FastMCP server for ontology-backed KG construction.",
        "Typical workflow: init_memory -> check_existing_* -> create_* -> add_* -> export_memory.",
    ]
    policy_block = _format_main_entity_runtime_policy_for_mcp_prompt(meta_cfg, ontology_name)
    if policy_block:
        sanitized_policy = str(policy_block).replace("`", "")
        for raw_line in sanitized_policy.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("## "):
                line = line[3:].strip()
            if line.startswith("- "):
                line = line[2:].strip()
            if line:
                instruction_lines.append(line)

    instruction_text = "\n".join(instruction_lines).strip() + "\n"
    lines.append(f"mcp = FastMCP({ontology_name!r})")
    lines.append("")
    lines.append(f"INSTRUCTION_PROMPT: str = {instruction_text!r}")
    lines.append("")
    lines.append("@mcp.prompt(name='instruction')")
    lines.append("def instruction() -> str:")
    lines.append("    return INSTRUCTION_PROMPT")
    lines.append("")

    # Memory tools (normalize wrapper names)
    if "init_memory_wrapper" in tool_names:
        lines.append("@mcp.tool()")
        lines.append("def init_memory(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:")
        lines.append("    return _init_memory_wrapper(doi=doi, top_level_entity_name=top_level_entity_name)")
        lines.append("")
    if "export_memory_wrapper" in tool_names:
        lines.append("@mcp.tool()")
        lines.append("def export_memory() -> str:")
        lines.append("    return _export_memory_wrapper()")
        lines.append("")

    # Other tools: wrapper name == underlying name
    for n in tool_names:
        if n in {"init_memory_wrapper", "export_memory_wrapper"}:
            continue
        # Use exact signature from extracted signature if available.
        sig = next((f["signature"] for f in funcs if f["name"] == n), None)
        if not sig or not sig.startswith("def "):
            # Fallback: simplest wrapper
            lines.append("@mcp.tool()")
            lines.append(f"def {n}(*args, **kwargs) -> str:")
            lines.append(f"    return _{n}(*args, **kwargs)")
            lines.append("")
            continue

        # Convert "def name(...):" -> "def name(...):" wrapper (keep params/return hints)
        # but ensure return type -> str if present in signature text.
        header = sig.strip()
        # Ensure function name matches n (defensive)
        header = re.sub(r"^def\s+\w+\s*\(", f"def {n}(", header)
        lines.append("@mcp.tool()")
        lines.append(header)
        lines.append(f"    return _{n}(")
        # Pass-through by keyword for explicit args (best effort via AST)
        try:
            tree = ast.parse(sig + "\n    pass\n")
            fn = next((x for x in tree.body if isinstance(x, ast.FunctionDef)), None)
            arg_names: list[str] = []
            if fn:
                arg_names.extend([a.arg for a in fn.args.args])
                arg_names.extend([a.arg for a in fn.args.kwonlyargs])
            for a in arg_names:
                if a == "self":
                    continue
                lines.append(f"        {a}={a},")
        except Exception:
            lines.append("        # NOTE: failed to introspect args; calling without keyword mapping")
        lines.append("    )")
        lines.append("")

    lines.append("if __name__ == '__main__':")
    lines.append("    mcp.run(transport='stdio')")
    lines.append("")

    out_path = Path(output_dir) / "main.py"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


async def generate_split_main_scripts_direct(
    ontology_path: str,
    ontology_name: str,
    checks_script_path: str,
    relationships_script_path: str,
    base_script_path: str,
    entity_script_paths: list,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 1,
    meta_cfg: dict | None = None,
) -> str:
    """
    Two-step "divide & conquer" main generation:
      1) LLM generates two FRAGMENTS (core + relationships), each wrapping a subset of tools.
      2) LLM stitches those fragments into one runnable `main.py`.
    """
    policy_block = _format_main_entity_runtime_policy_for_mcp_prompt(meta_cfg, ontology_name)
    # Build function inventories from real scripts (AST-based).
    all_script_paths = [checks_script_path, relationships_script_path, base_script_path] + entity_script_paths
    owners = _function_owner_map(all_script_paths)
    doc_map = _extract_firstline_docstrings_from_scripts(all_script_paths)
    tbox_class_comments, tbox_prop_comments = _extract_tbox_comment_maps(ontology_path)
    # Persist a small summary for debugging without spamming console output.
    try:
        summary_path = Path(output_dir) / "tbox_comment_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "ontology": ontology_name,
                    "class_comment_count": len(tbox_class_comments),
                    "property_comment_count": len(tbox_prop_comments),
                    "sample_class_keys": sorted(list(tbox_class_comments.keys()))[:20],
                    "sample_property_keys": sorted(list(tbox_prop_comments.keys()))[:20],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    # Build per-part signature inventories (concise).
    funcs: list[dict] = []
    funcs.extend(extract_functions_from_underlying(base_script_path))
    funcs.extend(extract_functions_from_underlying(checks_script_path))
    funcs.extend(extract_functions_from_underlying(relationships_script_path))
    for p in entity_script_paths:
        funcs.extend(extract_functions_from_underlying(p))

    # De-dupe by name.
    seen: set[str] = set()
    uniq: list[dict] = []
    for f in funcs:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        uniq.append(f)

    core = [
        f for f in uniq
        if (
            f["name"] in {"init_memory_wrapper", "export_memory_wrapper"}
            or f["name"].startswith("check_")
            or f["name"].startswith("create_")
            or f["name"].startswith("update_")
        )
    ]
    rel = [f for f in uniq if (f["name"].startswith("add_") or f["name"] in {"add_relation", "list_relation_properties"})]

    def _fmt_sig_list(items: list[dict]) -> str:
        lines = [
            f"Total functions: {len(items)}",
            "NOTE: Function bodies are intentionally omitted.",
            "",
        ]
        for it in items:
            sig = it["signature"]
            name = it["name"]
            hint = doc_map.get(name, "")
            tbox_hint = _tbox_hint_for_tool(name, tbox_class_comments, tbox_prop_comments)
            if hint:
                if tbox_hint:
                    lines.append(f"- {sig}  # doc: {hint} | tbox: {tbox_hint}")
                else:
                    lines.append(f"- {sig}  # doc: {hint}")
            else:
                if tbox_hint:
                    lines.append(f"- {sig}  # tbox: {tbox_hint}")
                else:
                    lines.append(f"- {sig}")
        return "\n".join(lines).strip()

    # Architecture note used by both parts.
    checks_mod = Path(checks_script_path).with_suffix("").name
    rel_mod = Path(relationships_script_path).with_suffix("").name
    base_mod = Path(base_script_path).with_suffix("").name
    ent_mods = [Path(p).with_suffix("").name for p in entity_script_paths]
    architecture_note = (
        "**ARCHITECTURE: TWO-STEP MAIN GENERATION (FRAGMENTS -> STITCHED MAIN)**\n"
        "- Step 1 outputs: `main_part_core.py`, `main_part_relationships.py`\n"
        "- Step 2 output: `main.py` (single runnable FastMCP server)\n"
        "\n"
        "**REAL MODULES (do NOT use placeholders like `.module`)**\n"
        f"- Base: `.{base_mod}`\n"
        f"- Checks: `.{checks_mod}`\n"
        f"- Relationships: `.{rel_mod}`\n"
        f"- Entities: {', '.join('`.' + m + '`' for m in ent_mods)}\n"
    )
    if policy_block:
        architecture_note = architecture_note + "\n" + policy_block

    # If openai is not installed, fall back to deterministic main.py generation (alignment-first).
    try:
        client = create_openai_client()
    except Exception:
        return _build_main_py_deterministic(
            ontology_name=ontology_name,
            checks_script_path=checks_script_path,
            relationships_script_path=relationships_script_path,
            base_script_path=base_script_path,
            entity_script_paths=[str(p) for p in entity_script_paths],
            output_dir=output_dir,
        )

    def _validate_no_server_bootstrap(code: str, filename: str) -> tuple[bool, str]:
        lowered = code.lower()
        bad_markers = ["fastmcp(", "mcp.run(", "if __name__"]
        for m in bad_markers:
            if m in lowered:
                return False, f"{filename}: fragment must not include server bootstrap (`{m}` found)"
        return True, ""

    async def _gen_part(part_name: str, meta_prompt: str, sigs: str, out_filename: str) -> str:
        base_prompt = _build_split_part_prompt(meta_prompt, ontology_path, ontology_name, sigs, architecture_note)

        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                prompt = base_prompt
                if attempt > 1 and last_exc is not None:
                    prompt += (
                        "\n\n"
                        "## FIX THE PREVIOUS FAILURE\n"
                        f"The previous attempt failed with this error:\n{last_exc}\n\n"
                        "Regenerate the fragment with correct Python syntax. Common pitfall: multiline imports must be properly closed.\n"
                    )

                prompt_path = Path(output_dir) / f"{out_filename}.prompt_attempt_{attempt}.md"
                prompt_path.parent.mkdir(parents=True, exist_ok=True)
                prompt_path.write_text(prompt, encoding="utf-8")

                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You are an expert in FastMCP module development."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=_get_temperature_for_model(model_name),
                    **_token_limit_kwargs(model_name, 16000),
                )
                code = extract_code_from_response(resp.choices[0].message.content or "")
                if not code:
                    raise ValueError("LLM returned empty response")

                # Deterministically fix import ownership.
                code = _rewrite_main_relative_imports(code, owners)
                code = _strip_placeholder_module_imports(code)
                ok_imp, imp_err = _validate_imported_function_names_exist(code, owners, out_filename)
                if not ok_imp:
                    last_exc = ValueError(imp_err)
                    if attempt == max_retries:
                        break
                    continue

                # Always persist raw attempt for debugging (even if invalid).
                raw_attempt_path = Path(output_dir) / f"{Path(out_filename).stem}_attempt_{attempt}.py"
                raw_attempt_path.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")

                # Step-1 fragments are intentionally "half-finished": they do NOT need to be importable.
                # We only enforce that they don't include server bootstrap (to avoid duplicated definitions)
                # and we ensure placeholder relative imports are removed via _rewrite_main_relative_imports.
                ok_frag, frag_err = _validate_no_server_bootstrap(code, out_filename)
                if not ok_frag:
                    last_exc = ValueError(frag_err)
                    if attempt == max_retries:
                        break
                    continue

                out_path = Path(output_dir) / out_filename
                out_path.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")
                return str(out_path)
            except Exception as e:
                last_exc = e
                if attempt == max_retries:
                    break
        raise Exception(f"Failed to generate {part_name}: {last_exc}")

    # Step 1: Generate two fragments.
    part_core_path = await _gen_part("core", "direct_main_part_core_fragment_prompt.md", _fmt_sig_list(core), "main_part_core.py")
    part_rel_path = await _gen_part("relationships", "direct_main_part_relationships_fragment_prompt.md", _fmt_sig_list(rel), "main_part_relationships.py")

    def _validate_called_underscored_names_are_imported(code: str, filename: str) -> tuple[bool, str]:
        """
        Ensure every called name like `_foo(...)` is actually imported/defined in the module.
        This catches typos like `_create_Separete` early.
        """
        try:
            tree = ast.parse(code, filename=filename)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        imported_or_defined: set[str] = set()
        called: set[str] = set()

        class V(ast.NodeVisitor):
            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                for alias in node.names:
                    imported_or_defined.add(alias.asname or alias.name)
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                imported_or_defined.add(node.name)
                self.generic_visit(node)
            def visit_Assign(self, node: ast.Assign) -> None:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        imported_or_defined.add(t.id)
                self.generic_visit(node)
            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id.startswith("_"):
                    called.add(node.func.id)
                self.generic_visit(node)

        V().visit(tree)
        missing = sorted([n for n in called if n not in imported_or_defined])
        if missing:
            return False, f"{filename}: called underscored names not imported/defined: {', '.join(missing[:10])}"
        return True, ""

    # Step 2: Stitch fragments into final main.py via LLM (fragments are the ONLY wrapper inputs).
    stitch_template = load_meta_prompt("direct_main_stitch_prompt.md")
    part_core_code = Path(part_core_path).read_text(encoding="utf-8")
    part_rel_code = Path(part_rel_path).read_text(encoding="utf-8")
    last_stitch_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        stitch_prompt = _format_meta_prompt(
            stitch_template,
            ontology_name=ontology_name,
            part_core_code=part_core_code,
            part_relationships_code=part_rel_code,
        )
        if policy_block:
            stitch_prompt += "\n\n" + policy_block
        if attempt > 1 and last_stitch_exc is not None:
            stitch_prompt += (
                "\n\n"
                "## FIX THE PREVIOUS FAILURE\n"
                f"The previous attempt failed with:\n{last_stitch_exc}\n\n"
                "Regenerate a correct, runnable `main.py`.\n"
            )

        stitch_prompt_path = Path(output_dir) / f"main_stitch_prompt_attempt_{attempt}.md"
        stitch_prompt_path.write_text(stitch_prompt, encoding="utf-8")

        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert in FastMCP server development. Produce a complete runnable main.py by stitching provided fragments."},
                    {"role": "user", "content": stitch_prompt},
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000),
            )
            main_code = extract_code_from_response(resp.choices[0].message.content or "")
            if not main_code:
                raise ValueError("LLM returned empty stitched main.py")

            # Deterministically fix import ownership in final main.py too (also strips bogus relative imports).
            main_code = _rewrite_main_relative_imports(main_code, owners)
            main_code = _strip_placeholder_module_imports(main_code)
            main_code = _patch_fastmcp_instruction_compat(main_code)
            main_code = _ensure_mcp_tool_docstrings_with_tbox(main_code, doc_map, tbox_class_comments, tbox_prop_comments)
            # Fix and validate wrapper forwarding to avoid NameError-inducing typos.
            main_code = _rewrite_main_wrapper_forwarding_param_typos(main_code)
            main_code = _fix_reversed_underscored_alias_assignments(main_code)

            ok, err = validate_python_syntax(main_code, "main.py")
            if not ok:
                raise ValueError(f"Stitched main.py syntax: {err}")
            ok_imp, imp_err = _validate_imported_function_names_exist(main_code, owners, "main.py")
            if not ok_imp:
                raise ValueError(imp_err)
            ok_alias, alias_err = _validate_underscored_alias_calls(main_code)
            if not ok_alias:
                raise ValueError(alias_err)
            ok_calls, call_err = _validate_called_underscored_names_are_imported(main_code, "main.py")
            if not ok_calls:
                raise ValueError(call_err)
            ok_fw, fw_err = _validate_main_wrapper_forwarding_uses_defined_params(main_code, "main.py")
            if not ok_fw:
                raise ValueError(fw_err)

            out_path = Path(output_dir) / "main.py"
            out_path.write_text(main_code + ("\n" if not main_code.endswith("\n") else ""), encoding="utf-8")
            return str(out_path)
        except Exception as e:
            last_stitch_exc = e
            if attempt == max_retries:
                break

    raise Exception(f"Failed to stitch main.py: {last_stitch_exc}")

def validate_python_syntax(code: str, filepath: str = "<generated>") -> tuple[bool, str]:
    """
    Validate Python code syntax by attempting to compile it.

    Returns:
        (is_valid, error_message)
    """
    try:
        compile(code, filepath, 'exec')
        return True, ""
    except SyntaxError as e:
        error_msg = f"Syntax error at line {e.lineno}: {e.msg}"
        if e.text:
            error_msg += f"\n  {e.text.strip()}"
            if e.offset:
                error_msg += f"\n  {' ' * (e.offset - 1)}^"
        return False, error_msg
    except Exception as e:
        return False, f"Compilation error: {str(e)}"


def _normalize_python_name_literals(code: str) -> str:
    """
    Normalize common lower-case Python name literals emitted by LLMs.

    This is token-based so it only rewrites actual NAME tokens and leaves quoted
    strings / comments untouched.
    """
    replacements = {
        "none": "None",
        "true": "True",
        "false": "False",
    }
    try:
        tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.NAME and tok.string in replacements:
                tok = tokenize.TokenInfo(tok.type, replacements[tok.string], tok.start, tok.end, tok.line)
            tokens.append(tok)
        return tokenize.untokenize(tokens)
    except Exception:
        return code


# ============================================================================
# OM-2 unit enforcement guardrails (generator-level, domain-agnostic)
# ============================================================================

_OM2_HELPERS_CONTRACT = """
## OM-2 UNIT HANDLING CONTRACT (STRICT; DOMAIN-AGNOSTIC)

If the ontology-derived input includes an OM-2 unit inventory:

### Base module MUST implement these helpers (single source of truth)
- `OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")`
- `OM2_UNIT_MAP: Dict[str, URIRef]`
  - keys: **unit labels** (normalized, e.g. lowercased and stripped)
  - values: OM-2 unit IRIs (e.g., `OM2.degreeCelsius`)
  - MUST be derived ONLY from the provided ontology-derived unit inventory (do not invent units)

- `def _resolve_om2_unit(unit_label: str) -> URIRef`
  - validates `unit_label` against `OM2_UNIT_MAP`
  - raises `ValueError` with a message that includes the allowed labels if unknown

- `def _find_or_create_om2_quantity(g: Graph, *, quantity_class: URIRef, label: str, value: Union[int,float,str], unit_label: str) -> URIRef`
  - NOTE: `quantity_class`, `label`, `value`, `unit_label` are **keyword-only** parameters (enforced by `*`)
  - validates unit_label via `_resolve_om2_unit`
  - reuses existing quantity instances when `(rdf:type, numerical value, unit)` match
  - when creating, sets exactly one `om2:hasNumericalValue` (XSD.double) and one `om2:hasUnit` (unit IRI)

### Entity modules MUST call the helper in this exact style
- DO NOT pass unit IRIs around; always pass the **unit label string** into `unit_label=...`
- DO NOT call `_find_or_create_om2_quantity` with positional arguments except the first positional graph `g`

Example (correct):
```python
q = _find_or_create_om2_quantity(
    g,
    quantity_class=OM2.Temperature,
    label="target temperature",
    value=150,
    unit_label=unit,  # unit is a label string like "degree celsius"
)
```
"""


def _ontology_has_om2_unit_inventory(ontology_path: str) -> bool:
    try:
        cs = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
        om2_units = cs.get("om2_units") or {}
        if not isinstance(om2_units, dict):
            return False
        return any(bool(v) for v in om2_units.values())
    except Exception:
        return False


def _ontology_uses_om2_units(ontology_path: str, ontology_name: str | None = None) -> bool:
    """
    Decide whether to enable OM-2 unit helpers using ontology-derived evidence only.

    This function must remain domain-agnostic:
    - no hardcoded ontology names
    - no domain-specific allow/deny lists
    - behavior is derived solely from the parsed ontology / OM-2 unit inventory
    """
    return _ontology_has_om2_unit_inventory(ontology_path)


def _validate_om2_base_contract(code: str) -> tuple[bool, str]:
    """
    Generator-level check: ensure generated base script exposes a stable OM-2 helper API
    so downstream entity scripts can call it deterministically.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Cannot validate OM-2 contract due to syntax error: {e}"

    has_resolve = False
    has_find_or_create = False
    contract_errors: list[str] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_resolve_om2_unit":
            has_resolve = True
        if node.name == "_find_or_create_om2_quantity":
            has_find_or_create = True
            # Expect at least one positional arg (Graph) and kwonly args for the contract fields
            kwonly = [a.arg for a in node.args.kwonlyargs]
            required_kwonly = ["quantity_class", "label", "value", "unit_label"]
            missing = [x for x in required_kwonly if x not in kwonly]
            if missing:
                contract_errors.append(
                    "_find_or_create_om2_quantity must define keyword-only args "
                    f"{required_kwonly}; missing: {missing}"
                )

            # Also require a '*' marker (kwonlyargs present is a proxy; but enforce no accidental positional
            # params for contract fields by checking regular args names)
            reg_args = [a.arg for a in node.args.args]
            forbidden_positional = [x for x in required_kwonly if x in reg_args]
            if forbidden_positional:
                contract_errors.append(
                    "_find_or_create_om2_quantity must not accept contract fields positionally; "
                    f"found as positional args: {forbidden_positional}"
                )

    if not has_resolve:
        contract_errors.append("Missing _resolve_om2_unit(unit_label: str) helper in base script.")
    if not has_find_or_create:
        contract_errors.append("Missing _find_or_create_om2_quantity(...) helper in base script.")

    if contract_errors:
        return False, " | ".join(contract_errors)
    return True, ""


def _validate_om2_entity_call_style(code: str) -> tuple[bool, str]:
    """
    Generator-level check: ensure entity scripts call _find_or_create_om2_quantity
    using the contract style (no positional args beyond graph; unit_label keyword used).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Cannot validate OM-2 call style due to syntax error: {e}"

    violations: list[str] = []

    class _V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Only handle direct calls by name: _find_or_create_om2_quantity(...)
            fn_name = None
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            if fn_name == "_find_or_create_om2_quantity":
                # Allow at most one positional arg: the graph `g`
                if len(node.args) > 1:
                    violations.append(
                        f"_find_or_create_om2_quantity called with {len(node.args)} positional args; "
                        "only the first (graph) may be positional."
                    )

                kw_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                if "unit_label" not in kw_names:
                    violations.append(
                        "_find_or_create_om2_quantity call missing required keyword 'unit_label' "
                        "(unit label string must be provided)."
                    )
                if "unit_iri" in kw_names or "unit" in kw_names:
                    # 'unit' should be provided as unit_label=unit, not unit=...
                    violations.append(
                        "_find_or_create_om2_quantity must be called with keyword 'unit_label=...'; "
                        "do not pass unit IRIs or use unit=..."
                    )
            self.generic_visit(node)

    _V().visit(tree)

    if violations:
        return False, " | ".join(violations)
    return True, ""


def _validate_resolve_om2_unit_call_style(code: str) -> tuple[bool, str]:
    """
    Generator-level check: ensure entity scripts call _resolve_om2_unit with the base-script signature:
      _resolve_om2_unit(unit_label: str) -> URIRef

    In particular, do NOT allow `_resolve_om2_unit(g, unit_label)` (Graph must not be passed).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Cannot validate _resolve_om2_unit call style due to syntax error: {e}"

    violations: list[str] = []

    class _V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            fn_name = None
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id

            if fn_name == "_resolve_om2_unit":
                # Allowed forms:
                #  - _resolve_om2_unit(unit_label)
                #  - _resolve_om2_unit(unit_label=unit_label)
                if len(node.args) > 1:
                    violations.append(
                        f"_resolve_om2_unit called with {len(node.args)} positional args; "
                        "it must be called with exactly one argument: unit_label (string)."
                    )
                if len(node.args) == 1 and node.keywords:
                    violations.append(
                        "_resolve_om2_unit should not mix positional and keyword args; use one or the other."
                    )
                if len(node.args) == 0:
                    kw_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                    if "unit_label" not in kw_names:
                        violations.append(
                            "_resolve_om2_unit must be called as _resolve_om2_unit(unit_label) or "
                            "_resolve_om2_unit(unit_label=...)."
                        )
                    # If they pass unit=... we also flag (common confusion).
                    if "unit" in kw_names:
                        violations.append(
                            "_resolve_om2_unit does not accept unit=...; use unit_label=... (string label)."
                        )
            self.generic_visit(node)

    _V().visit(tree)
    if violations:
        return False, " | ".join(violations)
    return True, ""


# Static list of available functions in universal_utils.py
# This list is maintained manually to match sandbox/code/universal_utils.py
UNIVERSAL_UTILS_FUNCTIONS = [
    'locked_graph',
    'init_memory',
    'export_memory',
    '_mint_hash_iri',
    '_iri_exists',
    '_find_by_type_and_label',
    '_get_label',
    '_set_single_label',
    '_ensure_type_with_label',
    '_require_existing',
    '_sanitize_label',
    '_format_success',
    '_list_instances_with_label',
    '_to_pos_int',
    '_export_snapshot_silent',
    'get_memory_paths',
    'inspect_memory',
]


def create_openai_client():
    """
    Create and return an OpenAI client using the same pattern as LLMCreator.
    Uses REMOTE_API_KEY/REMOTE_BASE_URL primarily, with fallbacks for common repo env keys.
    """
    if OpenAI is None:
        raise ModuleNotFoundError(
            "Python package 'openai' is not installed. Install it to use direct LLM generation.\n"
            "Example: pip install openai"
        )
    load_dotenv(override=True)

    api_key = (
        os.getenv("REMOTE_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = (
        os.getenv("REMOTE_BASE_URL")
        or os.getenv("BASE_URL")
    )

    if not api_key:
        raise ValueError(
            "No API key found in environment variables. "
            "Set one of: REMOTE_API_KEY, API_KEY, or OPENAI_API_KEY."
        )

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)  # type: ignore[misc]
    else:
        return OpenAI(api_key=api_key)  # type: ignore[misc]


def load_meta_prompt(prompt_name: str) -> str:
    """
    Load a meta-prompt from ape_generated_contents/meta_prompts/mcp_scripts/.

    Args:
        prompt_name: Name of the prompt file (e.g., 'direct_underlying_script_prompt.md')

    Returns:
        Content of the meta-prompt as a string
    """
    meta_prompt_path = project_root / "ape_generated_contents" / "meta_prompts" / "mcp_scripts" / prompt_name

    if not meta_prompt_path.exists():
        raise FileNotFoundError(f"Meta-prompt not found: {meta_prompt_path}")

    with open(meta_prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


class _SafeFormatDict(dict):
    """
    Safe formatter mapping for meta-prompts.

    If a template contains extra `{like_this}` fields (often from code examples),
    normal `str.format(...)` will raise KeyError. This mapping leaves unknown
    fields untouched so generation can proceed.
    """

    def __missing__(self, key: str) -> str:  # pragma: no cover
        return "{" + str(key) + "}"


def _format_meta_prompt(template: str, **kwargs) -> str:
    """
    Format a meta-prompt template without crashing on stray `{...}` fields.

    This is intentionally more forgiving than `template.format(**kwargs)` because
    meta-prompts often embed Python examples containing braces.
    """
    import string

    # Optional: warn once per call if template contains fields not provided.
    try:
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(template)
            if field_name
        }
        missing = sorted([f for f in fields if f not in kwargs])
        if missing:
            preview = ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
            print(
                f"   ⚠️  Meta-prompt contains unfilled fields ({len(missing)}): {preview}. "
                "Leaving them as literals; if unintended, escape braces as `{{...}}`."
            )
    except Exception:
        # Never fail formatting due to warning logic.
        pass

    return template.format_map(_SafeFormatDict(**kwargs))


def _split_list(items: list[str], chunk_size: int) -> list[list[str]]:
    if chunk_size <= 0:
        return [items]
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _extract_header_before_first_def(code: str) -> str:
    """
    Take everything before the first top-level decorator/def.
    This preserves shebang, module docstring, imports, and module-level constants.
    """
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("@") or line.startswith("def ") or line.startswith("async def "):
            return "\n".join(lines[:i]).rstrip() + "\n"
    return code.rstrip() + "\n"


def _extract_function_blocks(code: str) -> list[tuple[str, str]]:
    """
    Return a list of (function_name, source_block) for top-level functions in code.
    Includes decorators.
    """
    import ast

    lines = code.splitlines(True)
    mod = ast.parse(code)
    out: list[tuple[str, str]] = []
    for node in mod.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        start = node.lineno
        if node.decorator_list:
            start = min([d.lineno for d in node.decorator_list] + [start])
        end = getattr(node, "end_lineno", None)
        if end is None:
            # Fallback: best-effort slice.
            end = node.lineno
        block = "".join(lines[start - 1 : end]).rstrip() + "\n"
        out.append((node.name, block))
    return out


def _merge_relationship_parts(part_codes: list[str]) -> str:
    """
    Deterministically merge multiple valid relationship modules into one:
    - Keep header (docstring/imports/constants) from the first part.
    - Collect top-level functions from all parts (dedupe by name, keep first occurrence).
    - Order: private helpers (name startswith '_') first, then remaining in first-seen order.
    """
    if not part_codes:
        raise ValueError("No relationship parts to merge")

    header = _extract_header_before_first_def(part_codes[0])
    seen: dict[str, str] = {}
    order: list[str] = []
    for code in part_codes:
        for name, block in _extract_function_blocks(code):
            if name in seen:
                continue
            seen[name] = block
            order.append(name)

    private = [n for n in order if n.startswith("_")]
    public = [n for n in order if not n.startswith("_")]
    merged_funcs = "\n\n".join([seen[n].rstrip() for n in (private + public)]).rstrip() + "\n"
    return (header.rstrip() + "\n\n" + merged_funcs).lstrip()


def _format_relationships_prompt_subset(
    *,
    meta_prompt_template: str,
    ontology_name: str,
    namespace_uri: str,
    object_props_subset: list[dict],
) -> str:
    """
    Build a smaller prompt that includes only a subset of object properties.
    """
    subset_lines: list[str] = []
    subset_lines.append(f"Namespace: {namespace_uri}")
    subset_lines.append("")
    subset_lines.append("# Object properties (subset; generate ONLY these add_* functions)")
    for p in object_props_subset:
        name = p.get("name")
        dom = ", ".join(p.get("domains") or []) or "(unknown)"
        rng = ", ".join(p.get("ranges") or []) or "(unknown)"
        subset_lines.append(f"- {name}: {dom} -> {rng}")
    subset_lines.append("")
    subset_lines.append(
        "CRITICAL PARTIAL GENERATION RULES:\n"
        "- Generate add_* functions ONLY for the object properties listed above.\n"
        "- Do NOT generate add_* functions for any other properties.\n"
        "- Output MUST be plain Python code (NO markdown fences like ```python).\n"
        "- The file MUST compile.\n"
    )

    return _format_meta_prompt(meta_prompt_template, ontology_name=ontology_name) + "\n\n" + "\n".join(subset_lines)


def parse_ttl_tbox(ontology_path: str) -> Dict[str, any]:
    """
    Parse T-Box ontology TTL to extract entity classes, properties, and relationships.

    Returns:
        Dictionary with:
        - namespace_uri: Base namespace URI
        - classes: List of OWL classes (local names)
        - object_properties: List of object properties with domain/range
        - datatype_properties: List of datatype properties with domain/range
        - class_hierarchy: Parent-child relationships
    """
    g = Graph()
    g.parse(ontology_path, format='turtle')

    # Find the main namespace (usually the one with most classes)
    namespaces = {str(ns): prefix for prefix, ns in g.namespaces()}

    ontology_ns = None
    max_classes = 0
    for ns_uri in namespaces.keys():
        if ns_uri in [str(RDF), str(RDFS), str(OWL), 'http://www.w3.org/XML/1998/namespace']:
            continue
        count = len([c for c in g.subjects(RDF.type, OWL.Class) if str(c).startswith(str(ns_uri))])
        if count > max_classes:
            max_classes = count
            ontology_ns = ns_uri

    if ontology_ns is None:
        # Fallback: use first non-standard namespace
        for ns_uri in namespaces.keys():
            if ns_uri not in [str(RDF), str(RDFS), str(OWL)]:
                ontology_ns = ns_uri
                break

    # Extract classes
    classes = []
    for cls in g.subjects(RDF.type, OWL.Class):
        if str(cls).startswith(str(ontology_ns)):
            local_name = str(cls).replace(str(ontology_ns), '')
            classes.append(local_name)

    # Extract object properties
    object_properties = []
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        if str(prop).startswith(str(ontology_ns)):
            local_name = str(prop).replace(str(ontology_ns), '')

            # Get domain and range
            domains = [str(d).replace(str(ontology_ns), '') for d in g.objects(prop, RDFS.domain)]
            ranges = [str(r).replace(str(ontology_ns), '') for r in g.objects(prop, RDFS.range)]

            object_properties.append({
                'name': local_name,
                'domains': domains,
                'ranges': ranges
            })

    # Extract datatype properties
    datatype_properties = []
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        if str(prop).startswith(str(ontology_ns)):
            local_name = str(prop).replace(str(ontology_ns), '')

            # Get domain
            domains = [str(d).replace(str(ontology_ns), '') for d in g.objects(prop, RDFS.domain)]

            datatype_properties.append({
                'name': local_name,
                'domains': domains
            })

    # Extract class hierarchy
    class_hierarchy = {}
    for cls in g.subjects(RDF.type, OWL.Class):
        if str(cls).startswith(str(ontology_ns)):
            local_name = str(cls).replace(str(ontology_ns), '')
            parents = []
            for parent in g.objects(cls, RDFS.subClassOf):
                if str(parent).startswith(str(ontology_ns)):
                    parent_name = str(parent).replace(str(ontology_ns), '')
                    parents.append(parent_name)
            if parents:
                class_hierarchy[local_name] = parents

    return {
        'namespace_uri': ontology_ns,
        'classes': sorted(classes),
        'object_properties': object_properties,
        'datatype_properties': datatype_properties,
        'class_hierarchy': class_hierarchy
    }


def _extract_om2_unit_inventory(om2_ttl_path: str) -> Dict[str, List[Dict[str, str]]]:
    """
    Extract a small, deterministic inventory of OM-2 units from a (mock) T-Box.

    Returns dict like:
      {
        "TemperatureUnit": [{"label": "...", "iri": "om2:degreeCelsius", "full_iri": "http://.../degreeCelsius"}, ...],
        ...
      }
    """
    from rdflib.namespace import RDF, RDFS

    g = Graph()
    g.parse(om2_ttl_path, format="turtle")

    OM2_NS = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
    categories = [
        "TemperatureUnit",
        "PressureUnit",
        "DurationUnit",
        "VolumeUnit",
        "TemperatureRateUnit",
        "AmountFractionUnit",
    ]

    def _local(uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[-1]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    out: Dict[str, List[Dict[str, str]]] = {c: [] for c in categories}

    for s in set(g.subjects()):
        s_str = str(s)
        if not s_str.startswith(OM2_NS):
            continue
        types = {str(o) for o in g.objects(s, RDF.type)}
        for cat in categories:
            if f"{OM2_NS}{cat}" in types:
                label = None
                for l in g.objects(s, RDFS.label):
                    label = str(l)
                    break
                label = (label or _local(s_str)).strip()
                term = _local(s_str)
                out[cat].append(
                    {
                        "label": label,
                        "iri": f"om2:{term}",
                        "full_iri": s_str,
                    }
                )

    # Stable ordering + de-dupe
    for cat in out:
        uniq = {}
        for item in out[cat]:
            uniq[(item["label"].casefold(), item["iri"])] = item
        out[cat] = sorted(uniq.values(), key=lambda d: (d["label"].casefold(), d["iri"]))
    return out


def extract_concise_ontology_structure(ontology_path: str, *, include_om2_mock: bool = True) -> Dict[str, any]:
    """
    Extract a concise, focused structure from TTL ontology.

    Focus on:
    1. Class connections (object properties connecting classes)
    2. Class inputs (datatype properties for each class)

    Excludes:
    - rdfs:comment (verbose descriptions)
    - rdfs:label (human-readable labels)
    - Other metadata

    Returns:
        Dictionary with:
        - namespace_uri: Base namespace URI
        - classes: List of class names
        - class_structures: For each class, its connections and inputs
    """
    # Parse the main ontology first to determine its namespace robustly.
    # IMPORTANT: keep this graph OM-2-free so namespace selection is stable.
    g_main = Graph()
    g_main.parse(ontology_path, format="turtle")

    # Work graph: copy the main ontology triples, then optionally add OM-2 mock.
    # NOTE: do NOT alias `g` to `g_main` (parsing OM-2 would mutate g_main and break namespace selection).
    g = Graph()
    for prefix, ns in g_main.namespaces():
        g.bind(prefix, ns)
    g += g_main

    # Optionally load OM-2 mock alongside the main ontology so external ranges (om-2) resolve.
    # IMPORTANT: OM-2 must NOT influence main ontology namespace selection (computed from g_main only).
    om2_units = None
    if include_om2_mock:
        om2_mock_path = Path("data/ontologies/om2_mock.ttl")
        if om2_mock_path.exists():
            try:
                g.parse(str(om2_mock_path), format="turtle")
                om2_units = _extract_om2_unit_inventory(str(om2_mock_path))
            except Exception:
                om2_units = None

    # Find the main namespace
    namespaces = {str(ns): prefix for prefix, ns in g_main.namespaces()}
    ontology_ns: str | None = None
    max_classes = 0
    for ns_uri in namespaces.keys():
        if ns_uri in [str(RDF), str(RDFS), str(OWL), 'http://www.w3.org/XML/1998/namespace']:
            continue
        count = len([c for c in g_main.subjects(RDF.type, OWL.Class) if str(c).startswith(str(ns_uri))])
        # Prefer namespaces with more classes; break ties by preferring more-specific (longer) namespaces.
        if (count > max_classes) or (
            count == max_classes and count > 0 and ontology_ns is not None and len(ns_uri) > len(ontology_ns)
        ):
            max_classes = count
            ontology_ns = ns_uri

    if ontology_ns is None:
        for ns_uri in namespaces.keys():
            if ns_uri not in [str(RDF), str(RDFS), str(OWL)]:
                ontology_ns = ns_uri
                break

    def extract_classes_from_domain(domain_node):
        """Helper to extract classes from domain (handles union domains)."""
        classes_in_domain = []

        # Check if it's a direct class
        if str(domain_node).startswith(str(ontology_ns)):
            classes_in_domain.append(str(domain_node).replace(str(ontology_ns), ''))
        # Check if it's a blank node with unionOf
        elif isinstance(domain_node, URIRef) or (domain_node, RDF.type, OWL.Class) in g:
            # Check for unionOf
            for union_list in g.objects(domain_node, OWL.unionOf):
                # Iterate through the RDF collection
                current = union_list
                while current and current != RDF.nil:
                    first = g.value(current, RDF.first)
                    if first and str(first).startswith(str(ontology_ns)):
                        classes_in_domain.append(str(first).replace(str(ontology_ns), ''))
                    current = g.value(current, RDF.rest)

        return classes_in_domain

    # Extract all classes
    classes = []
    for cls in g.subjects(RDF.type, OWL.Class):
        if str(cls).startswith(str(ontology_ns)):
            local_name = str(cls).replace(str(ontology_ns), '')
            classes.append(local_name)

    # Build class structures
    class_structures = {}
    # Track external range IRIs so summaries can reflect referenced ontologies (e.g., om-2:Temperature)
    external_range_iris: Dict[str, str] = {}

    for class_name in classes:
        class_uri = URIRef(ontology_ns + class_name)

        # Find object properties where this class is the DOMAIN (what this class connects TO)
        connects_to = []
        for prop in g.subjects(RDF.type, OWL.ObjectProperty):
            if str(prop).startswith(str(ontology_ns)):
                for domain in g.objects(prop, RDFS.domain):
                    # Extract all classes from domain (handles unions)
                    domain_classes = extract_classes_from_domain(domain)
                    if class_name in domain_classes:
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        ranges = [str(r).replace(str(ontology_ns), '') for r in g.objects(prop, RDFS.range)
                                 if str(r).startswith(str(ontology_ns))]
                        # Also handle external ranges (om-2, etc.)
                        external_ranges = [str(r) for r in g.objects(prop, RDFS.range)
                                          if not str(r).startswith(str(ontology_ns)) and '/' in str(r)]
                        if ranges or external_ranges:
                            ext_locals: list[str] = []
                            for r in external_ranges:
                                if "/" not in r and "#" not in r:
                                    continue
                                local = (r.rsplit("#", 1)[-1]).rsplit("/", 1)[-1]
                                if local:
                                    ext_locals.append(local)
                                    # Keep a representative full IRI for this local name
                                    external_range_iris.setdefault(local, r)
                            all_ranges = ranges + ext_locals
                            connects_to.append({
                                'property': prop_name,
                                'target_classes': all_ranges
                            })

        # Find object properties where this class is the RANGE (what connects TO this class)
        connected_from = []
        for prop in g.subjects(RDF.type, OWL.ObjectProperty):
            if str(prop).startswith(str(ontology_ns)):
                for rng in g.objects(prop, RDFS.range):
                    if str(rng) == str(class_uri):
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        # Collect all domain classes (handling unions)
                        all_domain_classes = []
                        for domain in g.objects(prop, RDFS.domain):
                            all_domain_classes.extend(extract_classes_from_domain(domain))
                        if all_domain_classes:
                            connected_from.append({
                                'property': prop_name,
                                'source_classes': all_domain_classes
                            })

        # Find datatype properties where this class is the DOMAIN (what data/inputs this class has)
        datatype_inputs = []
        datatype_comments: Dict[str, str] = {}
        for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
            if str(prop).startswith(str(ontology_ns)):
                for domain in g.objects(prop, RDFS.domain):
                    # Extract all classes from domain (handles unions)
                    domain_classes = extract_classes_from_domain(domain)
                    if class_name in domain_classes:
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        datatype_inputs.append(prop_name)
                        cmt_vals = list(g.objects(prop, RDFS.comment))
                        if cmt_vals:
                            datatype_comments[prop_name] = str(cmt_vals[0]).strip()

        # Find subclass relationships
        parents = []
        for parent in g.objects(class_uri, RDFS.subClassOf):
            if str(parent).startswith(str(ontology_ns)):
                parent_name = str(parent).replace(str(ontology_ns), '')
                parents.append(parent_name)

        class_structures[class_name] = {
            'connects_to': connects_to,
            'connected_from': connected_from,
            'datatype_inputs': datatype_inputs,
            'datatype_comments': datatype_comments,
            'parent_classes': parents
        }

    integrity_profile: Dict[str, Any] = {}
    try:
        integrity_profile = extract_ontology_integrity_profile(ontology_path)
        class_constraints = integrity_profile.get("class_constraints", {}) or {}
        for class_name, structure in class_structures.items():
            structure["integrity_annotations"] = class_constraints.get(
                class_name,
                {
                    "instance_integrity_rules": [],
                    "edge_integrity_rules": [],
                    "ordering_semantics": [],
                    "typing_integrity_rules": [],
                },
            )
    except Exception:
        integrity_profile = {}
        for structure in class_structures.values():
            structure["integrity_annotations"] = {
                "instance_integrity_rules": [],
                "edge_integrity_rules": [],
                "ordering_semantics": [],
                "typing_integrity_rules": [],
            }

    # Build class_hierarchy dict for parent-class grouping
    class_hierarchy = {}
    for class_name, structure in class_structures.items():
        if structure['parent_classes']:
            class_hierarchy[class_name] = structure['parent_classes']

    return {
        'namespace_uri': ontology_ns,
        'classes': sorted(classes),
        'class_structures': class_structures,
        'class_hierarchy': class_hierarchy,
        # Optional: unit inventory (T-Box derived) to enable LLM-generated label->IRI mappings.
        'om2_units': om2_units,
        # External range IRI map (local -> full IRI) to reflect referenced ontologies in summaries.
        'external_range_iris': external_range_iris,
        # Generic ontology-derived integrity profile for prompt/runtime contracts.
        'integrity_profile': integrity_profile,
    }


def _ensure_import_re_module(code: str) -> str:
    """Ensure generated entity/base modules have `import re` when post-patches use regex."""
    if re.search(r"(?m)^import re\s*$", code):
        return code
    lines = code.splitlines(keepends=True)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    for i in range(insert_at, len(lines)):
        if lines[i].startswith("import json"):
            lines.insert(i + 1, "import re\n")
            return "".join(lines)
    for i in range(insert_at, len(lines)):
        if lines[i].startswith("import ") or lines[i].startswith("from "):
            lines.insert(i + 1, "import re\n")
            return "".join(lines)
    return "import re\n" + code


def _char_offset_after_leading_imports_and_docstring(code: str) -> int | None:
    """Byte offset in `code` where module-level helpers can be inserted (after imports + optional docstring)."""
    try:
        mod = ast.parse(code)
    except SyntaxError:
        return None
    lines = code.splitlines(keepends=True)
    for node in mod.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(
            getattr(node, "value", None), ast.Constant
        ) and isinstance(getattr(node.value, "value", None), str):
            continue
        ln = (node.lineno or 1) - 1
        return sum(len(lines[i]) for i in range(ln))
    return None


def _inject_entity_label_control_sanitization(code: str) -> str:
    """
    Strip parenthetical agent 'control' phrases from labels before `_sanitize_label`.

    Idempotent: skips when `_strip_control_suffix` is already present.
    If the model already emitted `_looks_like_control_phrase` but not `_strip_control_suffix`,
    inject only the stripper to avoid duplicate `def _looks_like_control_phrase` (syntax error).
    Helpers are inserted after imports (not before `_validate_*`), so they stay module-level even
    when the model leaves an unclosed block above the validators.
    """
    if "_strip_control_suffix" in code:
        return code
    m_v = re.search(r"(?m)^def _validate_required_label\(", code)
    m_r = re.search(r"(?m)^def _require_label\(", code)
    if not m_v and not m_r:
        return code
    code = _ensure_import_re_module(code)
    m_v = re.search(r"(?m)^def _validate_required_label\(", code)
    m_r = re.search(r"(?m)^def _require_label\(", code)
    strip_body = (
        "def _strip_control_suffix(label: str) -> str:\n"
        "    text = str(label or \"\")\n"
        "    previous = None\n"
        "    while text != previous:\n"
        "        previous = text\n"
        "        text = re.sub(\n"
        "            r\"\\s*\\(([^()]*)\\)\\s*$\",\n"
        "            lambda m: \"\" if _looks_like_control_phrase(m.group(1)) else m.group(0),\n"
        "            text,\n"
        "        ).strip()\n"
        "    return text\n"
    )
    if "_looks_like_control_phrase" in code:
        helper = "\n\n" + strip_body
    else:
        helper = (
            "\n\ndef _looks_like_control_phrase(text: str) -> bool:\n"
            "    s = str(text or \"\").strip().lower()\n"
            "    if not s:\n"
            "        return False\n"
            "    markers = (\n"
            "        \"stop tool\", \"tool call\", \"no more call\", \"prepare export\", \"export now\",\n"
            "        \"closing now\", \"stop generating\", \"final final\", \"final end\", \"done now\",\n"
            "        \"wrap up\", \"wrapping\",\n"
            "    )\n"
            "    return any(marker in s for marker in markers)\n"
            "\n"
            "\n"
            + strip_body
        )
    insert_at = _char_offset_after_leading_imports_and_docstring(code)
    if insert_at is None:
        insert_at = min(m.start() for m in (m_v, m_r) if m)
    code = code[:insert_at] + helper + "\n" + code[insert_at:]
    out = re.sub(
        r"(def _validate_required_label\([^)]*\)\s*(?:->[^\n]+)?\s*:\s*\n)(\s*)lbl\s*=\s*_sanitize_label\(label\)\s*\n",
        r"\1\2lbl = _sanitize_label(_strip_control_suffix(label))\n",
        code,
        count=1,
        flags=re.MULTILINE,
    )
    if out == code:
        out = re.sub(
            r"(def _validate_required_label\([^)]*\)\s*(?:->[^\n]+)?\s*:\s*\n)(\s*)lbl\s*=\s*_sanitize_label\(str\(label\)\)\s*\n",
            r"\1\2lbl = _sanitize_label(_strip_control_suffix(str(label)))\n",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    if out == code and m_r:
        out = re.sub(
            r"(def _require_label\([^)]*\)\s*(?:->[^\n]+)?\s*:\s*\n)(\s*)lbl\s*=\s*_sanitize_label\(label\)\s*\n",
            r"\1\2lbl = _sanitize_label(_strip_control_suffix(label))\n",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    if out == code and m_r:
        out = re.sub(
            r"(def _require_label\([^)]*\)\s*(?:->[^\n]+)?\s*:\s*\n)(\s*)lbl\s*=\s*_sanitize_label\(str\(label\)\)\s*\n",
            r"\1\2lbl = _sanitize_label(_strip_control_suffix(str(label)))\n",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    if out == code and m_r:
        out = re.sub(
            r"(def _require_label\([^)]*\)\s*(?:->[^\n]+)?\s*:\s*\n)(\s*)return _sanitize_label\(label\)\s*\n",
            r"\1\2return _sanitize_label(_strip_control_suffix(label))\n",
            code,
            count=1,
            flags=re.MULTILINE,
        )
    return out


def _patch_extension_scoped_create_canonical_labels(code: str, ontology_name: str) -> str:
    """
    For selected `create_<ClassLocal>(...)` functions, bind labels to `{scoped}__<ClassLocal>`
    so repeated tool calls reuse the same individual. ClassLocal values come from
    `generator_postprocess_config.json` (OWL local names), not from document text.
    """
    okey = str(ontology_name or "").strip().lower()
    cfg = _merge_generator_postprocess()
    class_list = (cfg.get("scoped_canonical_label_for_create") or {}).get(okey) or []
    if not isinstance(class_list, (list, tuple)) or not class_list:
        return code
    if "_scoped_canonical_node_label" in code:
        return code
    if "_read_global_state" not in code:
        return code
    code = _ensure_import_re_module(code)
    tail_re = cfg.get("scoped_context_label_tail_strip_regex")
    if tail_re is None or str(tail_re).strip() == "":
        base_block = (
            "    base = str(ent_name or \"\").strip()\n"
        )
    else:
        pat = str(tail_re).replace("\\", "\\\\").replace('"', '\\"')
        base_block = (
            f"    base = str(ent_name or \"\").strip()\n"
            f"    base = re.sub(r\"{pat}\", \"\", base, flags=re.IGNORECASE).strip()\n"
        )
    helper = (
        "\n\ndef _scoped_canonical_node_label(user_label: str, class_local: str) -> str:\n"
        "    \"\"\"Stable label for a scoped `create_*` call; class_local is an OWL class name from the T-Box.\"\"\"\n"
        "    try:\n"
        "        _, ent_name, _ = _read_global_state()\n"
        "    except Exception:\n"
        "        ent_name = \"\"\n"
        f"{base_block}"
        "    if not base:\n"
        "        base = _sanitize_label(str(user_label or \"\")) or \"node\"\n"
        "    return f\"{{base}}__{{class_local}}\"\n"
    )
    first_anchor = None
    for class_local in class_list:
        class_local = str(class_local).strip()
        if not class_local:
            continue
        anchor = f"def create_{class_local}("
        if anchor in code and first_anchor is None:
            first_anchor = anchor
    if first_anchor is None:
        return code
    pos0 = code.find(first_anchor)
    code = code[:pos0] + helper + "\n" + code[pos0:]
    for class_local in class_list:
        class_local = str(class_local).strip()
        if not class_local:
            continue
        anchor = f"def create_{class_local}("
        if anchor not in code:
            continue
        pos = code.find(anchor)
        window = code[pos : pos + 12000]
        new_lbl = (
            f"lbl = _validate_required_label(_scoped_canonical_node_label(label, {class_local!r}))"
        )
        new_lbl_alt = (
            f"lbl = _require_label(_scoped_canonical_node_label(label, {class_local!r}))"
        )
        for old, new in (
            ("lbl = _validate_required_label(label)", new_lbl),
            ("lbl = _require_label(label)", new_lbl_alt),
        ):
            if old in window:
                rel = window.find(old)
                code = code[: pos + rel] + new + code[pos + rel + len(old) :]
                break
    return code


def _patch_extension_base_export_top_link(code: str, ontology_name: str) -> str:
    """
    When `export_memory_top_link_repair` contains a spec object for this ontology in
    `generator_postprocess_config.json`, inject the pre-export repair. All namespace aliases
    and local OWL names are read from that spec (codegen only assembles `Alias.local` text).
    """
    okey = str(ontology_name or "").strip().lower()
    cfg = _merge_generator_postprocess()
    raw = (cfg.get("export_memory_top_link_repair") or {}).get(okey)
    if not raw:
        return code
    if not isinstance(raw, dict):
        print(
            f"   [WARN] export_memory_top_link_repair.{okey} must be a JSON object (see generator_postprocess_config.json); skipped."
        )
        return code
    try:
        ensure_fn = _build_ensure_tbox_export_time_link_block(
            raw, cfg.get("scoped_context_label_tail_strip_regex")
        )
    except Exception as exc:
        print(f"   [WARN] export link repair spec for {okey!r} ignored: {exc}")
        return code
    if "def export_memory_wrapper" not in code:
        return code
    code = _ensure_import_re_module(code)
    if "_ensure_tbox_export_time_link" in code:
        return code
    em = re.search(r"(?m)^(?:@_guard_noncheck\s*\n)?def export_memory_wrapper\(", code)
    if not em:
        return code
    code = code[: em.start()] + ensure_fn + "\n" + code[em.start() :]
    code = re.sub(
        r"(?s)(def export_memory_wrapper\([^\)]*\)[^:]*:.*?try:\s*)return export_memory\(\)",
        r"\1_ensure_tbox_export_time_link()\n        return export_memory()",
        code,
        count=1,
    )
    return code


# Soft line-length threshold for reformatting single-line `from ... import a, b, ...`
# (parenthesized / one symbol per line — avoids long-line truncation by the model)
WRAP_LONG_FROM_IMPORT_MAX_LEN = 100
WRAP_MANY_IMPORT_NAMES = 5
WRAP_MANY_IMPORT_MIN_LINE_LEN = 80

# LLM / tool output may cut long lines mid-identifier (`...om2_`); fix everywhere in source.
_OM2_HELPER_TRUNC_RE = re.compile(r"_find_or_create_om2_(?!quantity)")


def _fix_truncated_om2_helper_name_in_source(code: str) -> str:
    """Replace truncated `_find_or_create_om2_` with `_find_or_create_om2_quantity` (imports and calls)."""
    return _OM2_HELPER_TRUNC_RE.sub("_find_or_create_om2_quantity", code)


def _import_from_to_parenthesized_block(node: ast.ImportFrom, base_indent: str) -> str:
    """
    Emit a single ImportFrom as a parenthesized import with one name per line.

    :param node: An ``ast.ImportFrom`` with at least one alias.
    :param base_indent: Whitespace before ``from`` (indentation of the statement).
    :returns: Replacement source fragment without a trailing module newline; the caller
      preserves bytes after the original statement (e.g. ``\\n``).
    """
    # `from` + relative dots + dotted module (may be empty for `from . import ...`)
    head = f"{base_indent}from {'.' * (node.level or 0)}{node.module or ''} import ("
    parts: list[str] = [head]
    for j, al in enumerate(node.names):
        part = f"\n{base_indent}    {al.name}"
        if al.asname:
            part += f" as {al.asname}"
        if j < len(node.names) - 1:
            part += ","
        parts.append(part)
    parts.append(f"\n{base_indent})")
    return "".join(parts)


def _single_line_import_from_byte_span(code: str, node: ast.ImportFrom) -> Optional[tuple[int, int]]:
    """
    :returns: ``(start, end)`` byte indices in *code* for a one-line import statement,
    or None if the node spans multiple lines.
    """
    if getattr(node, "end_lineno", None) is None or node.lineno != node.end_lineno:
        return None
    lines = code.splitlines(keepends=True)
    if not lines or node.lineno < 1 or node.lineno > len(lines):
        return None
    line0 = node.lineno - 1
    offset = sum(len(lines[i]) for i in range(line0))
    return offset + node.col_offset, offset + node.end_col_offset


def _should_reparenthesize_from_import(
    len_segment: int,
    name_count: int,
    max_len: int = WRAP_LONG_FROM_IMPORT_MAX_LEN,
) -> bool:
    if name_count <= 1:
        return False
    if len_segment > max_len:
        return True
    if name_count >= WRAP_MANY_IMPORT_NAMES and len_segment > WRAP_MANY_IMPORT_MIN_LINE_LEN:
        return True
    return False


def _wrap_long_singleline_from_imports(
    code: str,
    max_len: int = WRAP_LONG_FROM_IMPORT_MAX_LEN,
) -> str:
    """
    Rewrite overlong *single-line* ``from`` imports to parenthesized, one-name-per-line form.

    This is a lossless structural transform when ``ast.parse`` succeeds; it reduces the risk
    of model output cutting off the last few characters of a long import line.
    """
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if getattr(node, "end_lineno", None) is None or node.lineno != node.end_lineno:
            continue
        n_names = len(node.names)
        if n_names <= 1:
            continue
        span = _single_line_import_from_byte_span(code, node)
        if span is None:
            continue
        a, b = span
        seg = code[a:b]
        if not _should_reparenthesize_from_import(len(seg), n_names, max_len):
            continue
        # Indentation before `from` on this line
        line_start = code.rfind("\n", 0, a) + 1
        base_indent = code[line_start:a]
        new_text = _import_from_to_parenthesized_block(node, base_indent)
        spans.append((a, b, new_text))

    spans.sort(key=lambda t: t[0], reverse=True)
    out = code
    for a, b, new_text in spans:
        out = out[:a] + new_text + out[b:]

    if spans:
        try:
            ast.parse(out)
        except SyntaxError:
            return code
    return out


def _ast_node_char_span(code: str, node: ast.AST) -> Optional[tuple[int, int]]:
    if getattr(node, "end_lineno", None) is None:
        return None
    lines = code.splitlines(keepends=True)
    start = sum(len(lines[i]) for i in range(node.lineno - 1)) + node.col_offset
    end = sum(len(lines[i]) for i in range(node.end_lineno - 1)) + node.end_col_offset
    return start, end


def _inject_missing_ancestor_rdf_types(
    code: str,
    *,
    class_to_ancestors: dict[str, list[str]],
    known_classes: set[str],
) -> str:
    """
    Add missing `g.add((<iri>, RDF.type, NAMESPACE['<Ancestor>']))` lines in each `create_*`
    that need explicit superclass typing (T-Box-driven; matches `_validate_superclass_typing` coverage).
    """
    import ast

    def _fn_has_type_edge(seg: str, subj: str, tname: str) -> bool:
        return re.search(
            rf"g\.add\(\(\s*{re.escape(subj)}\s*,\s*RDF\.type\s*,\s*NAMESPACE\['{re.escape(tname)}'\]\s*\)\)",
            seg,
        ) is not None

    def _inject_into_function_segment(
        seg: str,
        class_local: str,
        subj: str,
        ancestors: list[str],
    ) -> str:
        missing: list[str] = []
        for p in ancestors:
            if p not in known_classes or not p or p == class_local:
                continue
            if _fn_has_type_edge(seg, subj, p):
                continue
            missing.append(p)
        if not missing:
            return seg
        m1 = re.search(
            rf"(g\.add\(\(\s*{re.escape(subj)}\s*,\s*RDF\.type\s*,\s*NAMESPACE\['{re.escape(class_local)}'\][^\r\n]*\)\s*\)\s*\r?\n)",
            seg,
        )
        m2 = re.search(
            rf"(g\.add\(\(\s*{re.escape(subj)}\s*,\s*RDF\.type\s*,\s*rdf_type\s*\)\s*\)\s*\r?\n)",
            seg,
        )
        m = m1 or m2
        if not m:
            return seg
        # m group(1) starts at "g" (not leading spaces); recover indent from the line.
        line_start = seg.rfind("\n", 0, m.start(0)) + 1
        lead = seg[line_start : m.start(0)]
        ind = lead if (not lead or lead.isspace()) else "            "
        block = "".join(f"{ind}g.add(({subj}, RDF.type, NAMESPACE['{p}']))\n" for p in missing)
        return seg[: m.end(0)] + block + seg[m.end(0) :]

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    patches: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("create_"):
            continue
        class_local = node.name[7:]
        ancs = class_to_ancestors.get(class_local) or []
        ancs = [a for a in ancs if a in known_classes and a and a != class_local]
        if not ancs:
            continue
        sp = _ast_node_char_span(code, node)
        if not sp:
            continue
        a, b = sp
        seg = code[a:b]
        subj = "iri"
        sm = re.search(r"(\w+)\s*=\s*_mint_hash_iri\(", seg)
        if sm:
            subj = sm.group(1)
        new_seg = _inject_into_function_segment(seg, class_local, subj, ancs)
        if new_seg != seg:
            patches.append((a, b, new_seg))

    patches.sort(key=lambda t: t[0], reverse=True)
    out = code
    for a, b, new_text in patches:
        out = out[:a] + new_text + out[b:]

    if patches:
        try:
            ast.parse(out)
        except SyntaxError:
            return code
    return out


def _canonicalize_entity_create_param_names_from_body(code: str) -> str:
    """
    Repair OCR/LLM-corrupted `create_*` parameter names by inferring the
    canonical public parameter name from the function body itself.

    This stays ontology-agnostic:
    - quantity-like blocks map to `<predicate>_value/_unit`
    - label-based lookup blocks map to `<TargetClassLower>_label`
    """
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    quantity_block_re = re.compile(
        r"if\s+"
        r"(?P<value>[A-Za-z_][A-Za-z0-9_]*_value)\s+is\s+not\s+None\s+or\s+"
        r"(?P<unit>[A-Za-z_][A-Za-z0-9_]*_unit)\s+is\s+not\s+None\s*:\s*"
        r".*?"
        r"g\.add\(\(\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<prop_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<prop_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
        r"\s*,\s*\w+\s*\)\)",
        re.S,
    )
    helper_quantity_block_re = re.compile(
        r"_maybe_attach_om2_quantity\(\s*\w+\s*,\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<helper_prop_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<helper_prop_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
        r"\s*,.*?"
        r"value\s*=\s*(?P<helper_value>[A-Za-z_][A-Za-z0-9_]*_value)\s*,\s*"
        r"unit_label\s*=\s*(?P<helper_unit>[A-Za-z_][A-Za-z0-9_]*_unit)",
        re.S,
    )
    keyword_quantity_helper_re = re.compile(
        r"_attach_om2_quantity_if_provided\(\s*\w+\s*,\s*"
        r".*?predicate_iri\s*=\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<kw_prop_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<kw_prop_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
        r"\s*,.*?"
        r"value\s*=\s*(?P<kw_value>[A-Za-z_][A-Za-z0-9_]*_value)\s*,\s*"
        r"unit_label\s*=\s*(?P<kw_unit>[A-Za-z_][A-Za-z0-9_]*_unit)",
        re.S,
    )
    label_block_re = re.compile(
        r"if\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*_label)\s+is\s+not\s+None\s*:\s*"
        r".*?"
        r"_find_or_create_simple_by_label\(\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
        r"\s*,\s*['\"](?P<class_label>[A-Za-z_][A-Za-z0-9_]*)['\"]",
        re.S,
    )
    simple_labeled_individual_block_re = re.compile(
        r"if\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*_label)\s+is\s+not\s+None\s*:\s*"
        r".*?"
        r"_find_or_create_simple_labeled_individual\(\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
        r"\s*,\s*['\"](?P<class_label>[A-Za-z_][A-Za-z0-9_]*)['\"]",
        re.S,
    )
    typed_label_helper_re = re.compile(
        r"_find_or_create_[A-Za-z0-9_]*by_type_and_label\(\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
        r"\s*,\s*['\"](?P<class_label>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*,\s*"
        r"(?P<label_arg>[A-Za-z_][A-Za-z0-9_]*_label)",
        re.S,
    )
    helper_def_re = re.compile(
        r"def\s+(?P<helper>_find_or_create_[A-Za-z0-9_]+_in_graph)\s*\([^)]*\)\s*(?:->[^:]*)?:\s*"
        r"(?P<body>.*?)(?=\ndef\s+|\n@|\Z)",
        re.S,
    )
    helper_call_re = re.compile(
        r"(?P<helper>_find_or_create_[A-Za-z0-9_]+_in_graph)\(\s*\w+\s*,\s*"
        r"(?P<label_arg>[A-Za-z_][A-Za-z0-9_]*_label)\s*\)",
        re.S,
    )
    keyword_label_helper_re = re.compile(
        r"_maybe_[A-Za-z0-9_]*by_label\(\s*\w+\s*,\s*"
        r"label\s*=\s*(?P<label_arg>[A-Za-z_][A-Za-z0-9_]*_label)\s*,\s*"
        r"rdf_type\s*=\s*(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])\s*,\s*"
        r"class_local\s*=\s*['\"](?P<class_label>[A-Za-z_][A-Za-z0-9_]*)['\"]",
        re.S,
    )
    direct_lookup_label_block_re = re.compile(
        r"if\s+(?P<label_arg>[A-Za-z_][A-Za-z0-9_]*_label)\s+is\s+not\s+None\s*:\s*"
        r".*?_find_by_type_and_label\(\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])",
        re.S,
    )
    positional_label_helper_re = re.compile(
        r"_find_or_create_[A-Za-z0-9_]*by_label\(\s*\w+\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])\s*,\s*"
        r"['\"](?P<class_label>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*,\s*"
        r"(?P<label_arg>[A-Za-z_][A-Za-z0-9_]*_label)",
        re.S,
    )
    label_and_type_helper_re = re.compile(
        r"_find_or_create_[A-Za-z0-9_]*label_and_type\(\s*\w+\s*,\s*"
        r"['\"](?P<class_label>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*,\s*"
        r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
        r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])\s*,\s*"
        r"(?P<label_arg>[A-Za-z_][A-Za-z0-9_]*_label)",
        re.S,
    )

    helper_class_by_name: dict[str, str] = {}
    for match in helper_def_re.finditer(code):
        body = match.group("body") or ""
        cls_match = re.search(
            r"_find_by_type_and_label\(\s*\w+\s*,\s*"
            r"(?:[A-Z][A-Z0-9_]*\.(?P<class_attr>[A-Za-z_][A-Za-z0-9_]*)|"
            r"NAMESPACE\[['\"](?P<class_item>[A-Za-z_][A-Za-z0-9_]*)['\"]\])",
            body,
        )
        if cls_match:
            helper_class_by_name[match.group("helper")] = (
                cls_match.group("class_attr") or cls_match.group("class_item") or ""
            )

    def _rewrite_function_segment(seg: str) -> str:
        rename_map: dict[str, str] = {}

        for match in quantity_block_re.finditer(seg):
            prop_name = match.group("prop_attr") or match.group("prop_item") or ""
            if not prop_name:
                continue
            old_value = match.group("value")
            old_unit = match.group("unit")
            rename_map.setdefault(old_value, f"{prop_name}_value")
            rename_map.setdefault(old_unit, f"{prop_name}_unit")

        for match in helper_quantity_block_re.finditer(seg):
            prop_name = match.group("helper_prop_attr") or match.group("helper_prop_item") or ""
            if not prop_name:
                continue
            old_value = match.group("helper_value")
            old_unit = match.group("helper_unit")
            rename_map.setdefault(old_value, f"{prop_name}_value")
            rename_map.setdefault(old_unit, f"{prop_name}_unit")

        for match in keyword_quantity_helper_re.finditer(seg):
            prop_name = match.group("kw_prop_attr") or match.group("kw_prop_item") or ""
            if not prop_name:
                continue
            old_value = match.group("kw_value")
            old_unit = match.group("kw_unit")
            rename_map.setdefault(old_value, f"{prop_name}_value")
            rename_map.setdefault(old_unit, f"{prop_name}_unit")

        for match in label_block_re.finditer(seg):
            class_name = (
                match.group("class_label")
                or match.group("class_attr")
                or match.group("class_item")
                or ""
            )
            if not class_name:
                continue
            rename_map.setdefault(match.group("label"), f"{class_name.lower()}_label")

        for match in simple_labeled_individual_block_re.finditer(seg):
            class_name = (
                match.group("class_label")
                or match.group("class_attr")
                or match.group("class_item")
                or ""
            )
            if not class_name:
                continue
            rename_map.setdefault(match.group("label"), f"{class_name[0].lower()}{class_name[1:]}_label")

        for match in typed_label_helper_re.finditer(seg):
            class_name = (
                match.group("class_label")
                or match.group("class_attr")
                or match.group("class_item")
                or ""
            )
            if not class_name:
                continue
            rename_map.setdefault(match.group("label_arg"), f"{class_name[0].lower()}{class_name[1:]}_label")

        for match in helper_call_re.finditer(seg):
            class_name = helper_class_by_name.get(match.group("helper"), "")
            if not class_name:
                continue
            rename_map.setdefault(match.group("label_arg"), f"{class_name[0].lower()}{class_name[1:]}_label")

        for match in keyword_label_helper_re.finditer(seg):
            class_name = (
                match.group("class_label")
                or match.group("class_attr")
                or match.group("class_item")
                or ""
            )
            if not class_name:
                continue
            rename_map.setdefault(match.group("label_arg"), f"{class_name[0].lower()}{class_name[1:]}_label")

        for match in direct_lookup_label_block_re.finditer(seg):
            class_name = match.group("class_attr") or match.group("class_item") or ""
            if not class_name:
                continue
            rename_map.setdefault(match.group("label_arg"), f"{class_name[0].lower()}{class_name[1:]}_label")

        for match in positional_label_helper_re.finditer(seg):
            class_name = (
                match.group("class_label")
                or match.group("class_attr")
                or match.group("class_item")
                or ""
            )
            if not class_name:
                continue
            rename_map.setdefault(match.group("label_arg"), f"{class_name[0].lower()}{class_name[1:]}_label")

        for match in label_and_type_helper_re.finditer(seg):
            class_name = (
                match.group("class_label")
                or match.group("class_attr")
                or match.group("class_item")
                or ""
            )
            if not class_name:
                continue
            rename_map.setdefault(match.group("label_arg"), f"{class_name[0].lower()}{class_name[1:]}_label")

        if not rename_map:
            return seg

        inverse: dict[str, list[str]] = {}
        for old_name, new_name in rename_map.items():
            if old_name == new_name:
                continue
            if re.search(rf"\b{re.escape(new_name)}\b", seg):
                continue
            inverse.setdefault(new_name, []).append(old_name)

        safe_pairs: list[tuple[str, str]] = []
        for new_name, old_names in inverse.items():
            if len(old_names) != 1:
                continue
            safe_pairs.append((old_names[0], new_name))

        if not safe_pairs:
            return seg

        updated = seg
        for old_name, new_name in sorted(safe_pairs, key=lambda item: len(item[0]), reverse=True):
            updated = re.sub(rf"\b{re.escape(old_name)}\b", new_name, updated)
        return updated

    patches: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("create_"):
            continue
        span = _ast_node_char_span(code, node)
        if not span:
            continue
        start, end = span
        segment = code[start:end]
        rewritten = _rewrite_function_segment(segment)
        if rewritten != segment:
            patches.append((start, end, rewritten))

    if not patches:
        return code

    out = code
    for start, end, rewritten in sorted(patches, key=lambda item: item[0], reverse=True):
        out = out[:start] + rewritten + out[end:]

    try:
        ast.parse(out)
    except SyntaxError:
        return code
    return out


def _strip_llm_control_phrase_helpers(code: str) -> str:
    """
    Drop LLM-copied _looks_like_control_phrase / _strip_control_suffix defs at any scope.
    Canonical helpers are injected by `_inject_entity_label_control_sanitization`.
    """
    bad = {"_looks_like_control_phrase", "_strip_control_suffix"}
    for _ in range(8):
        try:
            mod = ast.parse(code)
        except SyntaxError:
            code = re.sub(
                r"(?ms)^def _looks_like_control_phrase\([^)]*\)\s*(?:->[^\n]+)?\s*\n(?:^[ \t].*(?:\n|\r\n)|^\s*(?:\n|\r\n))+",
                "",
                code,
                count=1,
            )
            continue
        spans: list[tuple[int, int]] = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                if node.name in bad:
                    s = (node.lineno or 1) - 1
                    e = getattr(node, "end_lineno", None) or (node.lineno or 1)
                    spans.append((s, e))
                self.generic_visit(node)

        V().visit(mod)
        if not spans:
            break
        lines = code.splitlines(keepends=True)
        for s, e in sorted(spans, reverse=True):
            del lines[s:e]
        code = "".join(lines)
    return code


def _normalize_entity_script_contracts(code: str, ontology_name: str = "") -> str:
    """
    Normalize common contract mismatches in LLM-generated entity scripts so the
    emitted code matches the runtime helpers exposed by the generated base script.

    Current normalizations:
    - rewrite `_MED.<x>` to `NAMESPACE.<x>` when present
    - fix truncated `_find_or_create_om2_` import → `_find_or_create_om2_quantity`
    - remove spurious one-line tokens like `quantity` or `E[local]` (LLM glitch)
    - rewrite `_format_error("CODE", message)` to `_format_error(message, code="CODE")`
    - reformat long one-line `from` imports to parenthesized form
    """
    import ast

    normalized = _strip_llm_control_phrase_helpers(code)
    normalized = normalized.replace("_MED.", "NAMESPACE.")
    normalized = re.sub(
        r"(?m)^(\s*)return\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+allo\s*\r?\n\s*wed_iris\s*$",
        r"\1return \2 in allowed_iris",
        normalized,
    )
    normalized = re.sub(
        r"(?m)^(\s*)return\s+(.+?)\s+in\s+OM2_\s*\r?\n\s*UNIT_MAP\s*$",
        r"\1return \2 in OM2_UNIT_MAP",
        normalized,
    )
    # Long `from` lines are sometimes cut mid-identifier; a lone `quantity` line is rare noise.
    normalized = _fix_truncated_om2_helper_name_in_source(normalized)
    normalized = _canonicalize_entity_create_param_names_from_body(normalized)
    # LLM glitch: a line with only `quantity` (must handle CRLF, not just \n)
    normalized = re.sub(r"(?m)^\s*quantity\s*(\r?\n|$)", "", normalized)
    # Another rare LLM glitch is an orphan namespace lookup line such as `E[local]`
    # emitted outside any statement context. It has no side effects and breaks imports.
    normalized = re.sub(r"(?m)^\s*E\[[^\]]+\]\s*(\r?\n|$)", "", normalized)

    class _ErrorCallRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not (isinstance(node.func, ast.Name) and node.func.id == "_format_error"):
                return node
            if len(node.args) != 2 or node.keywords:
                return node

            first = node.args[0]
            second = node.args[1]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                return node

            code_text = first.value.strip()
            if not code_text or not re.fullmatch(r"[A-Z_]+", code_text):
                return node

            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="_format_error", ctx=ast.Load()),
                    args=[second],
                    keywords=[ast.keyword(arg="code", value=ast.Constant(value=code_text))],
                ),
                node,
            )

    try:
        tree = ast.parse(normalized)
        tree = _ErrorCallRewriter().visit(tree)
        ast.fix_missing_locations(tree)
        normalized = ast.unparse(tree)
    except Exception:
        # Non-fatal: keep original source if AST normalization fails.
        pass

    normalized = _inject_entity_label_control_sanitization(normalized)
    normalized = _patch_extension_scoped_create_canonical_labels(normalized, ontology_name)
    normalized = _patch_top_entity_create_multi_contract(normalized, ontology_name)
    if "OM2_UNIT_MAP" in normalized and "_normalize_om2_unit_alias" not in normalized:
        normalized = normalized.replace(
            "OM2_UNIT_MAP, _resolve_om2_unit, _find_or_create_om2_quantity",
            "OM2_UNIT_MAP, _normalize_om2_unit_alias, _resolve_om2_unit, _find_or_create_om2_quantity",
        )
        normalized = normalized.replace(
            "OM2_UNIT_MAP,\n    _resolve_om2_unit",
            "OM2_UNIT_MAP,\n    _normalize_om2_unit_alias,\n    _resolve_om2_unit",
        )
        normalized = normalized.replace(
            "str(unit_label).strip().lower()",
            "_normalize_om2_unit_alias(str(unit_label))",
        )
        normalized = normalized.replace(
            "str(unit).strip().lower()",
            "_normalize_om2_unit_alias(str(unit))",
        )
        normalized = normalized.replace(
            "ul = _sanitize_label(unit_label)",
            "ul = _normalize_om2_unit_alias(unit_label)",
        )
        normalized = normalized.replace(
            "unit_label = _sanitize_label(unit)",
            "unit_label = _normalize_om2_unit_alias(unit)",
        )
        normalized = normalized.replace(
            "_resolve_om2_unit(_sanitize_label(",
            "_resolve_om2_unit(_normalize_om2_unit_alias(",
        )
        normalized = normalized.replace(
            "unit_label=_sanitize_label(",
            "unit_label=_normalize_om2_unit_alias(",
        )
        normalized = normalized.replace(
            "if u not in allowed:",
            "u = _normalize_om2_unit_alias(u)\n    if u not in allowed:",
        )
    normalized = _wrap_long_singleline_from_imports(normalized)
    # again after unparse/insert/wrap: ast.unparse or multi-line import layout can leave a truncated `om2_`
    normalized = _fix_truncated_om2_helper_name_in_source(normalized)
    normalized = re.sub(r"(?m)^\s*quantity\s*(\r?\n|$)", "", normalized)
    normalized = re.sub(r"(?m)^\s*E\[[^\]]+\]\s*(\r?\n|$)", "", normalized)
    return normalized


def patch_super_flat_entity_scripts(
    ontology_path: str,
    ontology_name: str,
    entity_script_paths: list[str],
) -> list[str]:
    """
    For super-flat ontologies, inject deterministic helper tools into the generated
    entity script:
      - create_<TopClass>_top_only(label)
      - update_<TopClass>_data_properties(entity_iri, ...)

    This avoids relying on the LLM to discover the "create first, enrich later"
    pattern for flat schemas with a single main class and only datatype properties.
    """
    shape = detect_super_flat_ontology(ontology_path)
    if not shape.get("is_super_flat"):
        return entity_script_paths

    top_class = shape.get("top_level_class")
    if not top_class:
        return entity_script_paths

    concise = extract_concise_ontology_structure(ontology_path, include_om2_mock=False)
    class_structures = concise.get("class_structures", {}) or {}
    top_structure = class_structures.get(top_class, {}) or {}
    datatype_inputs = sorted(top_structure.get("datatype_inputs", []) or [])
    if not datatype_inputs:
        return entity_script_paths

    create_fn_name = f"create_{top_class}"
    top_only_fn_name = f"create_{top_class}_top_only"
    update_fn_name = f"update_{top_class}_data_properties"

    target_path: Optional[Path] = None
    for script_path in entity_script_paths:
        p = Path(script_path)
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if f"def {create_fn_name}(" in text:
            target_path = p
            break

    if target_path is None:
        print(f"   [WARN] Could not locate generated {create_fn_name} for super-flat patching")
        return entity_script_paths

    # One atomic setter per datatype property for unambiguous LLM tool-calling.
    dt_comments: dict[str, str] = top_structure.get("datatype_comments", {}) or {}
    setter_prefix = f"set_{top_class}_"

    atomic_blocks: list[str] = []
    for prop in datatype_inputs:
        fn_name = f"{setter_prefix}{prop}"
        raw_comment = dt_comments.get(prop, "")
        # Collapse multi-line comment and escape triple-quote delimiters
        comment_line = raw_comment.replace("\n", " ").replace("\r", " ").replace('"""', "'''")
        if comment_line:
            docstring = f'Set `{prop}` on a `{top_class}` individual.\n\n    Schema: {comment_line}'
        else:
            docstring = f'Set `{prop}` on a `{top_class}` individual.'
        atomic_blocks.append(
            f"""

@_guard_noncheck
def {fn_name}(entity_iri: str, value: str) -> str:
    \"\"\"{docstring}\"\"\"
    return {update_fn_name}(entity_iri=entity_iri, **{{{repr(prop)}: value}})
""".rstrip()
        )
    part_tools_block = "\n".join(atomic_blocks).rstrip()

    wrapper_block = f"""

# ---------------------------------------------------------------------------
# Auto-generated super-flat ontology helpers
# ---------------------------------------------------------------------------

_SF_NS = NAMESPACE

def _super_flat_error(code: str, message: str) -> str:
    return _format_error(message, code=code)

@_guard_noncheck
def {top_only_fn_name}(label: str) -> str:
    try:
        lbl = _sanitize_label(label)
        if not lbl:
            return _super_flat_error("VALIDATION_FAILED", "label is required")

        with locked_graph() as g:
            class_iri = _SF_NS.{top_class}
            existing = _find_by_type_and_label(g, class_iri, lbl)
            if existing is not None:
                return _format_success_json(str(existing), created=False, message="Already exists")

            iri = _mint_hash_iri("{top_class}")
            g.add((iri, RDF.type, class_iri))
            _set_single_label(g, iri, lbl)
            _export_snapshot_silent()
            return _format_success_json(str(iri), created=True, message="Created top-level entity")
    except Exception as e:
        return _super_flat_error("INTERNAL_ERROR", str(e))


@_guard_noncheck
def {update_fn_name}(entity_iri: str, label: Optional[str] = None, **data: Optional[str]) -> str:
    try:
        entity_iri = str(entity_iri).strip()
        if not entity_iri:
            return _super_flat_error("VALIDATION_FAILED", "entity_iri is required")

        with locked_graph() as g:
            entity = URIRef(entity_iri)
            class_iri = _SF_NS.{top_class}
            if (entity, RDF.type, class_iri) not in g:
                return _super_flat_error("VALIDATION_FAILED", f"entity_iri is not a {top_class}: {{entity_iri}}")

            changed = False
            if label is not None:
                lbl = _sanitize_label(label)
                if lbl:
                    _set_single_label(g, entity, lbl)
                    changed = True

            allowed = {{
{", ".join(repr(p) for p in datatype_inputs)}
            }}
            for k, v in (data or {{}}).items():
                if k not in allowed:
                    continue
                if v is None:
                    continue
                # overwrite single-valued super-flat fields
                pred = URIRef(str(_SF_NS) + str(k))
                for old in list(g.objects(entity, pred)):
                    g.remove((entity, pred, old))
                g.add((entity, pred, RDFLiteral(str(v))))
                changed = True
            _export_snapshot_silent()
            msg = "Updated existing entity" if changed else "No changes"
            return _format_success_json(str(entity), created=False, message=msg)
    except Exception as e:
        return _super_flat_error("INTERNAL_ERROR", str(e))

{part_tools_block}
"""

    text = _normalize_entity_script_contracts(
        target_path.read_text(encoding="utf-8"), ontology_name
    )
    start_marker = "# ---------------------------------------------------------------------------\n# Auto-generated super-flat ontology helpers"
    if start_marker in text:
        text = text.split(start_marker, 1)[0].rstrip() + "\n"
    text = text.rstrip() + "\n" + wrapper_block.rstrip() + "\n"
    target_path.write_text(text, encoding="utf-8")
    print(
        f"   [INFO] Super-flat ontology detected for {ontology_name}: injected "
        f"{top_only_fn_name}, {update_fn_name}, and {len(datatype_inputs)} atomic "
        f"set_{top_class}_<prop> functions into {target_path.name}"
    )
    return entity_script_paths


def patch_super_flat_base_script(
    ontology_path: str,
    ontology_name: str,
    base_script_path: str,
) -> str:
    """
    For super-flat ontologies, redefine `init_memory_wrapper` in the generated
    base script so entity-scoped memory is seeded with the already-created top
    entity before iteration-2 updates run.

    This bridges the runtime handoff between:
    - iteration 1: top entity created in shared top memory/state
    - iteration 2+: entity-specific memory keyed by entity label
    """
    shape = detect_super_flat_ontology(ontology_path)
    if not shape.get("is_super_flat"):
        return base_script_path

    top_class = shape.get("top_level_class")
    if not top_class:
        return base_script_path

    target_path = Path(base_script_path)
    if not target_path.exists():
        return base_script_path

    text = target_path.read_text(encoding="utf-8")
    # Fix a common direct-generation bug: calling get_memory_paths() without args.
    # Guard state must be stored under the current (doi, entity) memory directory.
    text = re.sub(
        r"def _guard_paths\(\)(?:\s*->\s*[^:]+)?:\n(?:    .*\n)+?\n",
        "def _guard_paths():\n"
        "    \"\"\"Return paths for guard state files.\"\"\"\n"
        "    try:\n"
        "        doi_g, ent_g, _ = _read_global_state()\n"
        "        mem_dir = get_memory_paths(doi_g, ent_g)[\"dir\"]\n"
        "    except Exception:\n"
        "        mem_dir = os.path.dirname(__file__)\n"
        "    return {\"state\": os.path.join(mem_dir, \"guard_state.json\")}\n\n",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"@_guard_noncheck\s*\ndef init_memory_wrapper\([^\n]*\)\s*->\s*str:\n(?:    .*\n)+?(?=@_guard_noncheck\s*\ndef export_memory_wrapper)",
        "",
        text,
        flags=re.MULTILINE,
    )
    start_marker = "# -----------------------------------------------------------------------------\n# Auto-generated super-flat memory seeding"
    if start_marker in text:
        text = text.split(start_marker, 1)[0].rstrip() + "\n"

    block = f"""

# -----------------------------------------------------------------------------
# Auto-generated super-flat memory seeding
# -----------------------------------------------------------------------------
from ..universal_utils import _read_global_state

_SF_BASE_NS = NAMESPACE

def _seed_super_flat_top_entity_in_memory() -> None:
    try:
        doi_actual, entity_actual, entity_iri = _read_global_state()
        iri_str = str(entity_iri or "").strip()
        if not iri_str:
            return

        with locked_graph(doi=doi_actual, top_level_entity_name=entity_actual, timeout=30.0) as g:
            entity = URIRef(iri_str)
            class_iri = _SF_BASE_NS.{top_class}
            if (entity, RDF.type, class_iri) not in g:
                g.add((entity, RDF.type, class_iri))
            if entity_actual:
                _set_single_label(g, entity, entity_actual)
    except Exception:
        # Never block init; this is a best-effort bridge from top memory to
        # entity-scoped memory for super-flat ontologies.
        return


@_guard_noncheck
def init_memory_wrapper(doi: Optional[str] = None, top_level_entity_name: Optional[str] = None) -> str:
    \"\"\"Initialize or resume memory graph and seed super-flat top entity.\"\"\"
    try:
        result = init_memory(doi, top_level_entity_name)
        _seed_super_flat_top_entity_in_memory()
        return result
    except Exception as e:
        return _format_error(f"Failed to initialize memory: {{e}}", code="MEMORY_INIT_FAILED", retryable=False)
"""

    target_path.write_text(text.rstrip() + "\n" + block.rstrip() + "\n", encoding="utf-8")
    print(
        f"   [INFO] Super-flat ontology detected for {ontology_name}: patched base init_memory_wrapper "
        f"in {target_path.name}"
    )
    return base_script_path


def patch_super_flat_main_script(
    ontology_path: str,
    ontology_name: str,
    main_script_path: str,
) -> str:
    """
    For super-flat ontologies, inject one atomic @mcp.tool() per datatype property into
    `main.py`.  Each tool is named `set_{TopClass}_{PropertyName}` and accepts exactly
    `entity_iri` and `value`, making it unambiguous for the LLM which tool to call.

    The atomic tools delegate to the underlying `_set_{TopClass}_{prop}` functions
    injected into the entity script by `patch_super_flat_entity_scripts`.
    """
    shape = detect_super_flat_ontology(ontology_path)
    if not shape.get("is_super_flat"):
        return main_script_path
    top_class = shape.get("top_level_class")
    if not top_class:
        return main_script_path

    p = Path(main_script_path)
    if not p.exists():
        return main_script_path

    text = p.read_text(encoding="utf-8")
    # The entity script exports `set_{TopClass}_{prop}` functions; main.py imports the
    # underlying update core as `_update_{TopClass}_data_properties`.
    underlying_core = f"_update_{top_class}_data_properties"
    if underlying_core not in text:
        return main_script_path

    # Remove any legacy chunked tools (update_{TopClass}_data_properties_partN) that
    # were injected by a previous version of this patcher.
    import re as _re
    legacy_pattern = _re.compile(
        r"\n@mcp\.tool\(\)\ndef update_" + _re.escape(top_class) +
        r"_data_properties_part\d+\(.*?\n(?=\n@mcp\.tool\(\)|\nif __name__|$)",
        _re.DOTALL,
    )
    text = legacy_pattern.sub("\n", text)

    # Collect per-property metadata from the ontology structure.
    concise = extract_concise_ontology_structure(ontology_path, include_om2_mock=False)
    class_structures = concise.get("class_structures", {}) or {}
    top_structure = class_structures.get(top_class, {}) or {}
    datatype_inputs = sorted(top_structure.get("datatype_inputs", []) or [])
    dt_comments: dict[str, str] = top_structure.get("datatype_comments", {}) or {}
    if not datatype_inputs:
        return main_script_path

    # Remove stale chunk-part imports from the import line.
    legacy_import_pattern = _re.compile(
        r",?\s*update_" + _re.escape(top_class) + r"_data_properties_part\d+\s+as\s+\w+"
    )
    text = legacy_import_pattern.sub("", text)

    # Also patch import line: the entity module now exports set_{TopClass}_{prop} functions.
    # We need to import them as _set_{TopClass}_{prop}.
    setter_prefix = f"set_{top_class}_"
    underlying_setter_prefix = f"_set_{top_class}_"

    # Build import additions for new setters (only those not already imported).
    import_additions: list[str] = []
    for prop in datatype_inputs:
        fn = f"{setter_prefix}{prop}"
        alias = f"{underlying_setter_prefix}{prop}"
        if alias not in text:
            import_additions.append(f"{fn} as {alias}")

    # Patch the import line that already imports from the entity module.
    entity_module_pattern = f"from .{ontology_name}_creation_entities_"
    if import_additions:
        # Find the last import line from entity modules and append to it.
        lines = text.split("\n")
        last_entity_import_idx = -1
        for i, line in enumerate(lines):
            if entity_module_pattern in line:
                last_entity_import_idx = i
        if last_entity_import_idx >= 0:
            existing_line = lines[last_entity_import_idx]
            # Strip closing paren/newline of the import if it's a single-line import
            additions_str = ", ".join(import_additions)
            if existing_line.rstrip().endswith(")"):
                lines[last_entity_import_idx] = existing_line.rstrip()[:-1] + f", {additions_str})"
            else:
                lines[last_entity_import_idx] = existing_line.rstrip() + f", {additions_str}"
            text = "\n".join(lines)

    # Build one @mcp.tool() wrapper per property.
    blocks: list[str] = []
    for prop in datatype_inputs:
        tool_fn = f"set_{top_class}_{prop}"
        if f"def {tool_fn}(" in text:
            continue  # already injected
        underlying_fn = f"_set_{top_class}_{prop}"
        raw_comment = dt_comments.get(prop, "")
        comment_line = raw_comment.replace("\n", " ").replace("\r", " ").replace('"""', "'''")
        if comment_line:
            docstring = f'Set `{prop}` on a `{top_class}` individual.\n\n    Schema: {comment_line}'
        else:
            docstring = f'Set `{prop}` on a `{top_class}` individual.'
        blocks.append(
            f'\n\n@mcp.tool()\ndef {tool_fn}(entity_iri: str, value: str) -> str:\n'
            f'    """{docstring}"""\n'
            f'    return {underlying_fn}(entity_iri=entity_iri, value=value)'
        )

    if not blocks:
        return main_script_path

    tool_block = "\n".join(blocks).rstrip()

    # Insert before the `if __name__ == '__main__'` guard if present, else append.
    guard_idx = text.find("\nif __name__")
    if guard_idx < 0:
        text = text.rstrip() + "\n" + tool_block + "\n"
    else:
        text = text[:guard_idx].rstrip() + "\n" + tool_block + "\n\n" + text[guard_idx:].lstrip()

    p.write_text(text, encoding="utf-8")
    print(
        f"   [INFO] Super-flat ontology detected for {ontology_name}: injected "
        f"{len(datatype_inputs)} atomic set_{top_class}_<prop> tools into {p.name}"
    )
    return main_script_path


def _ontology_object_property_count(ontology_path: str, ontology_name: str) -> int:
    concise = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )
    total = 0
    for structure in (concise.get("class_structures", {}) or {}).values():
        total += len((structure or {}).get("connects_to", []) or [])
    return total


def _integrity_contract_block_from_concise(
    concise_structure: Dict[str, Any],
    *,
    include_machine_readable: bool = True,
) -> str:
    """Build generic ontology-derived integrity guidance from concise structure."""
    profile = concise_structure.get("integrity_profile", {}) or {}
    base = format_ontology_integrity_guidance(
        profile,
        include_machine_readable=include_machine_readable,
    ).strip()
    if not base:
        return ""

    lines = [base]
    if profile.get("ordered_member_classes"):
        lines.append(
            "Script-generation enforcement:\n"
            "- For ontology-marked ordered members, each `create_*` function must create exactly one member individual per call.\n"
            "- Keep order-like fields scalar for one member only; do not accept aggregated multi-member payloads.\n"
            "- Do not use same-class deduplication checks for ontology-marked non-reusable ordered members."
        )
    return "\n\n".join(line for line in lines if line)


def format_concise_structure_as_markdown(concise_structure: Dict, ontology_name: str) -> str:
    """
    Format the concise ontology structure as a markdown document.

    Args:
        concise_structure: Output from extract_concise_ontology_structure()
        ontology_name: Name of the ontology

    Returns:
        Markdown-formatted string
    """
    lines = [
        f"# Concise Ontology Structure: {ontology_name}",
        "",
        "**Auto-generated by direct script generation pipeline**",
        "",
        "This document contains the concise, focused structure extracted from the ontology TTL file.",
        "It includes structural information needed for script generation AND schema constraints for LLM guidance:",
        "- Class definitions",
        "- Object property connections (domain → range)",
        "- Datatype property assignments (domain) with rdfs:comment (encoding rules, allowed values, constraints)",
        "- Class hierarchy (inheritance)",
        "- Required creation functions",
        "",
        "---",
        "",
        f"## Namespace",
        "",
        f"`{concise_structure['namespace_uri']}`",
        "",
        "---",
        "",
        "## OM-2 Unit Inventory (T-Box derived)",
        "",
        "If present, this section provides OM-2 unit individuals and their labels from the (mock) OM-2 T-Box.",
        "Use it to build **label → IRI** mappings and strict `Literal[...]` unit parameters in generated code.",
        "",
        "**IMPORTANT**: Do not invent units; only use labels listed here.",
        ""
    ]

    om2_units = concise_structure.get("om2_units")
    if not om2_units:
        lines.append("_No OM-2 unit inventory available._")
        lines.append("")
    else:
        for cat, items in om2_units.items():
            if not items:
                continue
            lines.append(f"### {cat}")
            lines.append("")
            for it in items:
                lines.append(f"- **{it['label']}** → `{it['iri']}`")
            lines.append("")

    integrity_block = _integrity_contract_block_from_concise(
        concise_structure,
        include_machine_readable=True,
    )
    lines.extend([
        "---",
        "",
        "## Ontology-Derived Integrity Contract",
        "",
    ])
    if integrity_block:
        lines.extend(integrity_block.splitlines())
    else:
        lines.append("_No ontology-derived integrity annotations detected._")
    lines.append("")

    lines.extend([
        "---",
        "",
        f"## Classes ({len(concise_structure['classes'])} total)",
        ""
    ])

    for cls in concise_structure['classes']:
        lines.append(f"- `{cls}`")

    # Get class structures for detailed signatures section later
    class_structures = concise_structure.get('class_structures', {})

    # Jump straight to detailed signatures - no misleading summary sections
    lines.extend([
        "",
        "---",
        "",
        "## Create Function Signatures",
        "",
        "**CRITICAL**: Each `create_*` function MUST include ALL parameters listed below.",
        "These are the AUTHORITATIVE signatures - use these EXACTLY when generating code.",
        ""
    ])

    # Add detailed function signature for each class
    for cls in sorted(concise_structure['classes']):
        class_name = cls.split('/')[-1] if '/' in cls else cls
        structure = class_structures.get(cls, {})  # classes list can use compact or namespace-qualified keys

        lines.append(f"### `create_{class_name}` Parameters:")
        lines.append("")
        lines.append("```python")
        lines.append(f"def create_{class_name}(")
        lines.append("    label: str,  # Required")

        # Datatype properties with type inference and schema comments
        datatype_props = structure.get('datatype_inputs', [])
        dt_comments = structure.get('datatype_comments', {})
        for prop in sorted(datatype_props):
            prop_name = prop.split('/')[-1] if '/' in prop else prop

            # Infer type from property name
            if 'Order' in prop_name or 'Count' in prop_name:
                param_type = "Optional[int]"
            elif prop_name.startswith('is') or prop_name.startswith('has') and ('Vacuum' in prop_name or 'Sealed' in prop_name or 'Stirred' in prop_name or 'Repeated' in prop_name or 'Layered' in prop_name or 'Wait' in prop_name or 'Filtration' in prop_name or 'Evaporator' in prop_name):
                param_type = "Optional[bool]"
            elif 'Ph' in prop_name or 'Purity' in prop_name or 'Amount' in prop_name or 'Names' in prop_name or 'Formula' in prop_name or 'Description' in prop_name or 'Parameter' in prop_name or 'Number' in prop_name:
                param_type = "Optional[str]"
            else:
                param_type = "Optional[str]"

            cmt = dt_comments.get(prop_name, "")
            if cmt:
                # Collapse to single line and truncate for readability in signatures
                cmt_single = cmt.replace("\n", " ").replace("\r", " ")
                if len(cmt_single) > 120:
                    cmt_single = cmt_single[:117] + "..."
                lines.append(f"    {prop_name}: {param_type} = None,  # {cmt_single}")
            else:
                lines.append(f"    {prop_name}: {param_type} = None,")

        # Object connections as label parameters for auto-creation
        # Domain-agnostic rule: only add label parameters for target classes that look "auxiliary"
        # by ontology structure (frequently referenced, low outgoing connectivity).
        def _snake(s: str) -> str:
            t = re.sub(r"[^0-9A-Za-z]+", "_", s or "").strip("_")
            # camelCase → snake-ish
            t = re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", t)
            return t.lower() or "entity"

        aux_candidates: set[str] = set()
        for _cls_full, _st in class_structures.items():
            try:
                _simple = _cls_full.split("/")[-1] if "/" in _cls_full else _cls_full
                _connected_from = len((_st or {}).get("connected_from", []) or [])
                _connects_to = len((_st or {}).get("connects_to", []) or [])
                if _connected_from >= 2 and _connects_to <= 2:
                    aux_candidates.add(_simple)
            except Exception:
                continue

        seen_params = set()
        for conn in structure.get("connects_to", []):
            prop = conn["property"].split("/")[-1] if "/" in conn["property"] else conn["property"]
            prop_local = _snake(prop)

            for target in conn.get("target_classes", []) or []:
                target_name = target.split("/")[-1] if "/" in target else target

                # If the ontology mentions OM-2 quantities as ranges, include value+unit parameters.
                om2_quantity_locals = {
                    "Temperature",
                    "Pressure",
                    "Duration",
                    "Volume",
                    "TemperatureRate",
                    "AmountOfSubstanceFraction",
                }
                if target_name in om2_quantity_locals:
                    v_name = f"{prop_local}_value"
                    u_name = f"{prop_local}_unit"
                    if v_name not in seen_params:
                        lines.append(f"    {v_name}: Optional[float] = None,  # OM-2 {target_name} value")
                        seen_params.add(v_name)
                    if u_name not in seen_params:
                        lines.append(f"    {u_name}: Optional[str] = None,  # OM-2 {target_name} unit label (see OM-2 Unit Inventory)")
                        seen_params.add(u_name)
                    continue

                # Auxiliary entity label parameters (only when target looks auxiliary by structure).
                if target_name in aux_candidates:
                    param_name = f"{_snake(target_name)}_label"
                    if param_name not in seen_params:
                        lines.append(f"    {param_name}: Optional[str] = None,  # Auto-created auxiliary entity of type {target_name}")
                        seen_params.add(param_name)

        lines.append(") -> str:")
        lines.append("```")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Class Structures",
        "",
        "Detailed information about connections and inputs for each class.",
        ""
    ])

    for class_name in sorted(concise_structure['classes']):
        structure = concise_structure['class_structures'][class_name]

        lines.append(f"### `{class_name}`")
        lines.append("")

        if structure['parent_classes']:
            lines.append(f"**Inherits from:** {', '.join(f'`{p}`' for p in structure['parent_classes'])}")
            lines.append("")

        if structure['connects_to']:
            lines.append("**Connects to (via object properties):**")
            lines.append("")
            for conn in structure['connects_to']:
                # Reflect referenced external ontologies (e.g., om-2:Temperature) using the captured IRI map.
                ext_map = concise_structure.get("external_range_iris") or {}
                def _fmt_target(t: str) -> str:
                    iri = ext_map.get(t)
                    if not iri:
                        return f"`{t}`"
                    if "ontology-of-units-of-measure.org/resource/om-2/" in iri:
                        return f"`om-2:{t}`"
                    if "/kg/" in iri:
                        namespace = iri.split("/kg/", 1)[1].split("/", 1)[0].strip().lower()
                        if namespace:
                            return f"`{namespace}:{t}`"
                    if "ontocape/material/material.owl" in iri:
                        return f"`ontocape:{t}`"
                    return f"`{t}`"
                targets = ", ".join(_fmt_target(t) for t in conn['target_classes'])
                lines.append(f"- `{conn['property']}` → {targets}")
            lines.append("")

        if structure['connected_from']:
            lines.append("**Connected from (via object properties):**")
            lines.append("")
            for conn in structure['connected_from']:
                sources = ', '.join(f'`{s}`' for s in conn['source_classes'])
                lines.append(f"- `{conn['property']}` ← {sources}")
            lines.append("")

        if structure['datatype_inputs']:
            lines.append("**Datatype properties (inputs/data):**")
            lines.append("")
            dt_cmt_map = structure.get('datatype_comments', {})
            for prop in structure['datatype_inputs']:
                cmt = dt_cmt_map.get(prop, "")
                if cmt:
                    lines.append(f"- `{prop}` — {cmt}")
                else:
                    lines.append(f"- `{prop}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Add statistics at the end
    total_object_props = sum(
        len(s['connects_to']) + len(s['connected_from'])
        for s in concise_structure['class_structures'].values()
    )
    total_datatype_props = sum(
        len(s['datatype_inputs'])
        for s in concise_structure['class_structures'].values()
    )

    lines.extend([
        "## Statistics",
        "",
        f"- **Total Classes:** {len(concise_structure['classes'])}",
        f"- **Total Object Property Connections:** {total_object_props}",
        f"- **Total Datatype Property Assignments:** {total_datatype_props}",
        ""
    ])

    return "\n".join(lines)


def save_concise_structure(
    ontology_path: str,
    ontology_name: str,
    output_base_dir: Optional[Path] = None
) -> Path:
    """
    Extract and save the concise ontology structure as a markdown file.

    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology
        output_base_dir: Base output directory (defaults to ai_generated_contents_candidate)

    Returns:
        Path to the saved markdown file
    """
    if output_base_dir is None:
        output_base_dir = project_root / "ai_generated_contents_candidate"

    # Create ontology_structures subfolder
    structures_dir = output_base_dir / "ontology_structures"
    structures_dir.mkdir(parents=True, exist_ok=True)

    # Extract concise structure.
    # Include OM-2 only for ontologies that actually use it.
    concise_structure = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )

    # Format as markdown
    markdown_content = format_concise_structure_as_markdown(concise_structure, ontology_name)

    # Save to file
    output_path = structures_dir / f"{ontology_name}_concise.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    try:
        contract = build_generation_contract_bundle(ontology_name=ontology_name)
        write_generation_contract_bundle(
            contract,
            structures_dir / f"{ontology_name}_generation_contract.json",
        )
    except Exception as e:
        print(f"   ⚠️  Failed to write generation contract bundle for {ontology_name}: {e}")

    return output_path


def _run_generated_artifact_contract_validation(
    *,
    ontology_name: str,
    output_dir: str,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate generated scripts/prompts against the derived generation contract."""
    try:
        contract = build_generation_contract_bundle(ontology_name=ontology_name)
        report = validate_generated_artifacts(
            scripts_dir=output_dir,
            prompts_dir=Path(output_dir).parent.parent / "prompts" / ontology_name,
            contract_bundle=contract,
        )
    except Exception as e:
        raise ValueError(f"Generation contract validation failed to run: {e}") from e

    report_path = Path(output_dir) / "generation_contract_validation.json"
    try:
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    if not report.get("ok"):
        failures = "; ".join(str(x) for x in (report.get("failures") or []))
        raise ValueError(f"Generated artifact contract validation failed: {failures}")
    warnings = report.get("warnings") or []
    if warnings:
        print(f"   ⚠️  Generation contract validation warnings: {len(warnings)} (see {report_path.name})")
    else:
        print("   ✅ Generation contract validation passed")


def extract_code_from_response(response: str) -> str:
    """Extract Python code from LLM response, removing markdown formatting if present."""

    # Try to extract code from markdown code blocks
    code_block_pattern = r'```(?:python)?\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, response, re.DOTALL)

    if matches:
        # Use the largest code block (likely the main code)
        return _normalize_format_response_calls(max(matches, key=len).strip())

    # If no complete code blocks found, defensively strip stray leading/trailing fences.
    # This prevents syntax errors like:
    #   Syntax error at line 1: invalid syntax
    #   ```python
    #   ^
    s = response.strip()
    if s.startswith("```"):
        # Drop the first fence line (e.g., ```python or ```)
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :].lstrip()
    if s.endswith("```"):
        s = s[: -3].rstrip()

    # If still nothing special, assume the entire response is code.
    return _normalize_format_response_calls(s)


def build_underlying_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating an underlying MCP script using domain-agnostic meta-prompt.

    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Short ontology name from configuration

    Returns:
        Complete prompt string with TTL-extracted information filled into meta-prompt
    """
    # Load domain-agnostic meta-prompt
    meta_prompt_template = load_meta_prompt('direct_underlying_script_prompt.md')

    # Extract CONCISE ontology structure (focused on connections and inputs, no verbose comments).
    # Include OM-2 mock (if present) so unit inventory is available to the LLM strictly via ontology-derived input.
    concise_structure = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )

    # Parse TTL for additional metadata if needed
    tbox_info = parse_ttl_tbox(ontology_path)

    # Load reference snippet for patterns (domain-agnostic patterns)
    ref_script_path = project_root / "sandbox" / "code" / "mcp_creation" / "mcp_creation.py"
    ref_snippet = ""
    if ref_script_path.exists():
        with open(ref_script_path, 'r', encoding='utf-8') as f:
            # Take first 20k chars showing key patterns
            ref_snippet = f.read()[:20000]

    # Format concise ontology structure
    ontology_structure_lines = [
        f"Namespace: {concise_structure['namespace_uri']}",
        "",
        "# Classes",
        *[f"- {cls}" for cls in concise_structure['classes']],
        "",
        "# Class Structures (Connections and Inputs)",
        ""
    ]

    for class_name, structure in sorted(concise_structure['class_structures'].items()):
        ontology_structure_lines.append(f"## {class_name}")

        if structure['parent_classes']:
            ontology_structure_lines.append(f"  Inherits from: {', '.join(structure['parent_classes'])}")

        if structure['connects_to']:
            ontology_structure_lines.append("  Connects to (via object properties):")
            for conn in structure['connects_to']:
                targets = ', '.join(conn['target_classes'])
                ontology_structure_lines.append(f"    - {conn['property']} → {targets}")

        if structure['connected_from']:
            ontology_structure_lines.append("  Connected from (via object properties):")
            for conn in structure['connected_from']:
                sources = ', '.join(conn['source_classes'])
                ontology_structure_lines.append(f"    - {conn['property']} ← {sources}")

        if structure['datatype_inputs']:
            ontology_structure_lines.append("  Datatype properties (inputs/data):")
            for prop in structure['datatype_inputs']:
                ontology_structure_lines.append(f"    - {prop}")

        ontology_structure_lines.append("")

    concise_ontology_str = "\n".join(ontology_structure_lines)

    # Format entity classes (for backward compatibility)
    entity_classes_str = "\n".join(f"- {cls}" for cls in concise_structure['classes'])

    # Format object properties (simplified, from concise structure)
    object_props_list = []
    for class_name, structure in concise_structure['class_structures'].items():
        for conn in structure['connects_to']:
            targets = ', '.join(conn['target_classes'])
            object_props_list.append(f"- {conn['property']}: {class_name} → {targets}")
    object_props_str = "\n".join(sorted(set(object_props_list)))

    # Format datatype properties (simplified, from concise structure)
    datatype_props_list = []
    for class_name, structure in concise_structure['class_structures'].items():
        for prop in structure['datatype_inputs']:
            datatype_props_list.append(f"- {prop}: domain={class_name}")
    datatype_props_str = "\n".join(sorted(set(datatype_props_list)))

    # Format universal_utils functions list
    universal_utils_str = "\n".join(f"- {func}" for func in UNIVERSAL_UTILS_FUNCTIONS)

    # Fill in the meta-prompt template (safe against stray `{...}` from code examples).
    prompt = _format_meta_prompt(
        meta_prompt_template,
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=concise_structure['namespace_uri'],
        reference_snippet=ref_snippet,
        ontology_ttl=concise_ontology_str,  # Use concise structure instead of full TTL
        entity_classes=entity_classes_str,
        object_properties=object_props_str,
        datatype_properties=datatype_props_str,
        universal_utils_functions=universal_utils_str
    )
    integrity_block = _integrity_contract_block_from_concise(concise_structure)
    if integrity_block:
        prompt += "\n\n" + integrity_block

    return prompt


def extract_functions_from_underlying(underlying_script_path: str) -> List[Dict[str, str]]:
    """
    Extract all function signatures from the underlying script.

    Returns:
        List of dictionaries with 'name' and 'signature' keys
    """
    # IMPORTANT: do NOT use a single-line regex here.
    # The generated scripts frequently use multi-line function definitions, e.g.:
    #   def create_Foo(
    #       a: str,
    #       b: Optional[str] = None,
    #   ) -> str:
    # A regex like `def ...(.*?) -> ...:` will miss these.
    code = Path(underlying_script_path).read_text(encoding="utf-8")

    try:
        tree = ast.parse(code, filename=underlying_script_path)
    except SyntaxError:
        # If the underlying script itself doesn't parse, return empty and let callers handle it.
        return []

    functions: list[dict[str, str]] = []

    def _unparse(x) -> str:
        try:
            return ast.unparse(x)
        except Exception:
            return "Any"

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue

        # Produce an explicit one-line signature (params + annotations + defaults).
        # This is what the LLM should use to generate wrappers; bodies are irrelevant.
        a = node.args

        def _fmt_arg(arg_node: ast.arg, default_node=None) -> str:
            ann = ""
            if arg_node.annotation is not None:
                ann = f": {_unparse(arg_node.annotation)}"
            dflt = ""
            if default_node is not None:
                dflt = f" = {_unparse(default_node)}"
            return f"{arg_node.arg}{ann}{dflt}"

        parts: list[str] = []

        # posonly + args share defaults aligned to the tail of (posonly+args)
        pos = a.posonlyargs
        reg = a.args
        combined = pos + reg
        defaults = list(a.defaults)
        default_start = len(combined) - len(defaults)
        for i, argn in enumerate(combined):
            default_node = defaults[i - default_start] if i >= default_start and defaults else None
            parts.append(_fmt_arg(argn, default_node))
        if pos:
            parts.insert(len(pos), "/")

        # varargs / kwonly marker
        if a.vararg is not None:
            va = a.vararg
            ann = f": {_unparse(va.annotation)}" if va.annotation is not None else ""
            parts.append(f"*{va.arg}{ann}")
        elif a.kwonlyargs:
            parts.append("*")

        for kw_arg, kw_def in zip(a.kwonlyargs, a.kw_defaults):
            parts.append(_fmt_arg(kw_arg, kw_def))

        if a.kwarg is not None:
            ka = a.kwarg
            ann = f": {_unparse(ka.annotation)}" if ka.annotation is not None else ""
            parts.append(f"**{ka.arg}{ann}")

        ret = _unparse(node.returns) if node.returns is not None else "Any"
        signature = f"def {node.name}({', '.join([p for p in parts if p])}) -> {ret}:"

        functions.append({"name": node.name, "signature": signature})

    return functions


def build_main_script_prompt(
    ontology_path: str,
    ontology_name: str,
    underlying_script_path: Optional[str] = None,
    base_script_path: Optional[str] = None,
    entity_script_paths: Optional[list] = None,
    checks_script_path: Optional[str] = None,
    relationships_script_path: Optional[str] = None,
    meta_cfg: dict | None = None,
) -> str:
    """
    Build the prompt for generating a FastMCP main script using domain-agnostic meta-prompt.

    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Short ontology name from configuration
        underlying_script_path: Path to single underlying script (legacy, optional)
        base_script_path: Path to base script (for multi-script architecture)
        entity_script_paths: List of paths to entity group scripts (for multi-script architecture)

    Returns:
        Complete prompt string with extracted information filled into meta-prompt
    """
    # Load domain-agnostic meta-prompt
    meta_prompt_template = load_meta_prompt('direct_main_script_prompt.md')

    # Determine architecture
    is_multi_script = base_script_path is not None and entity_script_paths is not None and len(entity_script_paths) > 0

    # Extract CONCISE ontology structure (focused on connections and inputs, no verbose comments)
    concise_structure = extract_concise_ontology_structure(ontology_path)

    # Parse TTL for additional metadata if needed
    tbox_info = parse_ttl_tbox(ontology_path)

    # Reference snippet (keep SMALL; large snippets blow up token budget and introduce irrelevant tools/rules).
    # We intentionally avoid pulling in the full sandbox reference main.py.
    ref_main_snippet = (
        "from fastmcp import FastMCP\n"
        "\n"
        "mcp = FastMCP(\"<ontology_name>\")\n"
        "\n"
        "@mcp.prompt(name=\"instruction\")\n"
        "def instruction_prompt():\n"
        "    return INSTRUCTION_PROMPT\n"
        "\n"
        "# ... @mcp.tool wrappers delegating to imported functions ...\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    mcp.run(transport=\"stdio\")\n"
    )

    # Format concise ontology structure (simplified version for main.py)
    ontology_structure_lines = [
        f"Namespace: {concise_structure['namespace_uri']}",
        "",
        "# Entity Classes",
        *[f"- {cls}" for cls in concise_structure['classes']],
        "",
        "# Key Relationships (Object Properties)",
        ""
    ]

    # Collect all unique object property relationships
    relationships = set()
    for class_name, structure in concise_structure['class_structures'].items():
        for conn in structure['connects_to']:
            targets = ', '.join(conn['target_classes'])
            relationships.add(f"- {conn['property']}: {class_name} → {targets}")

    ontology_structure_lines.extend(sorted(relationships))
    concise_ontology_str = "\n".join(ontology_structure_lines)

    # Extract function signatures from underlying script(s)
    functions: list[dict] = []
    if is_multi_script:
        # Multi-script architecture: extract from base + checks + relationships + all entity group scripts
        base_functions = extract_functions_from_underlying(base_script_path)
        functions.extend(base_functions)

        if checks_script_path:
            functions.extend(extract_functions_from_underlying(checks_script_path))

        if relationships_script_path:
            functions.extend(extract_functions_from_underlying(relationships_script_path))

        for entity_script_path in entity_script_paths:
            entity_functions = extract_functions_from_underlying(entity_script_path)
            functions.extend(entity_functions)
    elif underlying_script_path:
        # Single file architecture (legacy)
        functions = extract_functions_from_underlying(underlying_script_path)
    else:
        raise ValueError("Either (base_script_path + entity_script_paths) or underlying_script_path must be provided")

    # Build a CONCISE function inventory. Avoid dumping long ontology class/property blocks.
    # We still need to expose check_existing_* and add_* tools in main.py.
    seen_names: set[str] = set()
    base_funcs: list[dict] = []
    check_funcs: list[dict] = []
    create_funcs: list[dict] = []
    rel_funcs: list[dict] = []
    other_funcs: list[dict] = []

    for func in functions:
        name = func["name"]
        if name in seen_names:
            continue
        seen_names.add(name)
        if name in {"init_memory_wrapper", "export_memory_wrapper"}:
            base_funcs.append(func)
        elif name.startswith("check_existing_"):
            check_funcs.append(func)
        elif name.startswith("create_"):
            create_funcs.append(func)
        elif name.startswith("add_") or name in {"add_relation", "list_relation_properties"}:
            rel_funcs.append(func)
        else:
            other_funcs.append(func)

    def _lines_with_sigs(items: list[dict]) -> list[str]:
        return [f"- {it['signature']}" for it in items]

    def _lines_names_only(items: list[dict]) -> list[str]:
        return [f"- {it['name']}" for it in items]

    function_sigs_str = "\n".join(
        [
            f"Total public functions: {len(seen_names)}",
            "NOTE: Function bodies are intentionally omitted.",
            "",
            "### Memory / session wrappers (use exact signatures)",
            *(_lines_with_sigs(sorted(base_funcs, key=lambda x: x['name'])) or ["- (none)"]),
            "",
            "### Checks (use exact signatures; wrappers must call underscored alias)",
            *(_lines_with_sigs(sorted(check_funcs, key=lambda x: x['name'])) or ["- (none)"]),
            "",
            "### Entity creation (use exact signatures)",
            *(_lines_with_sigs(sorted(create_funcs, key=lambda x: x['name'])) or ["- (none)"]),
            "",
            "### Relationship/connect tools (use exact signatures; NO *args/**kwargs)",
            *(_lines_with_sigs(sorted(rel_funcs, key=lambda x: x['name'])) or ["- (none)"]),
            "",
            "### Other public functions (if any)",
            *(_lines_with_sigs(sorted(other_funcs, key=lambda x: x['name'])) or ["- (none)"]),
        ]
    ).strip()

    # Format entity classes
    entity_classes_str = "\n".join(f"- {cls}" for cls in concise_structure['classes'])

    # Format relationships (simplified)
    relationships_str = "\n".join(sorted(relationships))

    # Add architecture-specific info
    if is_multi_script:
        entity_script_list = "\n".join([
            f"- `{Path(path).name}`: {Path(path).stem.replace(f'{ontology_name}_creation_', '')} entities"
            for path in entity_script_paths
        ])

        architecture_note = f"""
**ARCHITECTURE: MULTI-SCRIPT (BASE + {len(entity_script_paths)} ENTITY GROUPS)**

Base script (`{Path(base_script_path).name}`):
- check_existing_* functions
- add_*_to_* relationship functions
- _find_or_create_* helper functions
- Memory management wrappers (init_memory, export_memory)

Entity group scripts ({len(entity_script_paths)} files):
{entity_script_list}

**IMPORTANT**: Import functions from ALL scripts in main.py:
```python
from .{Path(base_script_path).stem} import (
    # check_existing, add_*, memory functions
)

# Import create_* functions from each entity group
{chr(10).join([f'from .{Path(path).stem} import (...)' for path in entity_script_paths])}
```
"""
    elif underlying_script_path:
        architecture_note = f"**ARCHITECTURE: SINGLE SCRIPT** (`{Path(underlying_script_path).name}`)"
    else:
        architecture_note = "**ARCHITECTURE: UNKNOWN** (No scripts provided)"

    # Hard requirements to reduce common runtime/import failures in generated FastMCP servers.
    must_use_imports = """
## CRITICAL MUST-FOLLOW RULES (to avoid runtime failures)

### A) Required imports (must appear near the top of the file)

```python
from fastmcp import FastMCP
from typing import Optional
```

- Do NOT use: `from __future__ import annotations`

### B) Instruction prompt API compatibility (FastMCP 2.x)

Do NOT call `mcp.set_initial_instructions(...)` unless you guard it:

```python
if hasattr(mcp, "set_initial_instructions"):
    mcp.set_initial_instructions(INSTRUCTION_PROMPT)
else:
    @mcp.prompt(name="instruction")
    def instruction_prompt():
        return INSTRUCTION_PROMPT
```

### C) Do not start the server on import

Only run the server in:

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```
""".strip()

    # Fill in the meta-prompt template (safe against stray `{...}` from code examples).
    prompt = _format_meta_prompt(
        meta_prompt_template,
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=concise_structure['namespace_uri'],
        reference_main_snippet=ref_main_snippet,
        ontology_ttl=concise_ontology_str,  # Use concise structure instead of full TTL
        function_signatures=function_sigs_str,
        total_functions=len(functions),
        entity_classes=entity_classes_str,
        relationships=relationships_str,
        architecture_note=architecture_note + "\n\n" + must_use_imports
    )
    integrity_block = _integrity_contract_block_from_concise(concise_structure)
    if integrity_block:
        prompt += "\n\n" + integrity_block
    mb = _format_main_entity_runtime_policy_for_mcp_prompt(meta_cfg, ontology_name)
    if mb:
        prompt = prompt + "\n\n" + mb
    return prompt


def build_base_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating the BASE/INFRASTRUCTURE script (guard system, namespaces, helpers ONLY).

    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Short ontology name from configuration

    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_base_script_prompt.md')

    # Extract concise ontology structure (minimal - just namespace and classes for _find_or_create helpers).
    uses_om2 = _ontology_uses_om2_units(ontology_path, ontology_name)
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=uses_om2)

    # Identify common auxiliary entities that need _find_or_create helpers
    # These are typically entities that are often created as side-effects of main entity creation
    class_structures = concise_structure.get('class_structures', {})
    auxiliary_entities = []

    # Heuristic: entities that are range of many properties but don't have many properties themselves
    for cls_name, structure in class_structures.items():
        simple_name = cls_name.split('/')[-1]
        # Check if this entity is frequently referenced (connected_from count)
        connected_from_count = len(structure.get('connected_from', []))
        connects_to_count = len(structure.get('connects_to', []))

        # If it's frequently referenced but doesn't have many outgoing connections, it's likely auxiliary
        if connected_from_count >= 2 and connects_to_count <= 2:
            auxiliary_entities.append(simple_name)

    auxiliary_entities_str = "\n".join([f"- {entity}" for entity in sorted(set(auxiliary_entities))])

    # Include OM-2 unit inventory (if present) so the LLM can derive unit enforcement + label→IRI mapping.
    om2_units = concise_structure.get("om2_units") or {}
    om2_lines: list[str] = []
    for cat, items in (om2_units.items() if isinstance(om2_units, dict) else []):
        if not items:
            continue
        om2_lines.append(f"{cat}:")
        for it in items:
            om2_lines.append(f"- {it.get('label')} -> {it.get('iri')}")
        om2_lines.append("")
    om2_block = "\n".join(om2_lines).strip() if om2_lines else "(none)"

    # Fill template (safe against stray `{...}` from code examples).
    prompt = _format_meta_prompt(
        meta_prompt_template,
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=concise_structure['namespace_uri'],
        ontology_structure=(
            f"Auxiliary entities (need _find_or_create_ helpers):\n{auxiliary_entities_str}\n\n"
            + (
                f"OM-2 Unit Inventory (ontology-derived; use to build unit enforcement + label→IRI mapping):\n{om2_block}"
                if uses_om2
                else "OM-2 Unit Inventory: (disabled for this ontology)"
            )
        ),
        universal_utils_functions=", ".join(UNIVERSAL_UTILS_FUNCTIONS)
    )

    # Enforce namespace correctness deterministically via a contract block (config-driven).
    prompt += "\n\n" + _namespace_contract_block(concise_structure, ontology_name)
    integrity_block = _integrity_contract_block_from_concise(concise_structure)
    if integrity_block:
        prompt += "\n\n" + integrity_block

    prompt += (
        "\n\nBASE RUNTIME CONTRACT (MUST FOLLOW EXACTLY):\n"
        "- Define one stable uppercase alias that points to the primary namespace, i.e. `<PRIMARY_NS_ALIAS> = NAMESPACE`.\n"
        "- Implement `def _coerce_iri(iri: str) -> URIRef` in the base module.\n"
        "- `_coerce_iri` must strip the input, reject empty strings, and return `URIRef(s)`.\n"
        "- Implement `def _guard_iri(iri: str, role: str = 'iri') -> tuple[Optional[URIRef], Optional[str]]` as a thin wrapper over `_coerce_iri`.\n"
    )

    # Strengthen OM-2 unit handling deterministically (domain-agnostic).
    # This reduces LLM variability and prevents signature/call-style mismatches across modules.
    if uses_om2 and om2_lines:
        prompt += "\n\n" + _OM2_HELPERS_CONTRACT

    return prompt


def build_entity_group_prompt(
    ontology_path: str,
    ontology_name: str,
    group_info: dict,
    available_helpers: list = None,
    available_check_functions: list = None,
    available_add_functions: list = None
) -> str:
    """
    Build the prompt for generating a single entity group script (subset of entities).

    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology
        group_info: Dictionary with 'name', 'entities', 'description'
        available_helpers: List of _find_or_create_* helper function names from base script
        available_check_functions: List of check_existing_* function names from base script
        available_add_functions: List of add_* function names from base script

    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_entities_script_prompt.md')

    # Default to empty lists if not provided
    if available_helpers is None:
        available_helpers = []
    if available_check_functions is None:
        available_check_functions = []
    if available_add_functions is None:
        available_add_functions = []

    # Extract concise ontology structure; include OM-2 only for ontologies that actually use it.
    full_concise_structure = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )

    # Filter to only include entities in this group
    entity_names = set(group_info['entities'])

    # Build filtered structure
    ontology_structure_lines = []
    ontology_structure_lines.append(f"Namespace: {full_concise_structure['namespace_uri']}")
    ontology_structure_lines.append("")
    ontology_structure_lines.append(f"Entity Group: {group_info['name']}")
    ontology_structure_lines.append(f"Description: {group_info['description']}")
    ontology_structure_lines.append(f"Entities in this group: {len(entity_names)}")
    ontology_structure_lines.append("")

    class_structures = full_concise_structure.get('class_structures', {})
    for class_name, structure in sorted(class_structures.items()):
        # Only include classes in this group
        if class_name not in entity_names:
            continue

        ontology_structure_lines.append(f"## {class_name}")

        if structure['parent_classes']:
            ontology_structure_lines.append(f"  Inherits from: {', '.join(structure['parent_classes'])}")

        if structure['datatype_inputs']:
            ontology_structure_lines.append(f"  Datatype properties:")
            for prop in structure['datatype_inputs']:
                ontology_structure_lines.append(f"    - {prop}")

        if structure['connects_to']:
            ontology_structure_lines.append(f"  Object property connections:")
            for conn in structure['connects_to']:
                targets = ", ".join(conn.get("target_classes", []) or [])
                ontology_structure_lines.append(f"    - {conn['property']} → {targets}")

        ontology_structure_lines.append("")

    ontology_structure = "\n".join(ontology_structure_lines)

    # Format available functions from base script
    available_helpers_str = "\n".join([f"- {name}" for name in sorted(available_helpers)]) if available_helpers else "(none available)"
    available_checks_str = "\n".join([f"- {name}" for name in sorted(available_check_functions)]) if available_check_functions else "(none available)"
    available_adds_str = "\n".join([f"- {name}" for name in sorted(available_add_functions)]) if available_add_functions else "(none available)"

    # Fill in template (safe against stray `{...}` from code examples).
    prompt = _format_meta_prompt(
        meta_prompt_template,
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        namespace_uri=full_concise_structure['namespace_uri'],
        ontology_structure=ontology_structure,
        universal_utils_functions=", ".join(UNIVERSAL_UTILS_FUNCTIONS),
        group_name=group_info['name'],
        group_description=group_info['description'],
        entity_count=len(entity_names),
        entity_classes_list=", ".join(sorted(entity_names)),
        available_helpers=available_helpers_str,
        available_check_functions=available_checks_str,
        available_add_functions=available_adds_str
    )

    top_entity_contract = _resolve_top_entity_codegen_contract(ontology_name=ontology_name)
    top_entity_block = _build_top_entity_codegen_prompt_block(
        top_entity_contract=top_entity_contract,
        class_names=[str(c).strip() for c in (group_info.get("entities") or [])],
    )
    if top_entity_block:
        prompt += "\n\n" + top_entity_block

    # Enforce namespace correctness deterministically via a config-driven contract block.
    prompt += "\n\n" + _namespace_contract_block(full_concise_structure, ontology_name)
    integrity_block = _integrity_contract_block_from_concise(full_concise_structure)
    if integrity_block:
        prompt += "\n\n" + integrity_block

    # Enforce OM-2 call style contract only when OM-2 is enabled for this ontology.
    if _ontology_uses_om2_units(ontology_path, ontology_name):
        prompt += "\n\n" + _OM2_HELPERS_CONTRACT

    # Optional: inject blurred reference example to stabilize structure without domain leakage.
    try:
        from pathlib import Path as _Path
        ex_dir = _Path(__file__).resolve().parent / "mock_examples"
        ex_entity = (ex_dir / "entity_creation_blurred_example.py").read_text(encoding="utf-8")
        prompt += (
            "\n\nBLURRED REFERENCE EXAMPLE (copy STRUCTURE, not names):\n"
            "```python\n"
            + ex_entity
            + "\n```"
        )
    except Exception:
        pass

    return prompt


def build_entities_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating the ENTITIES script (all create_* functions).

    DEPRECATED: Use build_entity_group_prompt for multi-script generation.

    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Short ontology name from configuration

    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_entities_script_prompt.md')

    # Extract concise ontology structure (include OM-2 mock so referenced external concepts are visible)
    concise_structure = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )

    # Format class structures with full details
    ontology_structure_lines = []
    ontology_structure_lines.append(f"Namespace: {concise_structure['namespace_uri']}")
    ontology_structure_lines.append("")
    ontology_structure_lines.append(f"Total Classes: {len(concise_structure['classes'])}")
    ontology_structure_lines.append("")

    class_structures = concise_structure.get('class_structures', {})
    for class_name, structure in sorted(class_structures.items()):
        ontology_structure_lines.append(f"## {class_name}")

        if structure['parent_classes']:
            ontology_structure_lines.append(f"  Inherits from: {', '.join(structure['parent_classes'])}")

        if structure['datatype_inputs']:
            ontology_structure_lines.append("  Datatype properties:")
            for prop in structure['datatype_inputs']:
                ontology_structure_lines.append(f"    - {prop}")

        if structure['connects_to']:
            ontology_structure_lines.append("  Object properties:")
            for conn in structure['connects_to']:
                targets = ', '.join(conn['target_classes'])
                ontology_structure_lines.append(f"    - {conn['property']} → {targets}")

        ontology_structure_lines.append("")

    ontology_structure = "\n".join(ontology_structure_lines)

    # Create explicit list of all classes for verification
    entity_classes_list = "\n".join([f"- {cls.split('/')[-1]}" for cls in sorted(concise_structure['classes'])])

    # Fill template (safe against stray `{...}` from code examples).
    prompt = _format_meta_prompt(
        meta_prompt_template,
        ontology_name=ontology_name,
        script_name=f"{ontology_name}_creation",
        ontology_structure=ontology_structure,
        entity_classes_list=entity_classes_list
    )
    top_entity_contract = _resolve_top_entity_codegen_contract(ontology_name=ontology_name)
    top_entity_block = _build_top_entity_codegen_prompt_block(
        top_entity_contract=top_entity_contract,
        class_names=[str(c).split("/")[-1] for c in sorted(concise_structure['classes'])],
    )
    if top_entity_block:
        prompt += "\n\n" + top_entity_block
    integrity_block = _integrity_contract_block_from_concise(concise_structure)
    if integrity_block:
        prompt += "\n\n" + integrity_block

    return prompt


async def generate_base_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate the BASE script (checks, relationships, helpers) using direct LLM calls.

    Returns:
        Path to generated base script
    """
    print(f"\n📝 [1/2] Generating BASE script (checks, relationships, helpers)...")
    print(f"   Model: {model_name}")

    # Build prompt
    prompt = build_base_script_prompt(ontology_path, ontology_name)

    # Create OpenAI client
    client = create_openai_client()

    # Call LLM
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}...")

            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development."},
                    {"role": "user", "content": prompt}
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000)
            )

            # Extract code
            content = response.choices[0].message.content
            code = extract_code_from_response(content)

            # Post-process: enforce namespace constants deterministically
            concise_structure = extract_concise_ontology_structure(
                ontology_path,
                include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
            )
            code = _apply_namespace_contract_to_code(code, concise_structure)
            code = _normalize_base_runtime_contracts(
                code,
                ontology_name,
                ontology_path=ontology_path,
                meta_cfg=meta_cfg,
            )

            attempt_path = Path(output_dir) / f"{ontology_name}_creation_base_attempt_{attempt}.py"
            attempt_path.parent.mkdir(parents=True, exist_ok=True)
            attempt_path.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")

            # Validate syntax before writing
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_base.py")
            if not is_valid:
                raise ValueError(f"Syntax: {syntax_error}")

            # OM-2 contract validation (prevents downstream unit-handling breakage).
            if _ontology_uses_om2_units(ontology_path, ontology_name):
                ok_om2, om2_err = _validate_om2_base_contract(code)
                if not ok_om2:
                    raise ValueError(f"OM-2 base contract violation: {om2_err}")

            ok_base_contract, base_contract_err = _validate_base_runtime_contracts(code, ontology_name)
            if not ok_base_contract:
                raise ValueError(f"Base runtime contract violation: {base_contract_err}")

            # Write to file
            output_path = Path(output_dir) / f"{ontology_name}_creation_base.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)

            print(f"   ✓ Generated: {output_path.name}")
            return str(output_path)

        except Exception as e:
            last_exception = e
            print(f"   ✗ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)

    raise Exception(f"Failed to generate base script after {max_retries} attempts: {last_exception}")


async def generate_entity_group_script_direct(
    ontology_path: str,
    ontology_name: str,
    group_info: dict,
    output_dir: str,
    base_script_path: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3
) -> str:
    """
    Generate a single entity group script (subset of all entities).

    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        group_info: Dictionary with 'name', 'entities', 'script_name', 'description'
        output_dir: Directory to write the generated script
        base_script_path: Path to the base script (to extract available functions)
        model_name: LLM model to use
        max_retries: Number of retry attempts

    Returns:
        Path to generated script
    """
    print(f"\n📝 Generating entity group script: {group_info['name']}")
    print(f"   Entities: {', '.join(group_info['entities'])}")
    print(f"   Output: {group_info['script_name']}")

    # Extract functions from base script to know what's available
    base_functions = extract_functions_from_underlying(base_script_path)
    available_helpers = [f['name'] for f in base_functions if f['name'].startswith('_find_or_create_')]
    available_check_functions = [f['name'] for f in base_functions if f['name'].startswith('check_existing_')]
    available_add_functions = [f['name'] for f in base_functions if f['name'].startswith('add_')]

    print(f"   Available helpers: {len(available_helpers)} _find_or_create_* functions")
    print(f"   Available checks: {len(available_check_functions)} check_existing_* functions")

    # Build prompt for this specific group
    prompt = build_entity_group_prompt(
        ontology_path,
        ontology_name,
        group_info,
        available_helpers=available_helpers,
        available_check_functions=available_check_functions,
        available_add_functions=available_add_functions
    )

    # Create OpenAI client
    client = create_openai_client()

    # Call LLM with retries
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            print(f"   🔄 Attempt {attempt}/{max_retries}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in ontology-based code generation."},
                    {"role": "user", "content": prompt}
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000)
            )

            code = response.choices[0].message.content.strip()

            # Clean code fences if present
            if code.startswith("```"):
                lines = code.split("\n")
                code = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            code = _normalize_entity_script_contracts(code, ontology_name)

            # Validate syntax
            is_valid, syntax_error = validate_python_syntax(code, group_info["script_name"])
            if not is_valid:
                raise ValueError(f"Syntax: {syntax_error}")

            # Extra semantic guardrails: entity scripts must not duplicate OM-2 unit tables / OM-2 helpers.
            forbidden_markers = [
                "_TEMPERATURE_UNITS",
                "_PRESSURE_UNITS",
                "_DURATION_UNITS",
                "_VOLUME_UNITS",
                "_TEMPERATURE_RATE_UNITS",
                "_AMOUNT_FRACTION_UNITS",
                "_TEMPERATURE_UNIT_MAP",
                "_PRESSURE_UNIT_MAP",
                "_DURATION_UNIT_MAP",
                "_VOLUME_UNIT_MAP",
                "_TEMPERATURE_RATE_UNIT_MAP",
                "_AMOUNT_OF_SUBSTANCE_FRACTION_UNIT_MAP",
            ]
            if any(m in code for m in forbidden_markers):
                raise ValueError(
                    "Entity script duplicated OM-2 unit tables; must import/use OM2_UNIT_MAP + _find_or_create_om2_quantity from base."
                )

            # OM-2 call style validation (prevents passing unit IRIs / positional args to base helper).
            if _ontology_uses_om2_units(ontology_path, ontology_name):
                ok_calls, call_err = _validate_om2_entity_call_style(code)
                if not ok_calls:
                    raise ValueError(f"OM-2 entity call-style violation: {call_err}")

            top_entity_contract = _resolve_top_entity_codegen_contract(ontology_name=ontology_name)
            ok_top, top_err = _validate_top_entity_create_contract(
                code,
                top_entity_contract=top_entity_contract,
            )
            if not ok_top:
                raise ValueError(f"Top-entity create contract violation: {top_err}")

            # Write to file
            output_path = Path(output_dir) / group_info['script_name']
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)

            print(f"   ✓ Generated: {output_path.name} ({len(group_info['entities'])} entities)")
            return str(output_path)

        except Exception as e:
            last_exception = e
            print(f"   ✗ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)

    raise Exception(f"Failed to generate {group_info['name']} script after {max_retries} attempts: {last_exception}")


async def generate_entities_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    base_script_path: str,
    checks_script_path: str,
    relationships_script_path: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> list:
    """
    Generate 2 entity creation scripts (create_* functions split in half).

    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        output_dir: Directory to write scripts
        base_script_path: Path to base utilities script
        checks_script_path: Path to checks script
        relationships_script_path: Path to relationships script
        model_name: LLM model to use
        max_retries: Number of retry attempts

    Returns:
        List of paths to 2 generated entity scripts
    """
    print(f"   Generating entity creation scripts (2 parts)...")
    print(f"   Model: {model_name}")

    # Extract concise ontology to get all classes (include OM-2 mock so referenced external concepts are visible)
    concise_structure = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )
    all_classes = sorted(concise_structure['classes'])

    # Split classes into 2 equal groups
    mid_point = len(all_classes) // 2
    group_1_classes = all_classes[:mid_point]
    group_2_classes = all_classes[mid_point:]

    print(f"   Part 1: {len(group_1_classes)} classes")
    print(f"   Part 2: {len(group_2_classes)} classes")

    # Generate both scripts
    generated_scripts = []

    for part_num, classes in [(1, group_1_classes), (2, group_2_classes)]:
        print(f"\n   [{part_num}/2] Generating entities part {part_num}...")
        script_path = await generate_entity_part_script(
            ontology_path=ontology_path,
            ontology_name=ontology_name,
            part_number=part_num,
            classes_to_generate=classes,
            output_dir=output_dir,
            base_script_path=base_script_path,
            checks_script_path=checks_script_path,
            relationships_script_path=relationships_script_path,
            model_name=model_name,
            max_retries=max_retries,
            meta_cfg=meta_cfg,
        )
        generated_scripts.append(script_path)

    print(f"\n   ✅ Generated 2 entity creation scripts")
    return generated_scripts


def repair_generated_entity_scripts(
    ontology_name: str,
    entity_script_paths: list[str],
) -> list[str]:
    """
    Re-apply deterministic entity-script normalization to already generated files.

    This is intended for conservative recovery after upstream normalizers improve:
    we avoid hand-editing generated artifacts, but still let the generation pipeline
    re-canonicalize public tool signatures and other runtime contracts in place.
    """
    repaired_paths: list[str] = []
    for raw_path in entity_script_paths or []:
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Entity script not found: {path}")
        original = path.read_text(encoding="utf-8")
        normalized = _normalize_entity_script_contracts(original, ontology_name)
        syntax_ok, syntax_err = validate_python_syntax(normalized, str(path))
        if not syntax_ok:
            raise ValueError(f"Normalized entity script is invalid for {path.name}: {syntax_err}")
        if normalized != original:
            path.write_text(normalized.rstrip() + "\n", encoding="utf-8")
            print(f"   [REPAIR] Updated entity script contracts: {path.name}")
        else:
            print(f"   [REPAIR] No entity-script changes needed: {path.name}")
        repaired_paths.append(str(path))
    return repaired_paths


async def generate_entities_script_direct_legacy(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3
) -> str:
    """
    LEGACY: Generate the ENTITIES script (all create_* functions) using direct LLM calls.

    DEPRECATED: Use generate_entities_script_direct for multi-group generation.

    Returns:
        Path to generated entities script
    """
    print(f"\n📝 [2/2] Generating ENTITIES script (all create_* functions)...")
    print(f"   Model: {model_name}")

    # Build prompt
    prompt = build_entities_script_prompt(ontology_path, ontology_name)

    # Create OpenAI client
    client = create_openai_client()

    # Call LLM
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}...")

            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development. Generate ALL create functions - no shortcuts, no placeholders."},
                    {"role": "user", "content": prompt}
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000)
            )

            # Extract code
            content = response.choices[0].message.content
            code = extract_code_from_response(content)
            code = _normalize_entity_script_contracts(code, ontology_name)

            # Write to file
            output_path = Path(output_dir) / f"{ontology_name}_creation_entities.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)

            print(f"   ✓ Generated: {output_path.name}")
            return str(output_path)

        except Exception as e:
            last_exception = e
            print(f"   ✗ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)

    raise Exception(f"Failed to generate entities script after {max_retries} attempts: {last_exception}")


def create_entity_breakdown_plan(ontology_path: str, ontology_name: str, output_dir: str) -> dict:
    """
    Analyze ontology and create a structured plan for breaking down entity generation.

    Groups entities by semantic category to keep each generated script manageable (~300-500 lines).

    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Name of ontology
        output_dir: Output directory for plan file

    Returns:
        Dictionary containing the breakdown plan
    """
    import json
    from pathlib import Path

    # Parse ontology to get class list
    concise_structure = extract_concise_ontology_structure(ontology_path)
    classes = concise_structure["classes"]
    class_structures = concise_structure["class_structures"]

    # Domain-agnostic grouping: chunk classes into stable groups using ontology structure only.
    # (No hardcoded class/property/entity keywords.)
    simple_classes: List[str] = []
    for cls_full in classes:
        cls_name = cls_full.split("/")[-1] if "/" in cls_full else cls_full
        if cls_name:
            simple_classes.append(cls_name)
    simple_classes = sorted(set(simple_classes))

    max_per_group = 10
    groups: List[List[str]] = []
    for i in range(0, len(simple_classes), max_per_group):
        groups.append(simple_classes[i : i + max_per_group])

    plan = {
        "ontology": ontology_name,
        "total_entities": len(classes),
        "groups": []
    }

    for idx, group in enumerate(groups, 1):
        plan["groups"].append(
            {
                "name": f"group_{idx}",
                "description": f"Auto-grouped entity batch {idx}",
                "entities": group,
                "script_name": f"{ontology_name}_creation_entities_{idx}.py",
            }
        )

    # Save plan to JSON
    plan_path = Path(output_dir) / f"{ontology_name}_entity_breakdown.json"
    with open(plan_path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, indent=2)

    print(f"   📋 Created entity breakdown plan: {plan_path.name}")
    print(f"      Total entities: {plan['total_entities']}")
    print(f"      Number of groups: {len(plan['groups'])}")
    for group in plan["groups"]:
        print(f"      - {group['name']}: {len(group['entities'])} entities → {group['script_name']}")

    return plan


async def generate_underlying_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3
) -> str:
    """
    Generate an underlying MCP script using direct LLM calls with domain-agnostic meta-prompts.

    NOTE: This function is now used for generating the BASE script only.
    Entity creation functions are split across multiple scripts via generate_entity_group_script_direct().

    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short ontology name from configuration
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts for API calls

    Returns:
        Path to generated base script
    """
    print(f"\n📝 Generating underlying script via direct LLM call (domain-agnostic mode)...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")

    # Save concise ontology structure as markdown
    output_base_dir = Path(output_dir).parent.parent  # Go up to ai_generated_contents_candidate
    concise_md_path = save_concise_structure(ontology_path, ontology_name, output_base_dir)
    print(f"   📄 Saved concise ontology structure: {concise_md_path.name}")

    # Build prompt using domain-agnostic meta-prompt + TTL parsing
    prompt = build_underlying_script_prompt(ontology_path, ontology_name)

    # Create OpenAI client
    client = create_openai_client()

    # Call LLM API with retries
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry attempt {attempt}/{max_retries}...")

            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert Python developer specializing in RDF/semantic web and MCP server development. Generate code based on T-Box ontology structure."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000)
            )

            # Extract code from response
            code = extract_code_from_response(response.choices[0].message.content or "")
            code = _patch_fastmcp_instruction_compat(code)

            if not code:
                raise ValueError("LLM returned empty response")

            # Write to file
            output_path = Path(output_dir) / f"{ontology_name}_creation.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)

            print(f"   ✅ Generated: {output_path}")
            print(f"   📊 Size: {len(code)} characters")

            return str(output_path)

        except Exception as e:
            last_exception = e
            print(f"   ⚠️  Attempt {attempt} failed: {e}")

            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff

    # All retries failed
    raise Exception(f"Failed to generate script after {max_retries} attempts: {last_exception}")


async def generate_main_script_direct(
    ontology_path: str,
    ontology_name: str,
    checks_script_path: str,
    relationships_script_path: str,
    base_script_path: str,
    entity_script_paths: list,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3,
    meta_cfg: dict | None = None,
) -> str:
    """
    Generate a FastMCP main script using direct LLM calls with domain-agnostic meta-prompts.

    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology
        checks_script_path: Path to checks script
        relationships_script_path: Path to relationships script
        base_script_path: Path to base script
        entity_script_paths: List of paths to entity group scripts
        output_dir: Directory to write the generated script
        model_name: LLM model to use
        max_retries: Number of retry attempts

    Returns:
        Path to generated script
    """
    print(f"\n📝 [FINAL] Generating main.py ...")
    print(f"   Ontology: {ontology_name}")
    print(f"   Model: {model_name}")
    print(f"   Output: {output_dir}")
    print(f"   Architecture: MULTI-SCRIPT")
    print(f"      - Checks: {Path(checks_script_path).name}")
    print(f"      - Relationships: {Path(relationships_script_path).name}")
    print(f"      - Base: {Path(base_script_path).name}")
    print(f"      - Entity scripts: {len(entity_script_paths)}")
    for idx, path in enumerate(entity_script_paths, 1):
        print(f"         {idx}. {Path(path).name}")

    # Deterministic main.py generation: import/wrap exactly what exists in the
    # underlying modules. This avoids recurrent alias drift between generated
    # helper names and their public wrappers.
    deterministic_path = _build_main_py_deterministic(
        ontology_name=ontology_name,
        checks_script_path=checks_script_path,
        relationships_script_path=relationships_script_path,
        base_script_path=base_script_path,
        entity_script_paths=list(entity_script_paths),
        output_dir=output_dir,
        meta_cfg=meta_cfg,
    )
    deterministic_code = Path(deterministic_path).read_text(encoding="utf-8")
    syntax_ok, syntax_err = validate_python_syntax(deterministic_code, deterministic_path)
    if not syntax_ok:
        raise ValueError(f"Deterministic main.py syntax invalid: {syntax_err}")
    all_script_paths = [checks_script_path, relationships_script_path, base_script_path] + entity_script_paths
    owners = _function_owner_map(all_script_paths)
    imports_ok, imports_err = _validate_imported_function_names_exist(
        deterministic_code,
        owners,
        Path(deterministic_path).name,
    )
    if not imports_ok:
        raise ValueError(f"Deterministic main.py import mismatch: {imports_err}")
    _run_generated_artifact_contract_validation(
        ontology_name=ontology_name,
        output_dir=output_dir,
        meta_cfg=meta_cfg,
    )
    print(f"   ✅ Deterministic main.py generated: {deterministic_path}")
    return deterministic_path

    # LLM-direct main.py generation (no agent tooling).
    # Combine all foundational scripts for validation only
    all_script_paths = [checks_script_path, relationships_script_path, base_script_path] + entity_script_paths

    # Build prompt using domain-agnostic meta-prompt + TTL parsing.
    # IMPORTANT: pass only entity group scripts in `entity_script_paths` (NOT checks/base/relationships),
    # otherwise we duplicate function inventories and confuse the model.
    prompt = build_main_script_prompt(
        ontology_path,
        ontology_name,
        underlying_script_path=None,  # Not used in new architecture
        base_script_path=base_script_path,
        entity_script_paths=entity_script_paths,  # entity group scripts only
        checks_script_path=checks_script_path,
        relationships_script_path=relationships_script_path,
        meta_cfg=meta_cfg,
    )

    # Add a short, explicit rule block to prevent the recurring alias mismatch bug.
    checks_mod = Path(checks_script_path).with_suffix("").name
    rel_mod = Path(relationships_script_path).with_suffix("").name
    base_mod = Path(base_script_path).with_suffix("").name
    ent_mods = [Path(p).with_suffix("").name for p in entity_script_paths]
    prompt += (
        "\n\n"
        "## CRITICAL NON-NEGOTIABLE RULES (fix these exact past failures)\n"
        "1) ALWAYS import underlying functions using an underscored alias: `foo as _foo`.\n"
        "2) EVERY wrapper MUST delegate to the underscored alias (never call the wrapper itself).\n"
        "   BAD: `def create_Add(...): return create_Add(...)`\n"
        "   GOOD: `def create_Add(...): return _create_Add(...)`\n"
        "3) If you import `export_memory_wrapper as _export_memory_wrapper`, then wrapper `export_memory()` MUST call `_export_memory_wrapper()`.\n"
        "   Do NOT call `export_memory_wrapper()`.\n"
        "4) Import grouping for this repo (multi-script):\n"
        f"   - Checks come from `.{checks_mod}` (check_existing_* only)\n"
        f"   - Base comes from `.{base_mod}` (init_memory_wrapper/export_memory_wrapper + any helpers)\n"
        f"   - Relationships come from `.{rel_mod}` (add_* only)\n"
        f"   - Creation functions come from entity modules: {', '.join('.' + m for m in ent_mods)}\n"
        "\n"
        "Return ONLY Python code (no markdown fences).\n"
    )

    # Create OpenAI client
    client = create_openai_client()

    # Pre-compute required function names from generated scripts (validation only).
    required_funcs = _extract_public_function_names_from_scripts(all_script_paths)

    # Call LLM API with retries
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry attempt {attempt}/{max_retries}...")

            # Persist the exact LLM input for later inspection/debugging.
            # We write per-attempt, because retries append error guidance to the prompt.
            # NOTE: use module-safe names (no dots) so users can run/debug with `python -m`.
            prompt_path = Path(output_dir) / f"main_prompt_attempt_{attempt}.md"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(
                "\n".join(
                    [
                        f"# main.py LLM prompt (attempt {attempt})",
                        "",
                        f"- Ontology: `{ontology_name}`",
                        f"- Model: `{model_name}`",
                        "",
                        "## Full prompt",
                        "",
                        "```",
                        prompt,
                        "```",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            if attempt == 1:
                # Stable alias to quickly find the most recent prompt.
                (Path(output_dir) / "main_prompt_latest.md").write_text(prompt_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"   🧾 Wrote LLM prompt: {prompt_path.name}")

            print(f"   ⏳ Calling {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert in FastMCP server development. Generate complete, production-ready FastMCP wrappers based on extracted function signatures."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000)
            )

            # Extract code from response
            code = extract_code_from_response(response.choices[0].message.content or "")

            if not code:
                raise ValueError("LLM returned empty response")

            # Deterministically fix import ownership (most reliable way).
            # The LLM often imports functions from the wrong underlying module (e.g., entity_2 funcs from entity_1),
            # which causes ImportError at runtime even if wrappers are correct.
            owners = _function_owner_map(all_script_paths)
            code = _rewrite_main_relative_imports(code, owners)
            code = _patch_fastmcp_instruction_compat(code)
            # Fix and validate wrapper forwarding to avoid NameError-inducing typos.
            code = _rewrite_main_wrapper_forwarding_param_typos(code)
            code = _normalize_python_name_literals(code)

            # Always write each attempt to disk BEFORE validation so it can be inspected.
            attempt_path = Path(output_dir) / f"main_attempt_{attempt}.py"
            attempt_path.parent.mkdir(parents=True, exist_ok=True)
            attempt_path.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")
            print(f"   📝 Wrote attempt file: {attempt_path.name}")

            # Validate syntax before writing
            is_valid, syntax_error = validate_python_syntax(code, "main.py")
            if not is_valid:
                # Feed the exact compiler error back to the next retry so the LLM
                # can correct indentation/structure (otherwise it may repeat).
                if attempt < max_retries:
                    last_exception = ValueError(f"Syntax: {syntax_error}")
                    extra_hint = ""
                    # Common failure mode: unindented line after an `if ...:` guard.
                    if "expected an indented block" in syntax_error.lower():
                        extra_hint = (
                            "\nCOMMON PITFALL TO FIX:\n"
                            "If you write:\n"
                            "  if hasattr(mcp, \"set_initial_instructions\"):\n"
                            "  mcp.set_initial_instructions(INSTRUCTION_PROMPT)\n"
                            "that is INVALID because the second line must be indented.\n"
                            "Correct form:\n"
                            "  if hasattr(mcp, \"set_initial_instructions\"):\n"
                            "      mcp.set_initial_instructions(INSTRUCTION_PROMPT)\n"
                            "  else:\n"
                            "      @mcp.prompt(name=\"instruction\")\n"
                            "      def instruction_prompt():\n"
                            "          return INSTRUCTION_PROMPT\n"
                        )
                    prompt += (
                        "\n\n⚠️ YOUR LAST OUTPUT DID NOT COMPILE.\n"
                        f"FIX THIS EXACT PYTHON SYNTAX ERROR:\n{syntax_error}\n"
                        f"{extra_hint}\n"
                        "Return the FULL corrected main.py as plain Python code.\n"
                    )
                    continue
                raise ValueError(f"Syntax: {syntax_error}")

            code = _fix_reversed_underscored_alias_assignments(code)

            # Validate the specific alias mismatch failure (no auto-fix; just retry with guidance).
            ok_alias, alias_err = _validate_underscored_alias_calls(code)
            if not ok_alias:
                if attempt < max_retries:
                    last_exception = ValueError(f"Alias mismatch: {alias_err[:120]}")
                    prompt += (
                        "\n\n⚠️ YOUR LAST OUTPUT HAS AN ALIAS DELEGATION BUG.\n"
                        "If you import `foo as _foo`, ALL calls must use `_foo(...)`.\n"
                        "Wrapper functions must never call themselves.\n"
                        "Example:\n"
                        "BAD:\n"
                        "  from .x import create_Add as _create_Add\n"
                        "  def create_Add(...):\n"
                        "      return create_Add(...)\n"
                        "GOOD:\n"
                        "  from .x import create_Add as _create_Add\n"
                        "  def create_Add(...):\n"
                        "      return _create_Add(...)\n"
                        "\n"
                        f"Detected issues:\n{alias_err}\n"
                        "Return the FULL corrected main.py as plain Python code.\n"
                    )
                    continue
                raise ValueError(f"Alias mismatch: {alias_err}")

            ok_fw, fw_err = _validate_main_wrapper_forwarding_uses_defined_params(code, "main.py")
            if not ok_fw:
                if attempt < max_retries:
                    last_exception = ValueError(f"Forwarding NameError risk: {fw_err[:140]}")
                    prompt += (
                        "\n\n⚠️ YOUR LAST OUTPUT HAS A WRAPPER FORWARDING BUG.\n"
                        "In each wrapper, when you call the imported underscored function, the keyword values must refer to parameters.\n"
                        "Do NOT invent or misspell parameter names in forwarded values.\n"
                        f"Detected issues:\n{fw_err}\n"
                        "Return the FULL corrected main.py as plain Python code.\n"
                    )
                    continue
                raise ValueError(f"Forwarding bug: {fw_err}")

            # Validate coverage: ensure EVERY required function has an @mcp.tool wrapper.
            wrapped = _extract_mcp_tool_wrappers_from_main(code)
            missing = [fn for fn in required_funcs if fn not in wrapped]
            if missing:
                # Make retries non-identical: explicitly list missing wrappers.
                if attempt < max_retries:
                    last_exception = ValueError(f"Missing wrappers: {len(missing)}")
                    missing_preview = "\n".join(f"- {m}" for m in missing[:80])
                    prompt += (
                        "\n\n⚠️ YOUR LAST OUTPUT IS INCOMPLETE.\n"
                        "You MUST add @mcp.tool wrappers for EVERY function listed in 'Functions Extracted from Underlying Script'.\n"
                        f"Missing wrappers ({len(missing)}):\n{missing_preview}\n"
                        "Return the FULL corrected main.py as plain Python code.\n"
                    )
                    continue
                raise ValueError(f"Missing wrappers: {len(missing)} (e.g., {missing[:10]})")

            # Write to file
            output_path = Path(output_dir) / "main.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)

            print(f"   ✅ Generated: {output_path}")
            print(f"   📊 Size: {len(code)} characters")

            return str(output_path)

        except Exception as e:
            last_exception = e
            print(f"   ⚠️  Attempt {attempt} failed: {e}")

            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff

    # All retries failed
    raise Exception(f"Failed to generate script after {max_retries} attempts: {last_exception}")





async def generate_checks_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3
) -> str:
    """Generate deterministic check_existing_* functions from the parsed T-Box."""
    print("   Generating check_existing functions...")

    def _load_class_names() -> List[str]:
        parsed_json = Path(ontology_path).with_name(f"{Path(ontology_path).stem}_parsed.json")
        classes: List[str] = []
        try:
            if parsed_json.exists():
                data = json.loads(parsed_json.read_text(encoding="utf-8"))
                classes_obj = data.get("classes", {}) if isinstance(data, dict) else {}
                if isinstance(classes_obj, dict):
                    classes = [str(name).strip() for name in classes_obj.keys() if str(name).strip()]
        except Exception:
            classes = []

        if classes:
            return sorted(set(classes))

        g = Graph()
        g.parse(ontology_path, format="turtle")
        namespace_uri = _derive_primary_namespace_uri(ontology_name, g)
        if not namespace_uri:
            raise ValueError(f"Could not determine namespace for ontology '{ontology_name}'")
        ns = str(namespace_uri)
        discovered: List[str] = []
        for cls in g.subjects(RDF.type, OWL.Class):
            if isinstance(cls, URIRef) and str(cls).startswith(ns):
                discovered.append(str(cls).rsplit("/", 1)[-1].rsplit("#", 1)[-1])
        return sorted(set(discovered))

    def _camel_to_snake(name: str) -> str:
        s = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name or "").strip())
        s = re.sub(r"[^A-Za-z0-9_]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s.lower()

    def _pluralize(word: str) -> str:
        if word.endswith("sis"):
            return word[:-3] + "ses"
        if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
            return word[:-1] + "ies"
        if word.endswith(("s", "x", "z", "ch", "sh")):
            return word + "es"
        return word + "s"

    def _tool_name_for_class(class_name: str) -> str:
        snake = _camel_to_snake(class_name)
        parts = snake.split("_")
        parts[-1] = _pluralize(parts[-1])
        return "check_existing_" + "_".join(parts)

    class_names = _load_class_names()
    if not class_names:
        raise ValueError(f"No ontology classes found for '{ontology_name}'")

    lines: List[str] = [
        "from rdflib import Graph, Literal, Namespace, URIRef, RDF, RDFS",
        "from ..universal_utils import locked_graph, _list_instances_with_label",
        f"from .{ontology_name}_creation_base import _guard_check, NAMESPACE",
        "",
        "",
        "def _local_name(uri: URIRef) -> str:",
        "    s = str(uri)",
        "    if '#' in s:",
        "        return s.split('#')[-1]",
        "    return s.rsplit('/', 1)[-1]",
        "",
        "",
        "def _get_label(g: Graph, node: URIRef) -> str:",
        "    for p in (RDFS.label, URIRef('http://www.w3.org/2004/02/skos/core#prefLabel')):",
        "        for o in g.objects(node, p):",
        "            try:",
        "                return str(o)",
        "            except Exception:",
        "                continue",
        "    return _local_name(node)",
        "",
        "",
        "def _safe_int(v):",
        "    try:",
        "        return int(str(v).strip())",
        "    except Exception:",
        "        return None",
        "",
        "",
        "def _is_ontology_uri(uri: URIRef) -> bool:",
        "    try:",
        "        return str(uri).startswith(str(NAMESPACE))",
        "    except Exception:",
        "        return False",
        "",
        "",
        "def _iter_items(items_text: str):",
        "    for raw in str(items_text or '').splitlines():",
        "        row = raw.strip()",
        "        if not row:",
        "            continue",
        "        parts = row.split('\\t', 1)",
        "        iri = parts[0].strip()",
        "        label = parts[1].strip() if len(parts) > 1 else ''",
        "        yield iri, label",
        "",
        "",
        "def _find_object_properties(g: Graph):",
        "    owl_ObjectProperty = URIRef('http://www.w3.org/2002/07/owl#ObjectProperty')",
        "    seen = set()",
        "    for p in g.subjects(RDF.type, owl_ObjectProperty):",
        "        if isinstance(p, URIRef) and _is_ontology_uri(p) and p not in seen:",
        "            seen.add(p)",
        "            yield p",
        "    for s, p, o in g:",
        "        if not isinstance(s, URIRef) or not isinstance(p, URIRef) or not isinstance(o, URIRef):",
        "            continue",
        "        if p in seen or not _is_ontology_uri(p):",
        "            continue",
        "        if not _is_ontology_uri(s) or not _is_ontology_uri(o):",
        "            continue",
        "        seen.add(p)",
        "        yield p",
        "",
        "",
        "def _find_datatype_properties(g: Graph):",
        "    owl_DatatypeProperty = URIRef('http://www.w3.org/2002/07/owl#DatatypeProperty')",
        "    seen = set()",
        "    for p in g.subjects(RDF.type, owl_DatatypeProperty):",
        "        if isinstance(p, URIRef) and _is_ontology_uri(p) and p not in seen:",
        "            seen.add(p)",
        "            yield p",
        "    for s, p, o in g:",
        "        if not isinstance(s, URIRef) or not isinstance(p, URIRef) or not isinstance(o, Literal):",
        "            continue",
        "        if p in seen or not _is_ontology_uri(p):",
        "            continue",
        "        if not _is_ontology_uri(s):",
        "            continue",
        "        seen.add(p)",
        "        yield p",
        "",
        "",
        "def _find_order_like_datatype_properties(g: Graph):",
        "    candidates = []",
        "    for dp in _find_datatype_properties(g):",
        "        ln = _local_name(dp).lower()",
        "        if any(k in ln for k in ('order', 'index', 'sequence', 'position', 'stepnumber', 'step_no', 'stepno', 'rank')):",
        "            candidates.append(dp)",
        "            continue",
        "        for c in g.objects(dp, RDFS.comment):",
        "            cl = str(c).lower()",
        "            if any(k in cl for k in ('order', 'index', 'sequence', 'position', 'step number', 'step-number', 'rank')):",
        "                candidates.append(dp)",
        "                break",
        "    return candidates",
        "",
        "",
        "def _find_container_member_object_properties(g: Graph):",
        "    candidates = []",
        "    for op in _find_object_properties(g):",
        "        ln = _local_name(op).lower()",
        "        if any(k in ln for k in ('hasstep', 'has_step', 'step', 'hasmember', 'has_member', 'member', 'consistsof', 'consists_of', 'contains', 'haspart', 'has_part')):",
        "            candidates.append(op)",
        "            continue",
        "        for c in g.objects(op, RDFS.comment):",
        "            cl = str(c).lower()",
        "            if any(k in cl for k in ('ordered', 'sequence', 'steps', 'members', 'contains', 'consists of', 'has part')):",
        "                candidates.append(op)",
        "                break",
        "    return candidates",
        "",
        "",
        "def _typed_ontology_instances(g: Graph):",
        "    seen = set()",
        "    for s, o in g.subject_objects(RDF.type):",
        "        if not isinstance(s, URIRef) or not isinstance(o, URIRef):",
        "            continue",
        "        if not _is_ontology_uri(s) or not _is_ontology_uri(o):",
        "            continue",
        "        if s in seen:",
        "            continue",
        "        seen.add(s)",
        "        yield s",
        "",
        "",
        "def _ontology_incoming_edges(g: Graph, node: URIRef, object_props):",
        "    hits = []",
        "    for pred in object_props:",
        "        for subj in g.subjects(pred, node):",
        "            if isinstance(subj, URIRef) and _is_ontology_uri(subj) and subj != node:",
        "                hits.append((subj, pred))",
        "    return hits",
        "",
        "",
        "def _ontology_outgoing_edges(g: Graph, node: URIRef, object_props):",
        "    hits = []",
        "    for pred in object_props:",
        "        for obj in g.objects(node, pred):",
        "            if isinstance(obj, URIRef) and _is_ontology_uri(obj) and obj != node:",
        "                hits.append((pred, obj))",
        "    return hits",
        "",
        "",
        "@_guard_check",
        "def check_and_report_order_consistency() -> str:",
        "    with locked_graph() as g:",
        "        container_to_members = {}",
        "        container_member_props = _find_container_member_object_properties(g)",
        "        order_props = _find_order_like_datatype_properties(g)",
        "        if not container_member_props or not order_props:",
        "            return (",
        "                'Order consistency check: no applicable ordering structure found in ontology.\\n'",
        "                f'Discovered container→member object properties: {len(container_member_props)}; '",
        "                f'order-like datatype properties: {len(order_props)}.'",
        "            )",
        "        for op in container_member_props:",
        "            for s, o in g.subject_objects(op):",
        "                if isinstance(s, URIRef) and isinstance(o, URIRef) and _is_ontology_uri(s) and _is_ontology_uri(o):",
        "                    container_to_members.setdefault(s, set()).add(o)",
        "        if not container_to_members:",
        "            return 'Order consistency check: no containers with members found in data.'",
        "        lines = []",
        "        issues = 0",
        "        for container, members in sorted(container_to_members.items(), key=lambda kv: str(kv[0])):",
        "            observed = {}",
        "            missing = []",
        "            for member in sorted(members, key=str):",
        "                order_value = None",
        "                for dp in order_props:",
        "                    for obj in g.objects(member, dp):",
        "                        order_value = _safe_int(obj)",
        "                        if order_value is not None:",
        "                            observed.setdefault(order_value, []).append(member)",
        "                            break",
        "                    if order_value is not None:",
        "                        break",
        "            if not observed:",
        "                continue",
        "            max_order = max(observed)",
        "            for expected in range(1, max_order + 1):",
        "                if expected not in observed:",
        "                    missing.append(expected)",
        "            duplicates = {k: v for k, v in observed.items() if len(v) > 1}",
        "            if duplicates or missing:",
        "                issues += 1",
        "                lines.append(f'Container: {container}')",
        "                if duplicates:",
        "                    for order_value, members_for_order in sorted(duplicates.items()):",
        "                        joined = ', '.join(str(m) for m in members_for_order)",
        "                        lines.append(f'- Duplicate order {order_value}: {joined}')",
        "                if missing:",
        "                    lines.append(f\"- Missing order values: {', '.join(str(x) for x in missing)}\")",
        "        if not lines:",
        "            return 'Order consistency check: no duplicate or missing order values found.'",
        "        return '\\n'.join([f'Order consistency issues found: {issues} container(s).'] + lines)",
        "",
        "",
        "@_guard_check",
        "def check_orphan_entities() -> str:",
        "    with locked_graph() as g:",
        "        object_props = list(_find_object_properties(g))",
        "        if not object_props:",
        "            return 'Orphan entity check: no ontology object properties found.'",
        "        order_props = list(_find_order_like_datatype_properties(g))",
        "        if not order_props:",
        "            return 'Orphan entity check: no order-like member structures found in ontology.'",
        "        orphan_lines = []",
        "        for node in sorted(_typed_ontology_instances(g), key=str):",
        "            incoming = _ontology_incoming_edges(g, node, object_props)",
        "            if incoming:",
        "                continue",
        "            has_order_like_value = False",
        "            for dp in order_props:",
        "                if any(True for _ in g.objects(node, dp)):",
        "                    has_order_like_value = True",
        "                    break",
        "            if not has_order_like_value:",
        "                continue",
        "            node_types = sorted(_local_name(t) for t in g.objects(node, RDF.type) if isinstance(t, URIRef) and _is_ontology_uri(t))",
        "            if not node_types:",
        "                continue",
        "            label = _get_label(g, node)",
        "            orphan_lines.append(f\"- {node} | label={label} | types={', '.join(node_types)}\")",
        "        if not orphan_lines:",
        "            return 'Orphan entity check: no orphan-like ordered members found.'",
        "        return '\\n'.join(['Potential orphan ordered members found:'] + orphan_lines)",
    ]

    for class_name in class_names:
        tool_name = _tool_name_for_class(class_name)
        lines.extend([
            "",
            "",
            "@_guard_check",
            f"def {tool_name}() -> str:",
            "    with locked_graph() as g:",
            f"        cls = NAMESPACE.{class_name}",
            "        items_text = _list_instances_with_label(g, cls)",
            "        if not str(items_text or '').strip():",
            f"            return \"No {class_name} instances found.\"",
            f"        lines = [\"Existing {class_name} instances:\"]",
            "        for iri, label in _iter_items(items_text):",
            "            lines.append(f\"- {iri}\" + (f\" ({label})\" if label else \"\"))",
            "        return \"\\n\".join(lines)",
        ])

    code = "\n".join(lines) + "\n"
    is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_checks.py")
    if not is_valid:
        raise ValueError(f"Generated deterministic checks script has syntax error: {syntax_error}")

    output_path = Path(output_dir) / f"{ontology_name}_creation_checks.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)

    print(f"   ✅ Generated: {output_path.name} - Syntax OK")
    return str(output_path)


async def generate_relationships_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3
) -> str:
    """Generate relationship/add_* functions using an LLM meta-prompt (reference-parity, not template)."""
    print("   Generating relationship functions (LLM, per-property + ergonomic helpers)...")

    if _ontology_object_property_count(ontology_path, ontology_name) == 0:
        output_path = Path(output_dir) / f"{ontology_name}_creation_relationships.py"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        stub = f"""import json
from rdflib import Graph, URIRef, RDF, RDFS
from ..universal_utils import locked_graph
from .{ontology_name}_creation_base import (
    NAMESPACE,
    _format_error,
    _format_success_json,
)


# No object properties in ontology; this module intentionally provides no add_* relationship functions.
# Kept as a valid, importable module for the FastMCP server wrapper.


def list_relationship_functions() -> str:
    return json.dumps({{"functions": []}}, ensure_ascii=False)
"""
        output_path.write_text(stub, encoding="utf-8")
        print(f"   ✅ Generated deterministic relationship stub: {output_path.name}")
        return str(output_path)

    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    concise_content = concise_md_path.read_text(encoding="utf-8")

    meta_prompt_template = load_meta_prompt("direct_relationships_script_prompt.md")

    # Add a strict namespace + locked_graph contract to prevent the known failure modes:
    # - wrong namespace IRIs (e.g., invented URI patterns instead of the ontology-provided namespace)
    # - locked_graph misused as locked_graph(g) (Graph passed into doi parameter) -> runtime failure
    concise_structure = extract_concise_ontology_structure(
        ontology_path,
        include_om2_mock=_ontology_uses_om2_units(ontology_path, ontology_name),
    )
    expected_relationship_props = sorted(
        {
            _local_name_from_iri(str((conn or {}).get("property") or "").strip())
            for structure in (concise_structure.get("class_structures", {}) or {}).values()
            for conn in ((structure or {}).get("connects_to", []) or [])
            if _local_name_from_iri(str((conn or {}).get("property") or "").strip())
        }
    )
    contracts = "\n\n".join(
        [
            _namespace_contract_block(concise_structure, ontology_name),
            "CRITICAL LOCKED_GRAPH CONTRACT (MUST FOLLOW EXACTLY):\n"
            "- Always use: `with locked_graph() as g:` (NO arguments to locked_graph).\n"
            "- NEVER call `locked_graph(g)` or pass a Graph into locked_graph.\n"
            "- Relationship mutations must happen inside the locked_graph context.\n",
            "CRITICAL RELATIONSHIP API CONTRACT (MUST FOLLOW EXACTLY):\n"
            "- Public `add_*` functions MUST NOT accept a `graph` or `g` parameter.\n"
            "- Public `add_*` functions must open `with locked_graph() as g:` internally.\n"
            "- Private helpers (for example `_add_relationship`) MAY accept `graph: Graph`.\n"
            "- `_format_success_json` must be called as `_format_success_json(iri, message, created=...)`.\n"
            "- NEVER call `_format_success_json({...})` with a dict positional argument.\n",
            "CRITICAL OBJECT-PROPERTY COVERAGE CONTRACT (MUST FOLLOW EXACTLY):\n"
            "- For every ontology object property below, generate a public function named exactly `add_<PropertyLocal>`.\n"
            "- Each such function must accept `(subject_iri: str, object_iri: str)` and return `str`.\n"
            "- The function must create the exact ontology predicate triple for that property.\n"
            + "\n".join(f"- add_{prop}" for prop in expected_relationship_props),
        ]
    )

    prompt = _format_meta_prompt(meta_prompt_template, ontology_name=ontology_name) + "\n\n" + contracts + "\n\n" + concise_content

    # Optional: inject blurred relationship example to stabilize structure without domain leakage.
    try:
        ex_dir = Path(__file__).resolve().parent / "mock_examples"
        ex_rel = (ex_dir / "relationships_blurred_example.py").read_text(encoding="utf-8")
        prompt += (
            "\n\nBLURRED REFERENCE EXAMPLE (copy STRUCTURE, not names):\n"
            "```python\n"
            + ex_rel
            + "\n```"
        )
    except Exception:
        pass

    client = create_openai_client()
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}... (Error: {last_error})")

            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "Generate ONLY valid, compilable Python code. No explanations."},
                    {"role": "user", "content": prompt},
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000),
            )

            code = extract_code_from_response(response.choices[0].message.content or "")
            if not code:
                raise ValueError("Empty response")
            code = _normalize_relationships_script_contracts(code)
            code = _patch_relationship_union_domain_contracts(
                code,
                ontology_path=ontology_path,
                ontology_name=ontology_name,
            )

            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_relationships.py")
            if not is_valid:
                last_error = f"Syntax: {syntax_error}"
                print(f"   ❌ Syntax error: {syntax_error}")
                if attempt < max_retries:
                    prompt += f"\n\n⚠️ FIX THIS SYNTAX ERROR:\n{syntax_error}"
                    continue
                raise ValueError(f"Syntax errors after {max_retries} attempts: {syntax_error}")

            # Hard semantic guardrail: locked_graph() must be called with no args.
            ok_lock, lock_err = _locked_graph_usage_is_valid(code)
            if not ok_lock:
                last_error = f"locked_graph misuse: {lock_err}"
                print(f"   ❌ Semantic check failed: {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX REQUIRED: You misused locked_graph. "
                        "Use `with locked_graph() as g:` ONLY (no arguments), and do all graph mutations inside that context. "
                        "Return the FULL corrected Python file."
                    )
                    continue
                raise ValueError(last_error)

            # Contract guardrail: ensure formatting helpers are called with correct signatures.
            ok_fmt, fmt_err = _format_helpers_usage_is_valid(code)
            if not ok_fmt:
                last_error = f"format helper misuse: {fmt_err}"
                print(f"   ❌ Semantic check failed: {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX REQUIRED: Relationship helpers used invalid JSON formatter signatures.\n"
                        + fmt_err
                        + "\nUse `_format_success_json(iri, message, created=...)` and `_format_error(message, code=...)`."
                        + "\nReturn the FULL corrected Python file."
                    )
                    continue
                raise ValueError(last_error)

            ok_rel_api, rel_api_err = _validate_relationship_public_api_is_valid(code)
            if not ok_rel_api:
                last_error = f"relationship API misuse: {rel_api_err}"
                print(f"   ❌ Semantic check failed: {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX REQUIRED: Your public add_* functions exposed an invalid API.\n"
                        "- Public add_* functions must NOT accept `graph`.\n"
                        "- Public add_* functions must return `str` and manage `locked_graph()` internally.\n"
                        "Return the FULL corrected Python file."
                    )
                    continue
                raise ValueError(last_error)

            missing_rel_fns = [
                prop for prop in expected_relationship_props
                if f"def add_{prop}" not in code
            ]
            if missing_rel_fns:
                last_error = "missing ontology object-property add_* functions: " + ", ".join(
                    f"add_{prop}" for prop in missing_rel_fns[:20]
                )
                print(f"   ❌ Semantic check failed: {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX REQUIRED: The relationships module omitted required public add_* functions.\n"
                        "Generate one public function named exactly `add_<PropertyLocal>(subject_iri: str, object_iri: str) -> str` "
                        "for every ontology object property listed in the object-property coverage contract.\n"
                        f"Missing now: {', '.join('add_' + p for p in missing_rel_fns)}\n"
                        "Return the FULL corrected Python file."
                    )
                    continue
                raise ValueError(last_error)

            base_script_path = str(Path(output_dir) / f"{ontology_name}_creation_base.py")
            ok_base_imports, base_import_err = _base_imports_are_valid(code, base_script_path)
            if not ok_base_imports:
                last_error = f"base import mismatch: {base_import_err}"
                print(f"   ❌ Semantic check failed: {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX REQUIRED: You imported names from the base module that do not exist.\n"
                        + base_import_err
                        + "\nReturn the FULL corrected Python file."
                    )
                    continue
                raise ValueError(last_error)

            # Semantic guardrail: If there is any indication of ordered membership, require mutation-time enforcement logic
            # in the relationships module (not only a separate report/check tool).
            #
            # We intentionally avoid relying on one specific predicate name and instead trigger when:
            # - the ontology text suggests an order-like property, OR
            # - the generated API accepts an order-like input parameter.
            import re
            import ast

            ontology_order_hint = _ontology_has_order_semantics(concise_content)
            # Only trigger the order-enforcement guard when the ontology structure itself
            # expresses order semantics. Parameter-name heuristics create false positives
            # for non-order ontologies and block otherwise runnable generated modules.
            needs_order_enforcement = ontology_order_hint
            if needs_order_enforcement:
                code_l = code.lower()

                # Look for strong signals of mutation-time enforcement.
                # We accept either an explicit helper function name pattern, or textual/structural hints.
                has_helper_name = re.search(r"def\s+_(enforce|validate|check)_[a-z0-9_]*(order|orders)", code, re.IGNORECASE) is not None
                mentions_contiguity = ("contiguous" in code_l) or ("non-contiguous" in code_l) or ("noncontiguous" in code_l)
                mentions_duplicate = ("duplicate" in code_l) or ("dedup" in code_l) or ("already exists" in code_l)
                mentions_expected_range = ("range(1" in code_l) or ("1.." in code_l) or ("expected" in code_l and "order" in code_l)

                if not (has_helper_name or (mentions_duplicate and (mentions_contiguity or mentions_expected_range))):
                    print("   ⚠️  Relationship module has no order-consistency helper; downstream ordered-member validation remains authoritative.")

            output_path = Path(output_dir) / f"{ontology_name}_creation_relationships.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(code, encoding="utf-8")
            print(f"   ✅ Generated: {output_path.name} - Syntax OK")
            return str(output_path)

        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                # Fallback: divide & merge generation to reduce truncation / syntax errors.
                print("   ⚠️  Falling back to divide-and-merge relationship generation...")

                tbox = parse_ttl_tbox(ontology_path)
                all_obj_props: list[dict] = tbox.get("object_properties") or []
                if not all_obj_props:
                    raise

                # Heuristic chunk size: smaller chunks reduce the chance of truncated output.
                chunk_size = 25
                prop_chunks = _split_list(all_obj_props, chunk_size)

                part_codes: list[str] = []
                for idx, chunk in enumerate(prop_chunks, start=1):
                    part_prompt = _format_relationships_prompt_subset(
                        meta_prompt_template=meta_prompt_template,
                        ontology_name=ontology_name,
                        namespace_uri=tbox.get("namespace_uri") or "",
                        object_props_subset=chunk,
                    )

                    part_last_err: str | None = None
                    for part_attempt in range(1, 3 + 1):
                        if part_attempt > 1:
                            part_prompt += f"\n\n⚠️ FIX THIS SYNTAX ERROR:\n{part_last_err}"
                        resp = client.chat.completions.create(
                            model=model_name,
                            messages=[
                                {"role": "system", "content": "Generate ONLY valid, compilable Python code. No explanations. No markdown fences."},
                                {"role": "user", "content": part_prompt},
                            ],
                            temperature=_get_temperature_for_model(model_name),
                            **_token_limit_kwargs(model_name, 12000),
                        )
                        part_code = extract_code_from_response(resp.choices[0].message.content or "")
                        if not part_code:
                            part_last_err = "Empty response"
                            continue
                        ok, err = validate_python_syntax(part_code, f"{ontology_name}_creation_relationships_part_{idx}.py")
                        if ok:
                            part_codes.append(part_code)
                            break
                        part_last_err = err
                    else:
                        raise ValueError(f"Failed to generate relationships part {idx}: {part_last_err}")

                merged = _normalize_relationships_script_contracts(_merge_relationship_parts(part_codes))
                ok_merged, merged_err = _validate_relationships_script_output(
                    code=merged,
                    ontology_name=ontology_name,
                    output_dir=output_dir,
                    concise_content=concise_content,
                    expected_relationship_props=expected_relationship_props,
                )
                if not ok_merged:
                    raise ValueError(f"Merge produced invalid relationships module: {merged_err}")

                output_path = Path(output_dir) / f"{ontology_name}_creation_relationships.py"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(merged, encoding="utf-8")
                print(f"   ✅ Generated (merged): {output_path.name} - Syntax OK")
                return str(output_path)



async def generate_entity_part_script(
    ontology_path: str,
    ontology_name: str,
    part_number: int,
    classes_to_generate: list,
    output_dir: str,
    base_script_path: str,
    checks_script_path: str,
    relationships_script_path: str,
    model_name: str = "gpt-5.2",
    max_retries: int = 3,
    concise_content_override: str | None = None,
    meta_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate one part of the entity creation scripts with syntax validation."""
    from pathlib import Path

    def _ancestor_closure(
        class_structures: dict,
        cls_name: str,
        *,
        max_hops: int = 20,
    ) -> list[str]:
        """Return transitive parent classes (local names) within the ontology namespace."""
        out: list[str] = []
        seen: set[str] = set()
        frontier: list[str] = [cls_name]
        hops = 0
        while frontier and hops < max_hops:
            cur = frontier.pop()
            parents = (class_structures.get(cur, {}) or {}).get("parent_classes") or []
            for p in parents:
                if not p or p in seen:
                    continue
                seen.add(p)
                out.append(p)
                frontier.append(p)
            hops += 1
        return out

    def _validate_entity_script_runtime_contracts(src: str) -> tuple[bool, str]:
        """
        Enforce non-syntax semantic contracts that routinely break runtime.

        These contracts align with `sandbox/code/universal_utils.py` (copied into
        `ai_generated_contents_candidate/scripts/universal_utils.py`):
        - `_guard_noncheck` is a decorator: NEVER call `_guard_noncheck()`.
        - `_mint_hash_iri` signature is `_mint_hash_iri(class_local: str)` (exactly 1 arg, no keywords).
        - `_export_snapshot_silent` (if used) must be called with NO args.
        - Every create_* function must be decorated with `@_guard_noncheck`.
        - A create_* function must never call another public create_* function because each public
          create_* opens its own locked_graph() scope and nested calls can deadlock on the same entity lock.
        """
        import ast

        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        # 1) Reject calling _guard_noncheck() (decorator misuse).
        for node in ast.walk(mod):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_guard_noncheck":
                return (
                    False,
                    "Do not call `_guard_noncheck()`; it is a decorator. Use `@_guard_noncheck` on create_* functions.",
                )

        # 2) Enforce _mint_hash_iri call arity.
        for node in ast.walk(mod):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "_mint_hash_iri":
                if len(node.args) != 1 or node.keywords:
                    return (
                        False,
                        "`_mint_hash_iri` must be called as `_mint_hash_iri(class_local)` with exactly 1 argument (no keywords).",
                    )

        # 3) Enforce _export_snapshot_silent call arity (if used).
        for node in ast.walk(mod):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "_export_snapshot_silent":
                if node.args or node.keywords:
                    return False, "`_export_snapshot_silent` (if used) must be called with NO arguments."

        # 4) Ensure all create_* functions have @_guard_noncheck decorator.
        for node in mod.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("create_"):
                continue
            has_guard = any(
                isinstance(d, ast.Name) and d.id == "_guard_noncheck"
                for d in (node.decorator_list or [])
            )
            if not has_guard:
                return False, f"Missing @_guard_noncheck decorator on function: {node.name}"

        # 5) Reject the invalid unit-check idiom: `_resolve_om2_unit(x) is None`.
        # `_resolve_om2_unit` is expected to raise ValueError on unknown units, not return None.
        for node in ast.walk(mod):
            if not isinstance(node, ast.Compare):
                continue
            if len(node.ops) != 1 or len(node.comparators) != 1:
                continue
            op = node.ops[0]
            rhs = node.comparators[0]
            if not isinstance(op, (ast.Is, ast.IsNot)):
                continue
            if not (isinstance(rhs, ast.Constant) and rhs.value is None):
                continue
            lhs = node.left
            if isinstance(lhs, ast.Call) and isinstance(lhs.func, ast.Name) and lhs.func.id == "_resolve_om2_unit":
                return (
                    False,
                    "Do not write `_resolve_om2_unit(unit) is None`. `_resolve_om2_unit` should raise on invalid units; "
                    "catch ValueError and return an INVALID_UNIT-style error instead.",
                )

        # 6) Reject nested public create_* calls inside create_* implementations.
        for node in mod.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("create_"):
                continue
            for inner in ast.walk(node):
                if inner is node or not isinstance(inner, ast.Call):
                    continue
                call_name = None
                if isinstance(inner.func, ast.Name):
                    call_name = inner.func.id
                elif isinstance(inner.func, ast.Attribute):
                    call_name = inner.func.attr
                if call_name and call_name.startswith("create_"):
                    return (
                        False,
                        f"{node.name} calls public helper `{call_name}(...)` inside its implementation. "
                        "Do not nest create_* calls inside create_*; reuse `_find_or_create_*` helpers or mutate "
                        "the current graph `g` directly inside the same locked_graph() scope.",
                    )

        return True, "OK"

    def _validate_superclass_typing(
        src: str,
        *,
        class_to_ancestors: dict[str, list[str]],
        known_classes: set[str] | None = None,
    ) -> tuple[bool, str]:
        """
        Ensure instances are typed as both subclass and all ancestor classes.

        Why: relationship validation typically checks for a parent type (superclass).
        We do NOT rely on RDFS reasoning at runtime, so we must emit explicit rdf:type triples.
        """
        import ast
        import re

        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        # Precompute function source segments for create_* functions.
        fn_src: dict[str, str] = {}
        for node in mod.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("create_"):
                seg = ast.get_source_segment(src, node) or ""
                fn_src[node.name] = seg

        known_classes = known_classes or set()

        missing: list[str] = []
        for cls, ancestors in class_to_ancestors.items():
            if not ancestors:
                continue
            fn_name = f"create_{cls}"
            seg = fn_src.get(fn_name)
            if not seg:
                # If the create_* function doesn't exist, coverage checks will catch it elsewhere.
                continue

            # Accept either bracket form or attribute form.
            # Require the parent class to appear in an rdf:type triple.
            for parent in ancestors:
                # Skip if parent == cls (defensive)
                if not parent or parent == cls:
                    continue
                # Some subgraph T-Boxes reference parent classes (via rdfs:subClassOf)
                # without declaring them as owl:Class. In that case, we cannot reliably
                # require explicit ancestor typing during generation. Skip such parents.
                if known_classes and parent not in known_classes:
                    continue

                # Ontology-agnostic typing check: accept either `NAMESPACE[...]` / `NAMESPACE.Parent`
                # (preferred for generated scripts), or any uppercase namespace variable defined in base.
                pat = (
                    r"RDF\.type\s*,\s*[^)\n]*"
                    + r"(?:"
                    + r"[A-Z][A-Z0-9_]*\[\s*[\"']"
                    + re.escape(parent)
                    + r"[\"']\s*\]"
                    + r"|[A-Z][A-Z0-9_]*\."
                    + re.escape(parent)
                    + r")"
                )
                if re.search(pat, seg) is None:
                    missing.append(f"{fn_name}: missing rdf:type for parent {parent}")

        if missing:
            preview = "\n".join("- " + m for m in missing[:40])
            return (
                False,
                "Superclass typing missing. For subclasses, emit rdf:type triples for ALL ancestor classes.\n"
                f"Missing (first {min(len(missing), 40)}):\n{preview}",
            )

        return True, "OK"

    def _validate_ordered_member_contracts(
        src: str,
        *,
        profile: dict[str, Any],
        class_structures: dict[str, Any],
    ) -> tuple[bool, str]:
        """Enforce generic ordered-member integrity contracts derived from ontology annotations."""
        ordered_classes = set(profile.get("ordered_member_classes", []) or [])
        if not ordered_classes:
            return True, "OK"

        non_reusable = set(profile.get("non_reusable_classes", []) or [])
        order_props = set(profile.get("single_valued_ordering_properties", []) or [])

        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        def _snake(name: str) -> str:
            text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "")
            text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
            return text.lower()

        fn_map: dict[str, ast.FunctionDef] = {}
        fn_src: dict[str, str] = {}
        for node in mod.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("create_"):
                fn_map[node.name] = node
                fn_src[node.name] = ast.get_source_segment(src, node) or ""

        violations: list[str] = []
        for cls in sorted(ordered_classes):
            fn_name = f"create_{cls}"
            fn_node = fn_map.get(fn_name)
            seg = fn_src.get(fn_name, "")
            if not fn_node or not seg:
                continue

            if cls in non_reusable:
                same_class_patterns = [
                    rf"\bcheck_existing_{re.escape(cls)}\b",
                    rf"\bcheck_existing_{re.escape(_snake(cls))}\b",
                    rf"\b_find_or_create_{re.escape(cls)}\b",
                    rf"\b_find_or_create_{re.escape(_snake(cls))}\b",
                ]
                if any(re.search(pattern, seg, flags=re.IGNORECASE) for pattern in same_class_patterns):
                    violations.append(f"{fn_name}: ordered/non-reusable members must not deduplicate or reuse same-class instances")

            class_order_props = [
                prop
                for prop in (class_structures.get(cls, {}) or {}).get("datatype_inputs", []) or []
                if prop in order_props
            ]
            if class_order_props:
                arg_nodes = list(fn_node.args.args) + list(fn_node.args.kwonlyargs)
                for arg in arg_nodes:
                    if arg.arg not in class_order_props or arg.annotation is None:
                        continue
                    try:
                        ann_text = ast.unparse(arg.annotation)
                    except Exception:
                        ann_text = ""
                    if re.search(r"\b(list|List|tuple|Tuple|set|Set|Sequence|Iterable|dict|Dict)\b", ann_text):
                        violations.append(
                            f"{fn_name}: ordering property `{arg.arg}` must stay scalar for one ordered member, not {ann_text}"
                        )

        if violations:
            preview = "\n".join("- " + item for item in violations[:40])
            return (
                False,
                "Ordered-member integrity contract violated.\n"
                f"Violations (first {min(len(violations), 40)}):\n{preview}",
            )
        return True, "OK"

    def _validate_required_step_scoped_object_contracts(
        src: str,
        *,
        profile: dict[str, Any],
        class_structures: dict[str, Any],
        classes_in_part: list[str],
        contract_specs: list[dict[str, str]] | None = None,
    ) -> tuple[bool, str]:
        """Enforce mandatory step-scoped object-property links derived from ontology text."""
        ordered_classes = set(profile.get("ordered_member_classes", []) or [])
        contract_specs = contract_specs or []
        if not ordered_classes and not contract_specs:
            return True, "OK"

        def _comment_makes_property_required(comment: str, prop_local: str) -> bool:
            text = str(comment or "").lower()
            prop = str(prop_local or "").lower()
            if not text or not prop:
                return False
            markers = ("must link", "must attach", "must have", "required", "exactly one")
            for segment in re.split(r"(?<=[.;])\s+|\n+", text):
                if prop in segment and any(marker in segment for marker in markers):
                    return True
            return False

        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        fn_src = {
            node.name: ast.get_source_segment(src, node) or ""
            for node in mod.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("create_")
        }

        violations: list[str] = []
        required_specs_by_class: dict[str, list[tuple[str, str]]] = {}
        for spec in contract_specs:
            domain = str((spec or {}).get("domain_local") or "").strip()
            prop = str((spec or {}).get("predicate_local") or "").strip()
            target = str((spec or {}).get("range_local") or "").strip()
            if domain and prop:
                required_specs_by_class.setdefault(domain, []).append((prop, target))

        for cls in classes_in_part:
            required_prop_targets: list[tuple[str, str]] = list(required_specs_by_class.get(cls, []))
            structure = class_structures.get(cls, {}) or {}
            if cls in ordered_classes:
                comments = "\n".join(
                    str(x or "")
                    for x in (
                        structure.get("comment"),
                        structure.get("comments"),
                        structure.get("description"),
                    )
                ).lower()
                for conn in structure.get("connects_to", []) or []:
                    prop = str((conn or {}).get("property") or "").strip()
                    targets = [
                        str(x).strip()
                        for x in ((conn or {}).get("target_classes") or [])
                        if str(x).strip()
                    ]
                    if (
                        comments
                        and prop
                        and prop.lower() in comments
                        and _comment_makes_property_required(comments, prop)
                    ):
                        for target in targets or [""]:
                            required_prop_targets.append((prop, target))

            seen_required: set[tuple[str, str]] = set()
            for prop, target in required_prop_targets:
                if (prop, target) in seen_required:
                    continue
                seen_required.add((prop, target))
                targets = [target] if target else []
                seg = fn_src.get(f"create_{cls}", "")
                if not seg:
                    violations.append(f"create_{cls}: missing constructor for required step-scoped `{prop}`")
                elif prop not in seg:
                    target_text = ", ".join(targets) if targets else "target"
                    violations.append(f"create_{cls}: must materialize required step-scoped `{prop}` to {target_text}")
                else:
                    for target in targets:
                        if target in {"Temperature", "Pressure", "Duration", "Volume", "TemperatureRate", "AmountOfSubstanceFraction"}:
                            continue
                        reuses_target = re.search(
                            rf"_find_by_type_and_label\([^)]*{re.escape(target)}",
                            seg,
                        ) is not None
                        mints_target = re.search(
                            rf"_mint_hash_iri\(\s*['\"]{re.escape(target)}['\"]\s*\)",
                            seg,
                        ) is not None
                        types_target = bool(
                            re.search(rf"RDF\.type\s*,\s*[A-Z][A-Z0-9_]*\.{re.escape(target)}", seg)
                            or re.search(rf"RDF\.type\s*,\s*NAMESPACE\[['\"]{re.escape(target)}['\"]\]", seg)
                        )
                        if not reuses_target:
                            violations.append(
                                f"create_{cls}: required `{prop}` target must be resolved/reused by `{target}` label before minting"
                            )
                        if mints_target and not types_target:
                            violations.append(
                                f"create_{cls}: fallback minted `{prop}` target must be typed as `{target}`"
                            )

        if violations:
            preview = "\n".join("- " + item for item in violations[:40])
            return (
                False,
                "Required step-scoped object-property contract violated.\n"
                f"Violations (first {min(len(violations), 40)}):\n{preview}",
            )
        return True, "OK"

    def _validate_step_scoped_constructor_predicates(
        src: str,
        *,
        profile: dict[str, Any],
        class_structures: dict[str, Any],
        classes_in_part: list[str],
        contract_specs: list[dict[str, str]] | None = None,
    ) -> tuple[bool, str]:
        """Ensure exposed step-scoped object label parameters use the matching ontology predicate."""
        ordered_classes = set(profile.get("ordered_member_classes", []) or [])
        contract_specs = contract_specs or []
        if not ordered_classes and not contract_specs:
            return True, "OK"

        def _lower_initial(name: str) -> str:
            return name[:1].lower() + name[1:] if name else ""

        def _snake(name: str) -> str:
            return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name or "")).lower()

        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        fn_src = {
            node.name: ast.get_source_segment(src, node) or ""
            for node in mod.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("create_")
        }

        violations: list[str] = []
        specs_by_class: dict[str, list[tuple[str, str]]] = {}
        for spec in contract_specs:
            domain = str((spec or {}).get("domain_local") or "").strip()
            prop = str((spec or {}).get("predicate_local") or "").strip()
            target = str((spec or {}).get("range_local") or "").strip()
            if domain and prop and target:
                specs_by_class.setdefault(domain, []).append((prop, target))

        for cls in classes_in_part:
            seg = fn_src.get(f"create_{cls}", "")
            if not seg:
                continue
            prop_targets: list[tuple[str, str]] = list(specs_by_class.get(cls, []))
            if cls in ordered_classes:
                structure = class_structures.get(cls, {}) or {}
                for conn in structure.get("connects_to", []) or []:
                    prop = str((conn or {}).get("property") or "").strip()
                    targets = [
                        str(x).strip()
                        for x in ((conn or {}).get("target_classes") or [])
                        if str(x).strip()
                    ]
                    for target in targets:
                        if prop and target:
                            prop_targets.append((prop, target))

            seen_prop_targets: set[tuple[str, str]] = set()
            for prop, target in prop_targets:
                if (prop, target) in seen_prop_targets:
                    continue
                seen_prop_targets.add((prop, target))
                if not prop or not target:
                    continue
                if target in {"Temperature", "Pressure", "Duration", "Volume", "TemperatureRate", "AmountOfSubstanceFraction"}:
                    continue
                label_markers = {
                    f"{_lower_initial(target)}_label",
                    f"{_snake(target)}_label",
                    f"{_lower_initial(prop)}_label",
                    f"{_snake(prop)}_label",
                }
                if any(marker in seg for marker in label_markers) and prop not in seg:
                    violations.append(
                        f"create_{cls}: label parameter for `{target}` must add step-scoped predicate `{prop}`"
                    )

        if violations:
            preview = "\n".join("- " + item for item in violations[:40])
            return (
                False,
                "Step-scoped constructor predicate contract violated.\n"
                f"Violations (first {min(len(violations), 40)}):\n{preview}",
            )
        return True, "OK"

    def _validate_om2_quantity_links(
        src: str,
        *,
        class_structures: dict[str, Any],
        classes_in_part: list[str],
    ) -> tuple[bool, str]:
        """Ensure created OM-2 quantities are linked from the entity via their ontology property."""
        quantity_targets = {"Temperature", "Pressure", "Duration", "Volume", "TemperatureRate", "AmountOfSubstanceFraction"}
        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        fn_src = {
            node.name: ast.get_source_segment(src, node) or ""
            for node in mod.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("create_")
        }

        violations: list[str] = []
        for cls in classes_in_part:
            seg = fn_src.get(f"create_{cls}", "")
            if not seg or "_find_or_create_om2_quantity" not in seg:
                continue
            structure = class_structures.get(cls, {}) or {}
            for conn in structure.get("connects_to", []) or []:
                prop = str((conn or {}).get("property") or "").strip()
                targets = {
                    str(x).strip()
                    for x in ((conn or {}).get("target_classes") or [])
                    if str(x).strip()
                }
                if not prop or not targets.intersection(quantity_targets):
                    continue
                prop_mentions = [
                    prop,
                    re.sub(r"(?<!^)(?=[A-Z])", "_", prop).lower(),
                    prop[:1].lower() + prop[1:] if prop else "",
                ]
                if not any(marker in seg for marker in prop_mentions):
                    continue
                link_pattern = rf"g\.add\(\s*\(\s*iri\s*,\s*[A-Z][A-Z0-9_]*\.{re.escape(prop)}\s*,"
                if re.search(link_pattern, seg) is None:
                    violations.append(
                        f"create_{cls}: OM-2 quantity for `{prop}` must be linked with `g.add((iri, <NS>.{prop}, quantity_iri))`"
                    )

        if violations:
            preview = "\n".join("- " + item for item in violations[:40])
            return (
                False,
                "OM-2 quantity link contract violated.\n"
                f"Violations (first {min(len(violations), 40)}):\n{preview}",
            )
        return True, "OK"

    def _validate_entity_private_helper_calls(src: str) -> tuple[bool, str]:
        """Reject generated calls to private helpers that are neither imported nor defined."""
        try:
            mod = ast.parse(src)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} (line {e.lineno})"

        available: set[str] = set()
        for node in mod.body:
            if isinstance(node, ast.FunctionDef):
                available.add(node.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    available.add(alias.asname or alias.name)

        missing: set[str] = set()
        for node in ast.walk(mod):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name.startswith("_find_or_create_") and name not in available:
                missing.add(name)

        if missing:
            return False, "undefined private helper call(s): " + ", ".join(sorted(missing))
        return True, "OK"

    # Load concise ontology for signatures (tests may pass inline markdown to avoid repo fixtures)
    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    if concise_content_override is not None:
        concise_content = concise_content_override
    else:
        with open(concise_md_path, "r", encoding="utf-8") as f:
            concise_content = f.read()

    # Build ontology-derived superclass requirements for this part.
    uses_om2 = _ontology_uses_om2_units(ontology_path, ontology_name)
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=uses_om2)
    class_structures = concise_structure.get("class_structures", {}) or {}
    ontology_symbol_locals: set[str] = {str(x).strip().split("/")[-1] for x in (concise_structure.get("classes") or []) if str(x).strip()}
    for cls_name, structure in class_structures.items():
        if str(cls_name).strip():
            ontology_symbol_locals.add(str(cls_name).strip())
        for prop_name in (structure.get("datatype_inputs") or []):
            if str(prop_name).strip():
                ontology_symbol_locals.add(str(prop_name).strip())
        for conn in (structure.get("connects_to") or []) + (structure.get("connected_from") or []):
            prop_name = str((conn or {}).get("property") or "").strip()
            if prop_name:
                ontology_symbol_locals.add(prop_name)

    class_names = [cls.split('/')[-1] for cls in classes_to_generate]
    classes_list = "\n".join([f"- {name}" for name in class_names])
    try:
        contract_bundle_for_entities = build_generation_contract_bundle(ontology_name=ontology_name)
        contract_step_scoped_specs = [
            {
                "domain_local": str((spec or {}).get("domain_local") or "").strip(),
                "predicate_local": str((spec or {}).get("predicate_local") or "").strip(),
                "range_local": str((spec or {}).get("range_local") or "").strip(),
            }
            for spec in (contract_bundle_for_entities.get("step_scoped_object_properties") or [])
            if str((spec or {}).get("domain_local") or "").strip() in class_names
        ]
        contract_required_step_scoped_specs = [
            {
                "domain_local": str((spec or {}).get("domain_local") or "").strip(),
                "predicate_local": str((spec or {}).get("predicate_local") or "").strip(),
                "range_local": str((spec or {}).get("range_local") or "").strip(),
            }
            for spec in (contract_bundle_for_entities.get("required_step_scoped_object_properties") or [])
            if str((spec or {}).get("domain_local") or "").strip() in class_names
        ]
    except Exception:
        contract_step_scoped_specs = []
        contract_required_step_scoped_specs = []

    class_to_ancestors: dict[str, list[str]] = {}
    for cn in class_names:
        if cn in class_structures:
            class_to_ancestors[cn] = _ancestor_closure(class_structures, cn)
        else:
            class_to_ancestors[cn] = []

    # Render an explicit inheritance checklist for the LLM (derived solely from ontology input).
    inheritance_lines: list[str] = []
    for cn in class_names:
        parents = class_structures.get(cn, {}).get("parent_classes") or []
        ancestors = class_to_ancestors.get(cn) or []
        if not parents and not ancestors:
            continue
        # Keep it readable: show direct parents + closure.
        inheritance_lines.append(f"- {cn}: direct parents = {parents or []}; all ancestors = {ancestors or []}")
    inheritance_block = "\n".join(inheritance_lines) if inheritance_lines else "(no subclass relationships detected for this part)"

    step_scoped_lines: list[str] = []
    try:
        ordered_member_locals = {
            str(x).strip()
            for x in (
                (concise_structure.get("integrity_profile") or {}).get("ordered_member_classes")
                or []
            )
            if str(x).strip()
        }
    except Exception:
        ordered_member_locals = set()
    for cn in class_names:
        if cn not in ordered_member_locals:
            continue
        for conn in (class_structures.get(cn, {}) or {}).get("connects_to", []) or []:
            prop = str((conn or {}).get("property") or "").strip()
            targets = [
                str(x).strip()
                for x in ((conn or {}).get("target_classes") or [])
                if str(x).strip()
            ]
            non_quantity_targets = [
                target for target in targets
                if target not in {"Temperature", "Pressure", "Duration", "Volume", "TemperatureRate", "AmountOfSubstanceFraction"}
            ]
            if prop and non_quantity_targets:
                step_scoped_lines.append(
                    f"- {cn}.{prop}: constructor must support linking to {', '.join(non_quantity_targets)} "
                    f"during creation (e.g. label/IRI parameter) or generated relationship tooling must make this link unavoidable in KG workflow."
                )
    for spec in contract_step_scoped_specs:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        target = str((spec or {}).get("range_local") or "").strip()
        if domain and prop and target and target not in {"Temperature", "Pressure", "Duration", "Volume", "TemperatureRate", "AmountOfSubstanceFraction"}:
            step_scoped_lines.append(
                f"- {domain}.{prop}: if create_{domain} accepts a {target} label/IRI parameter, it MUST add the exact predicate "
                f"`{prop}` to that {target}; do not substitute a generic parent-level relation."
            )
    for spec in contract_required_step_scoped_specs:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        target = str((spec or {}).get("range_local") or "").strip()
        if domain and prop and target:
            step_scoped_lines.append(
                f"- REQUIRED {domain}.{prop}: create_{domain} must resolve/reuse {target} by label first; "
                f"if it mints a fallback {target}, it MUST immediately add `rdf:type {target}` before adding `{prop}`."
            )
    quantity_targets = {"Temperature", "Pressure", "Duration", "Volume", "TemperatureRate", "AmountOfSubstanceFraction"}
    for cn in class_names:
        for conn in (class_structures.get(cn, {}) or {}).get("connects_to", []) or []:
            prop = str((conn or {}).get("property") or "").strip()
            targets = [
                str(x).strip()
                for x in ((conn or {}).get("target_classes") or [])
                if str(x).strip() in quantity_targets
            ]
            if prop and targets:
                step_scoped_lines.append(
                    f"- {cn}.{prop}: if create_{cn} creates/reuses an OM-2 {', '.join(targets)} with "
                    f"`_find_or_create_om2_quantity`, store the returned IRI and add "
                    f"`g.add((iri, <namespace>.{prop}, quantity_iri))`; creating the quantity alone is invalid."
                )
    step_scoped_block = "\n".join(sorted(set(step_scoped_lines))) or "(no step-scoped object-property constructor contracts for this part)"

    # Build strong prompt with explicit requirements.
    # Build a config-driven namespace import list for the entities script prompt (no hardcoded namespace var names).
    try:
        _ns_map = _render_namespaces_from_config(concise_structure)
        _extra_ns = [k for k in _ns_map.keys() if k != "NAMESPACE"]
        _extra_ns_sorted = sorted({k for k in _extra_ns if isinstance(k, str) and k.isidentifier()})
        _ns_import_line = (", " + ", ".join(_extra_ns_sorted)) if _extra_ns_sorted else ""
    except Exception:
        _ns_import_line = ""

    om2_requirements = ""
    om2_imports = ""
    om2_mandatory = ""
    if uses_om2:
        om2_requirements = f"""4. If the ontology mentions external OM-2 quantity concepts (e.g., Temperature), you MUST include the relevant creation logic.
   - Do NOT hardcode any unit tables not present in the provided ontology-derived unit inventory.
5. OM-2 strictness: DO NOT define per-file unit tables (e.g., `_TEMPERATURE_UNITS`, `_PRESSURE_UNIT_MAP`, etc.).
   Always use the shared `OM2_UNIT_MAP` + `_find_or_create_om2_quantity` imported from `{ontology_name}_creation_base`.

6. OM-2 call-style contract (IMPORTANT):
   - `_find_or_create_om2_quantity` MUST be called with keyword arguments:
     `_find_or_create_om2_quantity(g, quantity_class=..., label=..., value=..., unit_label=...)`
   - Do NOT pass unit IRIs; `unit_label` must be the unit label string (e.g., "degree celsius").
   - Do NOT call `_find_or_create_om2_quantity` with positional args beyond the first graph `g`.
   - The returned quantity IRI MUST be linked from the created entity using the ontology object property
     whose range is that OM-2 quantity class; creating an unlinked quantity is invalid.
   - `_resolve_om2_unit` MUST be called as `_resolve_om2_unit(unit_label)` (single argument).
     Do NOT pass the graph as a first argument (i.e., NEVER `_resolve_om2_unit(g, unit_label)`)."""
        om2_imports = """
    # REQUIRED for OM-2: use shared unit inventory + reuse helper from base (do NOT define per-file unit maps)
    OM2_UNIT_MAP, _resolve_om2_unit, _find_or_create_om2_quantity"""
        om2_mandatory = """

MANDATORY: OM-2 quantities
- If the ontology-derived input mentions OM-2 quantities (Temperature/Pressure/Duration/Volume/TemperatureRate/AmountOfSubstanceFraction),
  you MUST also implement create functions for them:
  - create_temperature(label: str, value: float, unit: str) -> str
  - create_pressure(...)
  - create_duration(...)
  - create_volume(...)
  - create_temperature_rate(...)
  - create_amount_of_substance_fraction(...)
  Unit validation MUST be done via ontology-derived unit labels (from the OM-2 unit inventory section)."""
    else:
        om2_requirements = """4. Do NOT generate OM-2 quantity helper tools or quantity-specific `create_*` functions unless they are explicitly part of this ontology.
5. Do NOT import `OM2_UNIT_MAP`, `_resolve_om2_unit`, or `_find_or_create_om2_quantity` for this ontology.

6. No-OM2 contract:
   - Do NOT generate `create_temperature`, `create_pressure`, `create_duration`, `create_volume`,
     `create_temperature_rate`, or `create_amount_of_substance_fraction` unless they are actual ontology classes."""

    top_entity_contract = _resolve_top_entity_codegen_contract(
        ontology_name=ontology_name,
        meta_cfg=meta_cfg,
    )
    top_entity_prompt_block = _build_top_entity_codegen_prompt_block(
        top_entity_contract=top_entity_contract,
        class_names=class_names,
    )
    top_entity_imports = ", get_top_entity_iri" if top_entity_prompt_block else ""

    prompt = f"""Generate {ontology_name}_creation_entities_{part_number}.py

CRITICAL REQUIREMENTS:
1. The code MUST compile without syntax errors.
2. Import ONLY existing names from base script (do NOT invent imports).
3. Return JSON STRING (use json.dumps()), NOT dict objects.
{om2_requirements}

7. Guard + universal_utils runtime contracts (IMPORTANT):
   - `_guard_noncheck` is a DECORATOR. NEVER call `_guard_noncheck()` inside functions.
   - Every `create_*` function MUST be decorated with `@_guard_noncheck`.
   - `_mint_hash_iri` MUST be called as `_mint_hash_iri(class_local)` with EXACTLY one argument (no keywords).
     Do NOT pass a namespace or a label to `_mint_hash_iri`.
   - `_export_snapshot_silent` is optional/no-op; if used, call it with NO arguments: `_export_snapshot_silent()`.
     Do NOT call `_export_snapshot_silent(g)`.
   - NEVER call a public `create_*` function from inside another `create_*` function.
   - Public `create_*` functions already manage their own `locked_graph()` scope; nested public create_* calls
     can re-enter the same file lock and deadlock the MCP server.
   - If you need an auxiliary entity while already inside `with locked_graph() as g:`, resolve or mint it in the
     SAME graph context using an imported `_find_or_create_*` helper from the base module when available, or by
     doing the graph mutation directly against `g`.
   - NEVER use patterns like `json.loads(create_X(...))` inside a `create_*` function.
{top_entity_prompt_block}

8. Class hierarchy typing (CRITICAL):
   - If a class has parent classes in the ontology-derived structure, you MUST assert rdf:type for BOTH:
     - the concrete class (subclass)
     - AND each parent/ancestor class (superclasses)
   - Do NOT rely on RDFS reasoning at runtime. Emit explicit rdf:type triples.
   - For this part, the ontology-derived inheritance summary is:
{inheritance_block}

9. Step-scoped object-property construction (CRITICAL, ontology-derived):
   - If an ordered step class has an object property to another ontology class, the generated create function for that step MUST make it practical to attach that object during normal construction.
   - Prefer a safe optional label parameter on the step constructor (for example `<target>_label`) that first resolves/reuses an existing target with `_find_by_type_and_label(g, <RangeClass>, <label>)`, then mints a typed fallback only if no match exists, and emits the object-property triple in the same locked graph context.
   - Do not rely only on parent-level links when the ontology declares a step-level object property.
{step_scoped_block}

CLASSES TO GENERATE (Part {part_number}):
{classes_list}

ONTOLOGY-DERIVED INPUT (includes OM-2 unit inventory and object-property ranges):
```markdown
{concise_content}
```

REQUIRED IMPORTS (use EXACTLY these; you may import additional helpers ONLY if they exist in base):
```python
import json
from typing import Optional
from rdflib import Graph, URIRef, RDF, RDFS, Literal as RDFLiteral
from ..universal_utils import (
    locked_graph, _mint_hash_iri, _sanitize_label,
    _find_by_type_and_label, _set_single_label, _export_snapshot_silent{top_entity_imports}
)
from .{ontology_name}_creation_base import (
    _guard_noncheck, NAMESPACE{_ns_import_line},
    # Additional namespaces are provided via the namespace contract block; import them if defined in base.
    _format_error, _format_success_json{om2_imports}
)
```
{om2_mandatory}

Generate WORKING, COMPILABLE Python code with ALL required functions.
"""

    # Optional: inject blurred reference examples to stabilize structure without leaking domain specifics.
    # These examples are intentionally non-domain-specific and should be used as patterns, not copied verbatim.
    try:
        ex_dir = Path(__file__).resolve().parent / "mock_examples"
        ex_entity = (ex_dir / "entity_creation_blurred_example.py").read_text(encoding="utf-8")
        prompt += (
            "\n\nBLURRED REFERENCE EXAMPLE (copy STRUCTURE, not names):\n"
            "```python\n"
            + ex_entity
            + "\n```"
        )
    except Exception:
        pass

    client = create_openai_client()
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"   🔄 Retry {attempt}/{max_retries}... (Error: {last_error})")

            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert Python developer. Generate ONLY valid, compilable Python code with correct imports. Return ONLY the code, no explanations."},
                    {"role": "user", "content": prompt}
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 16000)
            )

            code = extract_code_from_response(response.choices[0].message.content or "")
            if not code:
                raise ValueError("Empty response from LLM")
            code = _normalize_entity_script_contracts(code, ontology_name)
            code = _inject_missing_ancestor_rdf_types(
                code,
                class_to_ancestors=class_to_ancestors,
                known_classes=set(class_structures.keys()),
            )

            # `1_2` is an OCR-smell in generated names, but some ontologies legitimately
            # contain numeric code locals. Treat it as bad only
            # when it is not part of an ontology-derived local symbol.
            has_allowed_1_2_symbol = any("1_2" in local and local in code for local in ontology_symbol_locals)
            if "chemica1" in code or ("1_2" in code and not has_allowed_1_2_symbol):
                last_error = "Generated entity script contains OCR/LLM-mangled public symbol text."
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX MANGLED IDENTIFIERS:\n"
                        "The previous output contained OCR-like mangled identifiers such as `1_2` or `chemica1`.\n"
                        "Use exact ontology/local names from the provided structure, with normal alphabetic spelling.\n"
                        "Return the FULL corrected Python file."
                    )
                    continue
                raise ValueError(last_error)

            # VALIDATE SYNTAX
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_entities_{part_number}.py")
            if not is_valid:
                last_error = f"Syntax: {syntax_error}"
                print(f"   ❌ Syntax validation failed: {syntax_error}")
                if attempt < max_retries:
                    print(f"   🔄 Retrying with syntax error feedback...")
                    prompt += f"\n\n⚠️ PREVIOUS ATTEMPT HAD SYNTAX ERROR:\n{syntax_error}\n\nFix this and generate valid Python code."
                    continue
                raise ValueError(f"Generated code has syntax errors after {max_retries} attempts: {syntax_error}")

            # Extra semantic guardrails: entity scripts must not duplicate unit tables / OM-2 helpers.
            forbidden_markers = [
                "_TEMPERATURE_UNITS",
                "_PRESSURE_UNITS",
                "_DURATION_UNITS",
                "_VOLUME_UNITS",
                "_TEMPERATURE_RATE_UNITS",
                "_AMOUNT_FRACTION_UNITS",
                "_TEMPERATURE_UNIT_MAP",
                "_PRESSURE_UNIT_MAP",
                "_DURATION_UNIT_MAP",
                "_VOLUME_UNIT_MAP",
                "_TEMPERATURE_RATE_UNIT_MAP",
                "_AMOUNT_OF_SUBSTANCE_FRACTION_UNIT_MAP",
            ]
            if any(m in code for m in forbidden_markers):
                last_error = "Entity script duplicated OM-2 unit tables; must import/use OM2_UNIT_MAP + _find_or_create_om2_quantity from base."
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX: Do NOT define any per-file OM-2 unit dictionaries. "
                        "Import OM2_UNIT_MAP and _find_or_create_om2_quantity from the base module and use them directly."
                    )
                    continue
                raise ValueError(last_error)

            if not uses_om2:
                forbidden_non_om2_markers = [
                    "def create_temperature(",
                    "def create_pressure(",
                    "def create_duration(",
                    "def create_volume(",
                    "def create_temperature_rate(",
                    "def create_amount_of_substance_fraction(",
                    "OM2_UNIT_MAP",
                    "_resolve_om2_unit",
                    "_find_or_create_om2_quantity",
                ]
                if any(m in code for m in forbidden_non_om2_markers):
                    last_error = "Non-OM2 ontology emitted OM-2 quantity helpers/imports."
                    print(f"   ❌ {last_error}")
                    if attempt < max_retries:
                        prompt += (
                            "\n\n⚠️ FIX NON-OM2 ONTOLOGY:\n"
                            "Do NOT generate any OM-2 quantity functions or imports for this ontology.\n"
                            "Remove create_temperature/create_pressure/create_duration/create_volume/"
                            "create_temperature_rate/create_amount_of_substance_fraction and all OM2 helper imports.\n"
                        )
                        continue
                    raise ValueError(last_error)

            # Runtime contract validation: guard decorator usage, _mint_hash_iri / _export_snapshot_silent call styles.
            ok_rt, rt_err = _validate_entity_script_runtime_contracts(code)
            if not ok_rt:
                last_error = f"Runtime contract violation: {rt_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX RUNTIME CONTRACT:\n"
                        + rt_err
                        + "\n\nKey rules:\n"
                        + "- Use `@_guard_noncheck` on every create_*.\n"
                        + "- Never call `_guard_noncheck()`.\n"
                        + "- Call `_mint_hash_iri(class_local)` with exactly 1 argument.\n"
                        + "- If calling `_export_snapshot_silent`, call it with no arguments.\n"
                        + "- Never call one public create_* function from another create_* function.\n"
                        + "- Inside an existing locked_graph() scope, use `_find_or_create_*` helpers or direct graph mutations instead.\n"
                    )
                    continue
                raise ValueError(last_error)

            ok_fmt, fmt_err = _format_helpers_usage_is_valid(code)
            if not ok_fmt:
                last_error = f"Format helper contract violation: {fmt_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX FORMAT HELPER CALLS:\n"
                        + fmt_err
                        + "\n\nRules:\n"
                        + "- `_format_success_json` must be called as `_format_success_json(iri, message, created=...)`.\n"
                        + "- Do not pass a third positional argument; use the keyword-only `created=` argument.\n"
                    )
                    continue
                raise ValueError(last_error)

            ok_helpers, helpers_err = _validate_entity_private_helper_calls(code)
            if not ok_helpers:
                last_error = f"Entity helper contract violation: {helpers_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX PRIVATE HELPER CALLS:\n"
                        + helpers_err
                        + "\n\nRules:\n"
                        + "- Do not call private helpers unless they are imported from existing modules or defined in this file.\n"
                        + "- If a target entity is needed inside `locked_graph()`, either use `_find_by_type_and_label` plus direct graph mutation, or define a local private helper in the same generated file.\n"
                    )
                    continue
                raise ValueError(last_error)

            ok_top, top_err = _validate_top_entity_create_contract(
                code,
                top_entity_contract=top_entity_contract,
            )
            if not ok_top:
                last_error = f"Top-entity create contract violation: {top_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX TOP-ENTITY CONTRACT:\n"
                        + top_err
                        + "\n\nKey rules:\n"
                        + "- `create_<TopClass>` must reuse `get_top_entity_iri()` from global state.\n"
                        + "- Do not mint a second root for the scoped case.\n"
                        + "- If init_memory has not run, return a MEMORY_INIT_REQUIRED-style error instead of minting a new top entity.\n"
                    )
                    continue
                raise ValueError(last_error)

            # Superclass typing validation (derived from ontology): ensure subclass instances also get parent rdf:types.
            ok_types, types_err = _validate_superclass_typing(
                code,
                class_to_ancestors=class_to_ancestors,
                known_classes=set(class_structures.keys()),
            )
            if not ok_types:
                last_error = f"Superclass typing violation: {types_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX SUPERCLASS TYPING:\n"
                        + types_err
                        + "\n\nRule: after minting `iri`, add rdf:type triples for all parent classes (and ancestors) listed in the ontology structure.\n"
                        + "Example pattern:\n"
                        + "  g.add((iri, RDF.type, NAMESPACE['<ConcreteClass>']))\n"
                        + "  g.add((iri, RDF.type, NAMESPACE['<ParentClass>']))\n"
                    )
                    continue
                raise ValueError(last_error)

            ok_ordered, ordered_err = _validate_ordered_member_contracts(
                code,
                profile=concise_structure.get("integrity_profile", {}) or {},
                class_structures=class_structures,
            )
            if not ok_ordered:
                last_error = f"Ordered-member contract violation: {ordered_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX ORDERED-MEMBER INTEGRITY:\n"
                        + ordered_err
                        + "\n\nRules:\n"
                        + "- One ordered member individual per create_* call.\n"
                        + "- Keep order-like properties scalar, not list-like.\n"
                        + "- Do not deduplicate or reuse same-class ordered members marked non-reusable by the ontology.\n"
                    )
                    continue
                raise ValueError(last_error)

            ok_step_obj, step_obj_err = _validate_required_step_scoped_object_contracts(
                code,
                profile=concise_structure.get("integrity_profile", {}) or {},
                class_structures=class_structures,
                classes_in_part=class_names,
                contract_specs=contract_required_step_scoped_specs,
            )
            if not ok_step_obj:
                last_error = f"Required step-scoped object-property violation: {step_obj_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX REQUIRED STEP-SCOPED OBJECT-PROPERTY CONTRACT:\n"
                        + step_obj_err
                        + "\n\nRules:\n"
                        + "- If a step-class ontology comment says an object property must be linked, the step constructor must materialize that property.\n"
                        + "- Resolve/reuse the target object inside the same locked graph context and add the ontology-declared predicate triple before export.\n"
                        + "- Do not rely only on parent-level links for a mandatory step-level object property.\n"
                    )
                    continue
                raise ValueError(last_error)

            ok_step_pred, step_pred_err = _validate_step_scoped_constructor_predicates(
                code,
                profile=concise_structure.get("integrity_profile", {}) or {},
                class_structures=class_structures,
                classes_in_part=class_names,
                contract_specs=contract_step_scoped_specs,
            )
            if not ok_step_pred:
                last_error = f"Step-scoped constructor predicate violation: {step_pred_err}"
                print(f"   ❌ {last_error}")
                if attempt < max_retries:
                    prompt += (
                        "\n\n⚠️ FIX STEP-SCOPED CONSTRUCTOR PREDICATES:\n"
                        + step_pred_err
                        + "\n\nRules:\n"
                        + "- When a constructor accepts a label/IRI for a step-scoped target object, add the exact ontology-declared predicate for that class, not only a parent-level or generic relation.\n"
                        + "- Keep any generic relation only if it is also declared; do not replace a specific step relation with a generic one.\n"
                    )
                    continue
                raise ValueError(last_error)

            # OM-2 call style validation (prevents passing unit IRIs / positional args to base helper).
            if uses_om2:
                ok_quantity_links, quantity_link_err = _validate_om2_quantity_links(
                    code,
                    class_structures=class_structures,
                    classes_in_part=class_names,
                )
                if not ok_quantity_links:
                    last_error = f"OM-2 quantity link violation: {quantity_link_err}"
                    print(f"   ❌ {last_error}")
                    if attempt < max_retries:
                        prompt += (
                            "\n\n⚠️ FIX OM-2 QUANTITY LINKS:\n"
                            + quantity_link_err
                            + "\n\nRules:\n"
                            + "- Assign the result of `_find_or_create_om2_quantity(...)` to a variable such as `quantity_iri`.\n"
                            + "- Add the ontology-declared property triple from the entity being created to that quantity IRI.\n"
                            + "- Do not create standalone OM-2 quantities that are not reachable from the created entity.\n"
                        )
                        continue
                    raise ValueError(last_error)

                ok_calls, call_err = _validate_om2_entity_call_style(code)
                if not ok_calls:
                    last_error = f"OM-2 entity call-style violation: {call_err}"
                    print(f"   ❌ {last_error}")
                    if attempt < max_retries:
                        prompt += (
                            "\n\n⚠️ FIX OM-2 CALL STYLE:\n"
                            + call_err
                            + "\n\nUse keyword-only calls like:\n"
                            + "_find_or_create_om2_quantity(g, quantity_class=..., label=..., value=..., unit_label=unit)\n"
                        )
                        continue
                    raise ValueError(last_error)

                ok_resolve, resolve_err = _validate_resolve_om2_unit_call_style(code)
                if not ok_resolve:
                    last_error = f"OM-2 resolve-unit call-style violation: {resolve_err}"
                    print(f"   ❌ {last_error}")
                    if attempt < max_retries:
                        prompt += (
                            "\n\n⚠️ FIX _resolve_om2_unit CALL STYLE:\n"
                            + resolve_err
                            + "\n\nCall it ONLY as:\n"
                            + "_resolve_om2_unit(unit_label)\n"
                            + "Do NOT pass a Graph as the first argument.\n"
                        )
                        continue
                    raise ValueError(last_error)

            # Write validated code
            output_path = Path(output_dir) / f"{ontology_name}_creation_entities_{part_number}.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)

            print(f"   ✅ Generated: {output_path.name} ({len(code)} chars) - Syntax OK")
            return str(output_path)

        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                raise Exception(f"Failed after {max_retries} attempts. Last error: {last_error}")

