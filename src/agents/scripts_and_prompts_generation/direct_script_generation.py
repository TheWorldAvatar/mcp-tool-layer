#!/usr/bin/env python3
"""
Direct LLM Script Generation (Domain-Agnostic)

This module provides direct LLM-based script generation that:
1. Loads domain-agnostic meta-prompts from ape_generated_contents/meta_prompts/mcp_scripts/
2. Parses T-Box ontology TTL to extract entity classes, properties, relationships
3. Fills meta-prompt templates with extracted domain-specific information
4. Calls LLM API directly (no agents, no MCP tools)
5. Writes generated code to files

The meta-prompts should not contain hardcoded domain class/property/entity names.
All domain-specific information should come from parsing the TTL T-Box (or config artefacts),
not from hardcoded example lists inside prompts or this generator.
"""

import os
import sys
import re
import asyncio
import ast
import json
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple, Any
try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
from dotenv import load_dotenv
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL

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
    lines.append("- Define these namespaces EXACTLY as below (do not invent alternatives like `/kg/ontosynthesis/`).")
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
        m = re.search(r'^NAMESPACE\s*=\s*Namespace\(".*?"\)\s*$', out, flags=re.MULTILINE)
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
            for kw in node.keywords:
                if kw.arg == "iri":
                    errors.append("_format_success_json passed iri= as keyword (iri must be positional)")
                    break

    if errors:
        # Return a stable, readable error summary
        return False, "; ".join(sorted(set(errors)))
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
    Ensure each @mcp.tool wrapper has a docstring, and that docstrings include a short T-Box restriction hint
    when available (from rdfs:comment).
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
        hint = _tbox_hint_for_tool(node.name, class_comments, prop_comments)
        if hint:
            if "T-Box:" not in base:
                base = base.rstrip() + f"\n\nT-Box: {hint}"
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
      def create_HeatChill(..., hasTemperatureRate_value=None, ...):
          return _create_HeatChill(..., hasTemperatureRate_value=hasTemperature_rate_value, ...)

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
        if n.startswith(("create_", "add_", "check_existing_", "check_and_report_")) or n == "pipeline":
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

    lines.append(f"mcp = FastMCP({ontology_name!r})")
    lines.append("")
    lines.append("INSTRUCTION_PROMPT: str = (")
    lines.append(f"    'You are operating a {ontology_name} FastMCP server for ontology-backed KG construction.\\n'")
    lines.append("    'Typical workflow: init_memory -> check_existing_* -> create_* -> add_* -> export_memory.\\n'")
    lines.append(")")
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
    model_name: str = "gpt-4o",
    max_retries: int = 1,
) -> str:
    """
    Two-step "divide & conquer" main generation:
      1) LLM generates two FRAGMENTS (core + relationships), each wrapping a subset of tools.
      2) LLM stitches those fragments into one runnable `main.py`.
    """
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

    core = [f for f in uniq if (f["name"] in {"init_memory_wrapper", "export_memory_wrapper"} or f["name"].startswith("check_existing_") or f["name"].startswith("create_"))]
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
                    temperature=0,
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
                temperature=0,
                **_token_limit_kwargs(model_name, 16000),
            )
            main_code = extract_code_from_response(resp.choices[0].message.content or "")
            if not main_code:
                raise ValueError("LLM returned empty stitched main.py")

            # Deterministically fix import ownership in final main.py too (also strips bogus relative imports).
            main_code = _rewrite_main_relative_imports(main_code, owners)
            main_code = _strip_placeholder_module_imports(main_code)
            main_code = _ensure_mcp_tool_docstrings_with_tbox(main_code, doc_map, tbox_class_comments, tbox_prop_comments)
            # Fix and validate wrapper forwarding to avoid NameError-inducing typos.
            main_code = _rewrite_main_wrapper_forwarding_param_typos(main_code)

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
        for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
            if str(prop).startswith(str(ontology_ns)):
                for domain in g.objects(prop, RDFS.domain):
                    # Extract all classes from domain (handles unions)
                    domain_classes = extract_classes_from_domain(domain)
                    if class_name in domain_classes:
                        prop_name = str(prop).replace(str(ontology_ns), '')
                        datatype_inputs.append(prop_name)
        
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
            'parent_classes': parents
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
    }


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
        "It includes only structural information needed for script generation:",
        "- Class definitions",
        "- Object property connections (domain → range)",
        "- Datatype property assignments (domain)",
        "- Class hierarchy (inheritance)",
        "- Required creation functions",
        "",
        "**Excluded:** verbose rdfs:comment fields and other metadata.",
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
        structure = class_structures.get(cls, {})  # classes list has full keys like "OntoSyn/Add"
        
        lines.append(f"### `create_{class_name}` Parameters:")
        lines.append("")
        lines.append("```python")
        lines.append(f"def create_{class_name}(")
        lines.append("    label: str,  # Required")
        
        # Datatype properties with type inference
        datatype_props = structure.get('datatype_inputs', [])
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
                    if "/kg/OntoLab/" in iri:
                        return f"`ontolab:{t}`"
                    if "/kg/ontomops/" in iri:
                        return f"`ontomops:{t}`"
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
            for prop in structure['datatype_inputs']:
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
    # IMPORTANT: include OM-2 mock T-Box (if present) so unit inventory is available to the LLM
    # strictly via ontology-derived input (no hardcoded unit tables in prompts).
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    
    # Format as markdown
    markdown_content = format_concise_structure_as_markdown(concise_structure, ontology_name)
    
    # Save to file
    output_path = structures_dir / f"{ontology_name}_concise.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    
    return output_path


