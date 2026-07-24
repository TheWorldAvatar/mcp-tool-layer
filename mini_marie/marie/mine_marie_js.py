"""Mine cached Marie demo assets for API hints."""
from __future__ import annotations

import json
import re
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root

OUT = mini_marie_cache_root() / "marie_demo"


def main() -> None:
    text = ""
    for p in sorted(OUT.rglob("*")):
        if p.suffix in {".js", ".html"}:
            text += p.read_text(encoding="utf-8", errors="replace") + "\n"

    patterns = {
        "urls": r"https?://[^\s\"\\]+",
        "marie_paths": r"/demos/marie/[a-zA-Z0-9_./-]+",
        "env": r"NEXT_PUBLIC_[A-Z0-9_]+",
        "sparql": r"[a-zA-Z0-9._/-]*sparql[a-zA-Z0-9._/-]*",
        "chemistry": r"/chemistry/[a-zA-Z0-9_./-]+",
    }
    report = {k: sorted(set(re.findall(pat, text, re.I))) for k, pat in patterns.items()}
    out = OUT / "js_mine_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for k, v in report.items():
        print(f"{k}: {len(v)}")
        for h in v[:20]:
            print(f"  {h[:140]}")


if __name__ == "__main__":
    main()
