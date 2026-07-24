"""Extract structured data from cached Marie demo pages."""
from __future__ import annotations

import json
import re
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root

OUT = mini_marie_cache_root() / "marie_demo"


def extract_example_questions(index_text: str) -> list[dict]:
    m = re.search(r'"exampleQuestionGroups":(\[.*?\])\}\]\}', index_text, re.S)
    if not m:
        return []
    return json.loads(m.group(1))


def extract_species_filters(html: str) -> list[str]:
    # Advanced search page embeds property metadata in SSR payload
    props = set(re.findall(r'"propertyName":"([^"]+)"', html))
    labels = set(re.findall(r'"label":"([^"]{3,80})"', html))
    return sorted(props), sorted(labels)[:50]


def strings_from_js(min_len: int = 8) -> list[str]:
    hits: set[str] = set()
    for p in (OUT / "js").glob("*.js"):
        for m in re.findall(r'"([^"]{' + str(min_len) + r',200})"', p.read_text(encoding="utf-8", errors="replace")):
            if any(k in m.lower() for k in ("api", "query", "marie", "chemistry", "sparql", "agent", "search")):
                hits.add(m)
    return sorted(hits)


def main() -> None:
    index = (OUT / "index.html").read_text(encoding="utf-8", errors="replace")
    eq = extract_example_questions(index)
    total_q = sum(len(g.get("questions", [])) for g in eq)

    species_html = ""
    sp = OUT / "search__species.html"
    if sp.exists():
        species_html = sp.read_text(encoding="utf-8", errors="replace")

    props, labels = extract_species_filters(species_html) if species_html else ([], [])
    js_strings = strings_from_js()

    report = {
        "example_groups": len(eq),
        "example_questions_total": total_q,
        "example_question_groups": eq,
        "species_search_property_names": props[:80],
        "species_search_property_count": len(props),
        "species_search_sample_labels": labels,
        "js_api_related_strings": js_strings[:60],
    }
    path = OUT / "extracted_content.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"groups={len(eq)} questions={total_q} species_props={len(props)} js_hits={len(js_strings)}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
