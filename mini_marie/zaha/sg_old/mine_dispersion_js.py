"""One-off: mine DispersionHandler.js for API parameter names."""
from __future__ import annotations

import re
from urllib import request

URL = "https://sg-old.theworldavatar.io/visualisation/DispersionHandler.js"
req = request.Request(URL, headers={"User-Agent": "curl/8.0"})
body = request.urlopen(req, timeout=40).read().decode("utf-8", errors="replace")

for fn in [
    "createVirtualSensor",
    "updateVirtualSensors",
    "GetColourBar",
    "GetPollutantConcentrations",
    "feature-info",
    "featureInfo",
    "timeseries",
]:
    print(f"\n=== {fn} ===")
    for m in re.finditer(re.escape(fn), body, re.I):
        print(body[max(0, m.start() - 120) : m.start() + 500])
        print("---")
