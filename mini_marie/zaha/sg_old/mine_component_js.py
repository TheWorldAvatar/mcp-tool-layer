"""Mine component.min.js for timeseries + feature-info URL patterns."""
from __future__ import annotations

import re
from urllib import request

for rel in ["component/component.min.js", "lib/twa-vf.min.js"]:
    url = f"https://sg-old.theworldavatar.io/visualisation/{rel}"
    try:
        body = request.urlopen(request.Request(url, headers={"User-Agent": "curl/8.0"}), timeout=30).read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"\n{rel}: FAIL {exc}")
        continue
    print(f"\n=== {rel} ({len(body)} chars) ===")
    for pat in [
        r'feature-info[^"\']{0,80}',
        r'/[a-zA-Z0-9_./-]*(?:timeseries|Timeseries|feature-info)[a-zA-Z0-9_./-]*',
        r'parseData[^;]{0,200}',
        r'queryData[^;]{0,200}',
        r'hasRDB[^;]{0,120}',
    ]:
        hits = sorted(set(re.findall(pat, body, re.I)))[:15]
        if hits:
            print(f"  {pat}: {hits[:8]}")
