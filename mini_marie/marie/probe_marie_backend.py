"""Fast backend probe for Marie demo + chemistry KGs."""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root

BASE = "https://theworldavatar.io"
UA = "curl/8.0"
OUT = mini_marie_cache_root() / "marie_demo"
CTX = ssl.create_default_context()
TIMEOUT = 12


def get(url: str) -> tuple[int | str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return "ERR", str(e).encode()


def sparql_count(namespace: str) -> dict:
    q = "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }"
    url = (
        f"{BASE}/chemistry/blazegraph/namespace/{namespace}/sparql"
        f"?query={urllib.parse.quote(q)}"
    )
    code, body = get(url)
    text = body.decode("utf-8", errors="replace")
    m = re.search(r'"value"\s*:\s*"(\d+)"', text)
    return {"status": code, "triples": int(m.group(1)) if m else None, "snippet": text[:200]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    routes = [
        "/demos/marie",
        "/demos/marie/search/species",
        "/demos/marie/search/zeolite-frameworks",
        "/demos/marie/search/zeolitic-materials",
        "/demos/marie/plots/species",
        "/demos/marie/plots/zeolite-frameworks",
        "/demos/marie/ontology-info",
        "/demos/marie/history-info",
    ]
    routes_report = {}
    for route in routes:
        code, body = get(BASE + route)
        routes_report[route] = {"status": code, "size": len(body)}

    api_paths = [
        "/demos/marie/api/query",
        "/demos/marie/api/search",
        "/demos/marie/api/ask",
        "/demos/marie/api/chat",
        "/chemistry/marie/query",
        "/chemistry/marie-agent/query",
        "/chemistry/agent/query",
        "/chemistry/blazegraph/ui/",
    ]
    api_report = {}
    for path in api_paths:
        code, body = get(BASE + path)
        api_report[path] = {
            "status": code,
            "size": len(body),
            "snippet": body[:200].decode("utf-8", errors="replace"),
        }

    namespaces = ["ontospecies", "ontokin", "ontocompchem", "ontozeolite", "ontomops"]
    kg_report = {ns: sparql_count(ns) for ns in namespaces}

    # Extract example questions from cached index if present
    index = OUT / "index.html"
    example_groups = []
    if index.exists():
        text = index.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"exampleQuestionGroups":(\[.*?\])\}\]\}', text, re.S)
        if m:
            try:
                example_groups = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

    report = {
        "base": f"{BASE}/demos/marie",
        "build_id": "SD0pOP2kNokVAc3amT5s-",
        "version": "Marie v6 (current)",
        "routes": routes_report,
        "api_probe": api_report,
        "chemistry_kg": kg_report,
        "example_question_groups": example_groups,
        "sparql_endpoints": {
            ns: f"{BASE}/chemistry/blazegraph/namespace/{ns}/sparql" for ns in namespaces
        },
        "blazegraph_workbench": f"{BASE}/chemistry/blazegraph/ui/#query",
    }
    out_path = OUT / "probe_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("routes", "api_probe", "chemistry_kg")}, indent=2))


if __name__ == "__main__":
    main()
