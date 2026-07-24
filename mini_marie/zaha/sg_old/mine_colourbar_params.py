from urllib import request
import re

URL = "https://sg-old.theworldavatar.io/visualisation/DispersionHandler.js"
body = request.urlopen(request.Request(URL, headers={"User-Agent": "curl/8.0"})).read().decode()

# Find colour bar function block
idx = body.find("GetColourBar")
print(body[idx - 600 : idx + 200])

# All param keys near colourBar
for m in re.finditer(r"params\s*=\s*\{([^}]+)\}", body):
    if "derivation" in m.group(1) or "pollutant" in m.group(1) or "time" in m.group(1):
        print("\nPARAMS:", m.group(0)[:400])
