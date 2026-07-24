"""Probe https://theworldavatar.io/demos/marie — routes, JS bundles, API hints."""

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
MARIE = f"{BASE}/demos/marie"
UA = "curl/8.0"
OUT = mini_marie_cache_root() / "marie_demo"
CTX = ssl.create_default_context()


def get(url: str, timeout: int = 30) -> tuple[int | str, bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return "ERR", str(e).encode(), {}


def post_json(url: str, payload: dict, timeout: int = 60) -> tuple[int | str, bytes]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return "ERR", str(e).encode()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "js").mkdir(exist_ok=True)

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

    route_results: dict[str, dict] = {}
    all_html = ""
    for route in routes:
        code, body, hdrs = get(BASE + route)
        route_results[route] = {
            "status": code,
            "size": len(body),
            "content_type": hdrs.get("Content-Type", ""),
        }
        if code == 200:
            fname = route.replace("/demos/marie", "").strip("/") or "index"
            fname = fname.replace("/", "__")
            (OUT / f"{fname}.html").write_bytes(body)
            all_html += body.decode("utf-8", errors="replace")

    index = (OUT / "index.html").read_text(encoding="utf-8", errors="replace")
    chunks = sorted(set(re.findall(r"/demos/marie/_next/static/chunks/[^\"']+\.js", index)))

    js_text = all_html
    downloaded: list[str] = []
    for chunk in chunks:
        url = BASE + chunk
        code, body, _ = get(url)
        if code == 200:
            name = chunk.split("/")[-1]
            (OUT / "js" / name).write_bytes(body)
            js_text += body.decode("utf-8", errors="replace")
            downloaded.append(chunk)

    # Extract example questions from SSR payload
    eq_match = re.search(r'"exampleQuestionGroups":\[(.*?)\]\}\]\}', index, re.S)
    example_groups: list[dict] = []
    if eq_match:
        raw = "[" + eq_match.group(1) + "]"
        try:
            example_groups = json.loads(raw)
        except json.JSONDecodeError:
            pass

    url_hits = sorted(set(re.findall(r"https?://[a-zA-Z0-9._/-]+", js_text)))
    api_hits = sorted(set(re.findall(r"/demos/marie/[a-zA-Z0-9_./-]+", js_text)))
    env_hits = sorted(set(re.findall(r"NEXT_PUBLIC_[A-Z0-9_]+", js_text)))
    sparql_hits = sorted(set(re.findall(r"[a-zA-Z0-9._/-]*sparql[a-zA-Z0-9._/-]*", js_text, re.I)))

    # Probe likely API paths
    api_candidates = sorted(
        set(
            p
            for p in api_hits
            if any(x in p for x in ("api", "query", "search", "sparql", "ask", "chat"))
        )
    )
    api_probe: dict[str, dict] = {}
    for path in api_candidates[:30]:
        code, body, hdrs = get(BASE + path)
        api_probe[path] = {"status": code, "size": len(body), "snippet": body[:200].decode("utf-8", errors="replace")}

    # Common Marie backend paths (from TWA stack conventions)
    backend_paths = [
        "/demos/marie/api/query",
        "/demos/marie/api/search",
        "/demos/marie/api/ask",
        "/demos/marie/api/chat",
        "/demos/marie/api/sparql",
        "/marie/api/query",
        "/marie-agent/query",
        "/marie/query",
        "/blazegraph/namespace/marie/sparql",
        "/blazegraph/namespace/ontocompchem/sparql",
        "/blazegraph/namespace/ontokin/sparql",
        "/blazegraph/namespace/ontozeolite/sparql",
        "/blazegraph/namespace/ontomops/sparql",
        "/ontop/sparql/",
    ]
    backend_probe: dict[str, dict] = {}
    for path in backend_paths:
        url = BASE + path
        if "sparql" in path:
            q = urllib.parse.urlencode({"query": "ASK { ?s ?p ?o }"})
            code, body, hdrs = get(f"{url}?{q}")
        else:
            code, body, hdrs = get(url)
        backend_probe[path] = {
            "status": code,
            "size": len(body),
            "snippet": body[:300].decode("utf-8", errors="replace"),
        }

    # Try POST to query API with sample question
    sample_q = "Show me all species with molecular formula C6H8O6"
    post_probe: dict[str, dict] = {}
    for path in ["/demos/marie/api/query", "/demos/marie/api/search", "/demos/marie/api/ask"]:
        for payload in [
            {"query": sample_q},
            {"question": sample_q},
            {"text": sample_q},
        ]:
            key = f"POST {path} {list(payload.keys())[0]}"
            code, body = post_json(BASE + path, payload)
            post_probe[key] = {
                "status": code,
                "size": len(body),
                "snippet": body[:500].decode("utf-8", errors="replace"),
            }

    report = {
        "base_url": MARIE,
        "build_id": re.search(r'"buildId":"([^"]+)"', index),
        "routes": route_results,
        "example_question_groups": example_groups,
        "js_chunks": chunks,
        "js_downloaded": downloaded,
        "url_hits": url_hits[:100],
        "api_path_hits": api_hits[:80],
        "env_vars": env_hits,
        "sparql_hits": sparql_hits[:50],
        "api_probe": api_probe,
        "backend_probe": backend_probe,
        "post_probe": post_probe,
    }
    if isinstance(report["build_id"], re.Match):
        report["build_id"] = report["build_id"].group(1)

    out_path = OUT / "probe_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"Routes OK: {sum(1 for r in route_results.values() if r['status'] == 200)}/{len(routes)}")
    print(f"Example question groups: {len(example_groups)}")
    print(f"JS chunks: {len(chunks)} downloaded {len(downloaded)}")
    print("Backend hits:")
    for p, v in backend_probe.items():
        if v["status"] not in (404, "ERR"):
            print(f"  {v['status']} {p}")


if __name__ == "__main__":
    main()
