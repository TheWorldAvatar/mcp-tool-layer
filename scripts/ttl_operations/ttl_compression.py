#!/usr/bin/env python3
"""
Turtle compressor: shorten repeated IRIs into prefixes and trim noise.

Usage:
  python -m scripts.ttl_compression -i input.ttl -o output.ttl
Options:
  --min-count N       minimum URI-namespace repeats to create a prefix (default 3)
  --max-prefixes N    cap number of auto prefixes (default 24)
  --keep-comments     keep lines starting with #
  --no-minify         do not collapse extra whitespace
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Match angle-bracket IRIs only (safe to rewrite; avoid literals)
IRI_RE = re.compile(r"<(http[s]?://[^>\s]+)>")
# Existing prefixes: @prefix or PREFIX
PREFIX_DECL_RE = re.compile(
    r"^\s*(?:@prefix|PREFIX)\s+([A-Za-z][\w\-]*)\s*:\s*<([^>]+)>\s*\.\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Simple, conservative PN_LOCAL check (not full Turtle spec but safe)
PN_LOCAL_SAFE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-\.]*$")

# Common vocabularies
KNOWN_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "schema": "http://schema.org/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "prov": "http://www.w3.org/ns/prov#",
    "sh": "http://www.w3.org/ns/shacl#",
    "time": "http://www.w3.org/2006/time#",
    "geo": "http://www.opengis.net/ont/geosparql#",
}


def split_namespace(iri: str):
    """Split IRI into (ns, local) by last '#' or '/'."""
    cut = max(iri.rfind("#"), iri.rfind("/"))
    if cut == -1 or cut == len(iri) - 1:
        return None, None
    ns, local = iri[: cut + 1], iri[cut + 1 :]
    return ns, local


def derive_prefix_name(ns: str):
    """
    Derive a stable short name from the namespace.
    Strategy: last non-empty path segment or domain token.
    Fallback to nsN assigned later.
    """
    rest = ns.split("://", 1)[-1]
    rest = rest.rstrip("#/")

    candidates = []
    parts = rest.split("/")
    if parts:
        last = parts[-1]
        if last:
            candidates.append(last)
    # Domain token
    domain = parts[0] if parts else rest
    if domain:
        domain_main = domain.split(":")[0].split(".")
        if len(domain_main) >= 2:
            candidates.append(domain_main[-2])
        elif domain_main:
            candidates.append(domain_main[0])

    for cand in candidates:
        cand = re.sub(r"[^A-Za-z0-9_\-]", "", cand)
        if not cand:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9_\-]*$", cand):
            continue
        return cand.lower()

    return None  # will be assigned later as ns1, ns2...


def collect_existing_prefixes(text: str):
    mapping = {}
    for m in PREFIX_DECL_RE.finditer(text):
        prefix, uri = m.group(1), m.group(2)
        mapping[prefix] = uri
    return mapping


def invert_mapping(mapping: dict):
    inv = defaultdict(list)
    for k, v in mapping.items():
        inv[v].append(k)
    return inv


def main():
    ap = argparse.ArgumentParser(description="Compress TTL by prefixing repeated IRIs.")
    ap.add_argument("-i", "--input", type=str, required=False, help="Input TTL file. Default: stdin")
    ap.add_argument("-o", "--output", type=str, required=False, help="Output TTL file. Default: stdout")
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--max-prefixes", type=int, default=24)
    ap.add_argument("--keep-comments", action="store_true")
    ap.add_argument("--no-minify", action="store_true")
    args = ap.parse_args()

    raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()

    # Preserve existing prefix decls and add known ones if used
    existing_prefixes = collect_existing_prefixes(raw)

    # Start with known mappings, but avoid collisions with existing different URIs
    prefix_map = dict(existing_prefixes)
    for k, v in KNOWN_PREFIXES.items():
        if k in prefix_map and prefix_map[k] != v:
            continue
        prefix_map.setdefault(k, v)

    # Count namespaces from IRIs in angle brackets
    ns_counter = Counter()
    iris = IRI_RE.findall(raw)
    for iri in iris:
        ns, local = split_namespace(iri)
        if ns and local and PN_LOCAL_SAFE_RE.match(local):
            ns_counter[ns] += 1

    # Exclude namespaces already covered
    covered_namespaces = set(prefix_map.values())
    candidates = [(ns, cnt) for ns, cnt in ns_counter.items() if ns not in covered_namespaces and cnt >= args.min_count]
    candidates.sort(key=lambda x: (-x[1], x[0]))

    # Assign names
    used_names = set(prefix_map.keys())
    auto_added = {}
    idx = 1
    for ns, _ in candidates[: args.max_prefixes]:
        base = derive_prefix_name(ns) or f"ns{idx}"
        name = base
        j = 1
        while name in used_names and prefix_map.get(name) != ns:
            name = f"{base}{j}"
            j += 1
        prefix_map[name] = ns
        auto_added[name] = ns
        used_names.add(name)
        idx += 1

    # Build a lookup: namespace -> chosen prefix
    ns_to_prefix = invert_mapping(prefix_map)

    # Rewrite IRIs to qnames where safe
    def repl(m):
        iri = m.group(1)
        ns, local = split_namespace(iri)
        if not ns or not local:
            return f"<{iri}>"
        if not PN_LOCAL_SAFE_RE.match(local):
            return f"<{iri}>"
        choices = ns_to_prefix.get(ns)
        if not choices:
            return f"<{iri}>"
        p = sorted(choices, key=len)[0]
        return f"{p}:{local}"

    rewritten = IRI_RE.sub(repl, raw)

    # Optionally remove comments and extra whitespace
    lines = rewritten.splitlines()
    out_lines = []
    for ln in lines:
        if not args.keep_comments and ln.lstrip().startswith("#"):
            continue
        out_lines.append(ln)
    text = "\n".join(out_lines)

    if not args.no_minify:
        text = "\n".join([l.rstrip() for l in text.splitlines()])
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    # Prepare prefix header for new auto-added prefixes that are actually used
    used_prefixes_in_text = set(re.findall(r"\b([A-Za-z][\w\-]*):[A-Za-z_][A-Za-z0-9_\-\.]*", text))
    header_lines = []
    for pfx in sorted(auto_added.keys()):
        if pfx in used_prefixes_in_text:
            header_lines.append(f"@prefix {pfx}: <{prefix_map[pfx]}> .")

    header = ""
    if header_lines:
        header = "\n".join(header_lines) + "\n\n"

    result = header + text

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
