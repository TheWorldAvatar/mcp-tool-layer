"""Download chemistry T-Box files from TheWorldAvatar/ontology (Marie ontology-info sources)."""

from __future__ import annotations

import json
import ssl
import urllib.request
from pathlib import Path

UA = "curl/8.0"
CTX = ssl.create_default_context()
RAW = "https://raw.githubusercontent.com/TheWorldAvatar/ontology/main/ontology"
OUT = Path(__file__).resolve().parents[1] / "docs" / "resources" / "tbox"

# namespace_key -> list of (remote_path, local_filename)
FILES: dict[str, list[tuple[str, str]]] = {
    "ontospecies": [
        ("ontospecies/OntoSpecies_v2.owl", "OntoSpecies_v2.owl"),
        ("ontospecies/OntoSpecies.owl", "OntoSpecies.owl"),
    ],
    "ontokin": [("ontokin/OntoKin.owl", "OntoKin.owl")],
    "ontocompchem": [("ontocompchem/ontocompchem.owl", "ontocompchem.owl")],
    "ontozeolite": [
        ("ontozeolite/ontozeolite.owl", "ontozeolite.owl"),
        ("ontozeolite/ontocrystal.owl", "ontocrystal.owl"),
    ],
    "ontomops": [("ontomops/ontomops-ogm.ttl", "ontomops-ogm.ttl")],
    "ontoprovenance": [("ontoprovenance/OntoProvenance.owl", "OntoProvenance.owl")],
    "ontopesscan": [("ontopesscan/OntoPESScan.owl", "OntoPESScan.owl")],
}


def fetch(remote: str) -> tuple[bool, int, str]:
    url = f"{RAW}/{remote}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
            body = r.read()
            return True, len(body), body.decode("utf-8", errors="replace")
    except Exception as exc:
        return False, 0, str(exc)[:200]


def main() -> None:
    report: dict = {"source_repo": "https://github.com/TheWorldAvatar/ontology", "namespaces": {}}
    for ns, items in FILES.items():
        ns_dir = OUT / ns
        ns_dir.mkdir(parents=True, exist_ok=True)
        report["namespaces"][ns] = []
        for remote, local in items:
            ok, size, content = fetch(remote)
            entry = {"remote": remote, "local": f"{ns}/{local}", "ok": ok, "bytes": size}
            if ok:
                (ns_dir / local).write_text(content, encoding="utf-8")
                print(f"OK  {ns}/{local} ({size:,} bytes)")
            else:
                entry["error"] = content
                print(f"FAIL {ns}/{local}: {content}")
            report["namespaces"][ns].append(entry)

    (OUT / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {OUT / 'manifest.json'}")


if __name__ == "__main__":
    main()