def extract_code_from_response(response: str) -> str:
    """Extract Python code from LLM response, removing markdown formatting if present."""
    
    # Try to extract code from markdown code blocks
    code_block_pattern = r'```(?:python)?\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, response, re.DOTALL)
    
    if matches:
        # Use the largest code block (likely the main code)
        return max(matches, key=len).strip()

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
    return s


def build_underlying_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating an underlying MCP script using domain-agnostic meta-prompt.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        
    Returns:
        Complete prompt string with TTL-extracted information filled into meta-prompt
    """
    # Load domain-agnostic meta-prompt
    meta_prompt_template = load_meta_prompt('direct_underlying_script_prompt.md')
    
    # Extract CONCISE ontology structure (focused on connections and inputs, no verbose comments).
    # Include OM-2 mock (if present) so unit inventory is available to the LLM strictly via ontology-derived input.
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    
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
) -> str:
    """
    Build the prompt for generating a FastMCP main script using domain-agnostic meta-prompt.
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
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
    
    return prompt


def build_base_script_prompt(ontology_path: str, ontology_name: str) -> str:
    """
    Build the prompt for generating the BASE/INFRASTRUCTURE script (guard system, namespaces, helpers ONLY).
    
    Args:
        ontology_path: Path to the TTL ontology file
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        
    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_base_script_prompt.md')
    
    # Extract concise ontology structure (minimal - just namespace and classes for _find_or_create helpers)
    # Include OM-2 mock so unit inventory is available to the LLM purely via ontology-derived input.
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    
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
            f"OM-2 Unit Inventory (ontology-derived; use to build unit enforcement + label→IRI mapping):\n{om2_block}"
        ),
        universal_utils_functions=", ".join(UNIVERSAL_UTILS_FUNCTIONS)
    )

    # Enforce namespace correctness deterministically via a contract block (config-driven).
    prompt += "\n\n" + _namespace_contract_block(concise_structure, ontology_name)

    # Strengthen OM-2 unit handling deterministically (domain-agnostic).
    # This reduces LLM variability and prevents signature/call-style mismatches across modules.
    if om2_lines:
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
    
    # Extract concise ontology structure (include OM-2 mock so referenced external concepts are visible)
    full_concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    
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
        
        if structure['object_connections']:
            ontology_structure_lines.append(f"  Object property connections:")
            for prop, range_cls in structure['object_connections']:
                ontology_structure_lines.append(f"    - {prop} → {range_cls}")
        
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

    # Enforce namespace correctness deterministically via a config-driven contract block.
    prompt += "\n\n" + _namespace_contract_block(full_concise_structure, ontology_name)

    # Enforce OM-2 call style contract when OM-2 unit inventory exists.
    if _ontology_has_om2_unit_inventory(ontology_path):
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
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        
    Returns:
        Complete prompt string
    """
    meta_prompt_template = load_meta_prompt('direct_entities_script_prompt.md')
    
    # Extract concise ontology structure (include OM-2 mock so referenced external concepts are visible)
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    
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
    
    return prompt


