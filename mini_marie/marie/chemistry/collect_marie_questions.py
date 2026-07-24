"""Collect Marie demo example questions into marie_competency_questions.md."""
from __future__ import annotations

import json
import re
import ssl
import urllib.request
from pathlib import Path

UA = "curl/8.0"
CTX = ssl.create_default_context()
OUT = Path(__file__).resolve().parents[1] / "docs" / "resources" / "marie" / "marie_competency_questions.md"
BASE = "https://theworldavatar.io/demos/marie"
CACHE_HTML = Path(__file__).resolve().parents[1] / "docs" / "resources" / "marie_page_index.html"

HEADING_NS = {
    "Chemical Species (general)": "ontospecies",
    "Chemical Species (acid-base ionisation constants)": "ontospecies",
    "Gas-Phase Reaction Mechanisms": "ontokin",
    "Quantum Chemistry Computations": "ontocompchem",
    "Zeolites": "ontozeolite",
    "Metal-Organic Polyhedra": "ontomops",
}

# MCP tools mapped from Marie example questions + T-Box properties
TOOL_HINTS = {
    "ontospecies": [
        "get_species_by_molecular_formula",
        "lookup_species_by_label",
        "get_species_hbond_counts",
        "get_species_uses",
        "get_pka_by_inchi_fragment",
        "get_pka_by_label",
        "count_species_with_pka",
    ],
    "ontokin": [
        "list_reaction_mechanisms",
        "count_gas_phase_reactions",
        "find_mechanisms_with_reaction_label",
        "list_mechanism_reactions",
    ],
    "ontocompchem": [
        "get_homo_lumo_by_species",
        "get_zero_point_energy_by_basis",
        "get_rotational_constants",
    ],
    "ontozeolite": [
        "list_materials_by_framework_code",
        "get_reference_zeolite_for_framework",
        "lookup_zeolite_material_by_formula_fragment",
        "count_zeolite_frameworks",
    ],
    "ontomops": [
        "(instance queries via twa-mops MCP — Blazegraph ontomops namespace empty)",
    ],
    "ontoprovenance": ["lookup_author_by_name"],
    "ontopesscan": [],
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_json_array_after_key(text: str, key: str) -> list | None:
    # Next.js RSC embeds JSON with escaped quotes: \"key\":
    for needle in (f'"{key}":', f'\\"{key}\\":'):
        idx = text.find(needle)
        if idx < 0:
            continue
        start = text.find("[", idx)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    raw = text[start : i + 1].replace('\\"', '"')
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        break
    return None


def extract_groups(html: str) -> list[dict]:
    groups = parse_json_array_after_key(html, "exampleQuestionGroups")
    if groups:
        return groups
    # fallback: SSR first tab only
    titles = re.findall(r'role="tab"[^>]*>([^<]{3,120})</button>', html)
    out = []
    for t in titles:
        out.append({"heading": t.strip(), "questions": []})
    return out


def to_markdown(groups: list[dict]) -> str:
    lines = [
        "# Marie competency / example questions",
        "",
        "Natural-language example questions from the Marie demo "
        "([theworldavatar.io/demos/marie](https://theworldavatar.io/demos/marie)). "
        "Grouped as on the homepage **Example Questions** tabs. "
        "Each maps to a chemistry Blazegraph namespace and MCP tools derived from the local T-Box.",
        "",
        "**Refresh:** `python -m mini_marie.marie.chemistry.collect_marie_questions`",
        "",
        "---",
        "",
    ]
    n = 0
    for grp in groups:
        heading = grp.get("heading") or grp.get("title") or "Examples"
        ns = HEADING_NS.get(heading, "ontospecies")
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(f"- **Namespace:** `{ns}`")
        lines.append(f"- **MCP:** `chemistry-{ns}`")
        tools = TOOL_HINTS.get(ns, [])
        if tools:
            lines.append(f"- **Tools:** {', '.join(f'`{t}`' for t in tools)}")
        lines.append("")
        for q in grp.get("questions", []):
            text = q if isinstance(q, str) else str(q.get("question", q))
            text = text.strip()
            if not text:
                continue
            n += 1
            lines.append(f"### MQ{n}")
            lines.append("")
            lines.append(text)
            lines.append("")
    lines.extend(
        [
            "---",
            "",
            "## Tool design notes (T-Box → MCP)",
            "",
            "| Namespace | T-Box anchors | Example Marie themes |",
            "|-----------|---------------|----------------------|",
            "| `ontospecies` | `Species`, `hasMolecularFormula`, `hasDissociationConstants`, `hasHydrogenBondDonorCount`, `hasUse` | formulas, pKa, H-bonds, uses |",
            "| `ontokin` | `ReactionMechanism`, `GasPhaseReaction`, rate coefficients | mechanism lists, reaction comparisons |",
            "| `ontocompchem` | `HOMOEnergy`, `LUMOEnergy`, `ZeroPointEnergy`, `RotationalConstants` | QM energies, ZPE, rot constants |",
            "| `ontozeolite` | `ZeoliteFramework`, `hasFrameworkCode`, `isReferenceZeolite`, `ontocrystal:UnitCell` | framework codes, guests, unit cells |",
            "| `ontomops` | `MetalOrganicPolyhedron`, CBU (T-box only on chemistry host) | MOP size, CBU, provenance → use `twa-mops` |",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    html = fetch(BASE + "/")
    CACHE_HTML.write_text(html, encoding="utf-8")
    groups = extract_groups(html)
    if not groups or not any(g.get("questions") for g in groups):
        # use cache if live page omits RSC payload
        if CACHE_HTML.exists():
            groups = extract_groups(CACHE_HTML.read_text(encoding="utf-8"))
    if not groups or not any(g.get("questions") for g in groups):
        raise SystemExit("Failed to parse exampleQuestionGroups from Marie homepage")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(to_markdown(groups), encoding="utf-8")
    total = sum(len(g.get("questions", [])) for g in groups)
    print(f"Wrote {OUT} ({total} questions in {len(groups)} groups)")


if __name__ == "__main__":
    main()