async def generate_base_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
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
            concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
            code = _apply_namespace_contract_to_code(code, concise_structure)

            # Validate syntax before writing
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_base.py")
            if not is_valid:
                raise ValueError(f"Syntax: {syntax_error}")

            # OM-2 contract validation (prevents downstream unit-handling breakage).
            if _ontology_has_om2_unit_inventory(ontology_path):
                ok_om2, om2_err = _validate_om2_base_contract(code)
                if not ok_om2:
                    raise ValueError(f"OM-2 base contract violation: {om2_err}")
            
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
    model_name: str = "gpt-4o",
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
            if _ontology_has_om2_unit_inventory(ontology_path):
                ok_calls, call_err = _validate_om2_entity_call_style(code)
                if not ok_calls:
                    raise ValueError(f"OM-2 entity call-style violation: {call_err}")
            
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
    model_name: str = "gpt-4o",
    max_retries: int = 3
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
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
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
            max_retries=max_retries
        )
        generated_scripts.append(script_path)
    
    print(f"\n   ✅ Generated 2 entity creation scripts")
    return generated_scripts


async def generate_entities_script_direct_legacy(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
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
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """
    Generate an underlying MCP script using direct LLM calls with domain-agnostic meta-prompts.
    
    NOTE: This function is now used for generating the BASE script only.
    Entity creation functions are split across multiple scripts via generate_entity_group_script_direct().
    
    Args:
        ontology_path: Path to ontology TTL file
        ontology_name: Short name of ontology (e.g., 'ontosynthesis')
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
                temperature=0,
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
    model_name: str = "gpt-4o",
    max_retries: int = 3
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
                temperature=0,
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
            # Fix and validate wrapper forwarding to avoid NameError-inducing typos.
            code = _rewrite_main_wrapper_forwarding_param_typos(code)

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
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """Generate check_existing_* functions with syntax validation."""
    print(f"   Generating check_existing functions...")
    
    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    with open(concise_md_path, 'r', encoding='utf-8') as f:
        concise_content = f.read()
    
    prompt = f"""Generate {ontology_name}_creation_checks.py

CRITICAL: Code MUST compile without syntax errors.

Use EXACTLY these imports:
```python
from rdflib import Graph, Namespace, URIRef, RDF, RDFS
from ..universal_utils import locked_graph, _list_instances_with_label
from .{ontology_name}_creation_base import _guard_check, NAMESPACE
```

REQUIRED FUNCTIONS (from concise ontology):
{concise_content[90:600]}

ALSO REQUIRED (reference parity):
- Implement `check_and_report_order_consistency() -> str` (MUST be decorated with `@_guard_check`):
  - Function signature MUST take NO arguments (use `with locked_graph() as g:` internally)
  - Discover the relevant predicates from the provided ontology structure:
    - Identify one or more object properties that link a "container" to ordered members.
    - Identify a datatype property that encodes an order/index on the member node (order-like property).
  - Report (per container IRI) duplicate order values and missing order numbers in the range 1..max(order)
  - If no synthesis/steps/orders exist, return a clear message instead of crashing
  - Return a human-readable multi-line string

Generate WORKING Python code with ALL check_existing functions listed above PLUS `check_and_report_order_consistency`."""
    
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
                    {"role": "user", "content": prompt}
                ],
                temperature=_get_temperature_for_model(model_name),
                **_token_limit_kwargs(model_name, 8000)
            )
            
            code = extract_code_from_response(response.choices[0].message.content or "")
            if not code:
                raise ValueError("Empty response")
            
            # VALIDATE SYNTAX
            is_valid, syntax_error = validate_python_syntax(code, f"{ontology_name}_creation_checks.py")
            if not is_valid:
                last_error = f"Syntax: {syntax_error}"
                print(f"   ❌ Syntax error: {syntax_error}")
                if attempt < max_retries:
                    prompt += f"\n\n⚠️ FIX THIS SYNTAX ERROR:\n{syntax_error}"
                    continue
                raise ValueError(f"Syntax errors after {max_retries} attempts: {syntax_error}")

            # Extra semantic guardrails: order checker must take NO arguments.
            if "def check_and_report_order_consistency(" in code and "def check_and_report_order_consistency()".replace("()", "") not in code:
                # Use AST to enforce exact signature: no arguments.
                import ast
                try:
                    mod = ast.parse(code)
                    for node in mod.body:
                        if isinstance(node, ast.FunctionDef) and node.name == "check_and_report_order_consistency":
                            if node.args.args or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
                                raise ValueError("has_args")
                except Exception:
                    last_error = "Order-consistency checker must have signature: def check_and_report_order_consistency() -> str"
                    print(f"   ❌ Invalid order checker: {last_error}")
                    if attempt < max_retries:
                        prompt += "\n\n⚠️ FIX: check_and_report_order_consistency must take NO arguments and return str."
                        continue
                    raise ValueError(last_error)
            
            output_path = Path(output_dir) / f"{ontology_name}_creation_checks.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            print(f"   ✅ Generated: {output_path.name} - Syntax OK")
            return str(output_path)
            
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                raise


async def generate_relationships_script_direct(
    ontology_path: str,
    ontology_name: str,
    output_dir: str,
    model_name: str = "gpt-4o",
    max_retries: int = 3
) -> str:
    """Generate relationship/add_* functions using an LLM meta-prompt (reference-parity, not template)."""
    print("   Generating relationship functions (LLM, per-property + ergonomic helpers)...")

    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    concise_content = concise_md_path.read_text(encoding="utf-8")

    meta_prompt_template = load_meta_prompt("direct_relationships_script_prompt.md")

    # Add a strict namespace + locked_graph contract to prevent the known failure modes:
    # - wrong namespace IRIs (e.g., /kg/ontosynthesis/ instead of /kg/OntoSyn/)
    # - locked_graph misused as locked_graph(g) (Graph passed into doi parameter) -> runtime failure
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    contracts = "\n\n".join(
        [
            _namespace_contract_block(concise_structure, ontology_name),
            "CRITICAL LOCKED_GRAPH CONTRACT (MUST FOLLOW EXACTLY):\n"
            "- Always use: `with locked_graph() as g:` (NO arguments to locked_graph).\n"
            "- NEVER call `locked_graph(g)` or pass a Graph into locked_graph.\n"
            "- Relationship mutations must happen inside the locked_graph context.\n",
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
                        "\n\n⚠️ FIX REQUIRED: You misused base JSON helpers.\n"
                        "- `_format_error(message, *, code=...)` must NOT be called as `_format_error(CODE, message)`.\n"
                        "- `_format_success_json(iri, message, *, created=...)` must NOT pass `iri=` as a keyword; `iri` must be positional.\n"
                        "Return the FULL corrected Python file."
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

            ontology_order_hint = re.search(r"\b(hasorder|order|step[_-]?order|sequence[_-]?index|position)\b", concise_content, re.IGNORECASE) is not None
            code_order_param = False
            try:
                mod = ast.parse(code)
                for node in mod.body:
                    if isinstance(node, ast.FunctionDef):
                        arg_names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
                        for a in arg_names:
                            a_l = a.lower()
                            if ("order" in a_l) or (a_l in {"sequence_index", "sequenceindex", "position"}):
                                code_order_param = True
                                break
                    if code_order_param:
                        break
            except Exception:
                # If parsing fails here, syntax validation would already have caught it; keep guardrail conservative.
                code_order_param = False

            needs_order_enforcement = ontology_order_hint or code_order_param
            if needs_order_enforcement:
                code_l = code.lower()

                # Look for strong signals of mutation-time enforcement.
                # We accept either an explicit helper function name pattern, or textual/structural hints.
                has_helper_name = re.search(r"def\s+_(enforce|validate|check)_[a-z0-9_]*(order|orders)", code, re.IGNORECASE) is not None
                mentions_contiguity = ("contiguous" in code_l) or ("non-contiguous" in code_l) or ("noncontiguous" in code_l)
                mentions_duplicate = ("duplicate" in code_l) or ("dedup" in code_l) or ("already exists" in code_l)
                mentions_expected_range = ("range(1" in code_l) or ("1.." in code_l) or ("expected" in code_l and "order" in code_l)

                if not (has_helper_name or (mentions_duplicate and (mentions_contiguity or mentions_expected_range))):
                    last_error = "Missing order-consistency mutation-time enforcement (order-like behavior detected)."
                    print(f"   ❌ Semantic check failed: {last_error}")
                    if attempt < max_retries:
                        prompt += (
                            "\n\n⚠️ FIX REQUIRED: The ontology and/or your API indicates an ordered-membership workflow "
                            "(an order-like parameter such as `order` / `sequence_index` / `position`). "
                            "Your relationships module MUST enforce order consistency at mutation time when linking/adding "
                            "ordered members into a container: reject duplicates and reject non-contiguous sequences (1..max). "
                            "Implement a private helper (e.g., `_enforce_contiguous_orders(...)`) and call it from the relevant add_* function(s)."
                        )
                        continue
                    raise ValueError(last_error)

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

                merged = _merge_relationship_parts(part_codes)
                ok, err = validate_python_syntax(merged, f"{ontology_name}_creation_relationships.py")
                if not ok:
                    raise ValueError(f"Merge produced invalid syntax: {err}")

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
    model_name: str = "gpt-4o",
    max_retries: int = 3
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
    
    # Load concise ontology for signatures
    output_base_dir = Path("ai_generated_contents_candidate")
    concise_md_path = output_base_dir / "ontology_structures" / f"{ontology_name}_concise.md"
    with open(concise_md_path, 'r', encoding='utf-8') as f:
        concise_content = f.read()
    
    # Build ontology-derived superclass requirements for this part.
    concise_structure = extract_concise_ontology_structure(ontology_path, include_om2_mock=True)
    class_structures = concise_structure.get("class_structures", {}) or {}

    class_names = [cls.split('/')[-1] for cls in classes_to_generate]
    classes_list = "\n".join([f"- {name}" for name in class_names])

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
    
    # Build strong prompt with explicit requirements.
    # IMPORTANT: include OM-2 unit inventory + remind the model to include related external concepts (e.g., Temperature)
    # when mentioned by the ontology.
    # Build a config-driven namespace import list for the entities script prompt (no hardcoded namespace var names).
    try:
        _ns_map = _render_namespaces_from_config(concise_structure)
        _extra_ns = [k for k in _ns_map.keys() if k != "NAMESPACE"]
        _extra_ns_sorted = sorted({k for k in _extra_ns if isinstance(k, str) and k.isidentifier()})
        _ns_import_line = (", " + ", ".join(_extra_ns_sorted)) if _extra_ns_sorted else ""
    except Exception:
        _ns_import_line = ""

    prompt = f"""Generate {ontology_name}_creation_entities_{part_number}.py

CRITICAL REQUIREMENTS:
1. The code MUST compile without syntax errors.
2. Import ONLY existing names from base script (do NOT invent imports).
3. Return JSON STRING (use json.dumps()), NOT dict objects.
4. If the ontology mentions external OM-2 quantity concepts (e.g., Temperature), you MUST include the relevant creation logic.
   - Do NOT hardcode any unit tables not present in the provided ontology-derived unit inventory.
5. OM-2 strictness: DO NOT define per-file unit tables (e.g., `_TEMPERATURE_UNITS`, `_PRESSURE_UNIT_MAP`, etc.).
   Always use the shared `OM2_UNIT_MAP` + `_find_or_create_om2_quantity` imported from `{ontology_name}_creation_base`.

6. OM-2 call-style contract (IMPORTANT):
   - `_find_or_create_om2_quantity` MUST be called with keyword arguments:
     `_find_or_create_om2_quantity(g, quantity_class=..., label=..., value=..., unit_label=...)`
   - Do NOT pass unit IRIs; `unit_label` must be the unit label string (e.g., "degree celsius").
   - Do NOT call `_find_or_create_om2_quantity` with positional args beyond the first graph `g`.
   - `_resolve_om2_unit` MUST be called as `_resolve_om2_unit(unit_label)` (single argument).
     Do NOT pass the graph as a first argument (i.e., NEVER `_resolve_om2_unit(g, unit_label)`).

7. Guard + universal_utils runtime contracts (IMPORTANT):
   - `_guard_noncheck` is a DECORATOR. NEVER call `_guard_noncheck()` inside functions.
   - Every `create_*` function MUST be decorated with `@_guard_noncheck`.
   - `_mint_hash_iri` MUST be called as `_mint_hash_iri(class_local)` with EXACTLY one argument (no keywords).
     Do NOT pass a namespace or a label to `_mint_hash_iri`.
   - `_export_snapshot_silent` is optional/no-op; if used, call it with NO arguments: `_export_snapshot_silent()`.
     Do NOT call `_export_snapshot_silent(g)`.

8. Class hierarchy typing (CRITICAL):
   - If a class has parent classes in the ontology-derived structure, you MUST assert rdf:type for BOTH:
     - the concrete class (subclass)
     - AND each parent/ancestor class (superclasses)
   - Do NOT rely on RDFS reasoning at runtime. Emit explicit rdf:type triples.
   - For this part, the ontology-derived inheritance summary is:
{inheritance_block}

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
    _find_by_type_and_label, _set_single_label, _export_snapshot_silent
)
from .{ontology_name}_creation_base import (
    _guard_noncheck, NAMESPACE{_ns_import_line},
    # Additional namespaces are provided via the namespace contract block; import them if defined in base.
    _format_error, _format_success_json,
    # REQUIRED for OM-2: use shared unit inventory + reuse helper from base (do NOT define per-file unit maps)
    OM2_UNIT_MAP, _resolve_om2_unit, _find_or_create_om2_quantity
)
```

MANDATORY: OM-2 quantities
- If the ontology-derived input mentions OM-2 quantities (Temperature/Pressure/Duration/Volume/TemperatureRate/AmountOfSubstanceFraction),
  you MUST also implement create functions for them:
  - create_temperature(label: str, value: float, unit: str) -> str
  - create_pressure(...)
  - create_duration(...)
  - create_volume(...)
  - create_temperature_rate(...)
  - create_amount_of_substance_fraction(...)
  Unit validation MUST be done via ontology-derived unit labels (from the OM-2 unit inventory section).

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

            # OM-2 call style validation (prevents passing unit IRIs / positional args to base helper).
            if _ontology_has_om2_unit_inventory(ontology_path):
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

