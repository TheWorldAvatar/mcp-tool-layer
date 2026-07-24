import json
from urllib import parse, request

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"

sim = json.loads(request.urlopen(request.Request(HOST + "/dispersion-interactor/GetDispersionSimulations", headers={"User-Agent": UA})).read())
meta = sim["Singapore"]
ts = str(meta["time"][0])
z = meta["z"]["0"]
deriv = meta["derivationIri"]
co_iri = "https://www.theworldavatar.com/kg/ontodispersion/CO"

attempts = [
    {"lat": 1.33, "lng": 103.74, "pollutantIri": co_iri, "timestep": ts, "derivationIri": deriv, "zIri": z},
    {"lat": 1.33, "lng": 103.74, "pollutant": co_iri, "timestep": ts, "derivationIri": deriv, "zIri": z},
    {"x": 103.74, "y": 1.33, "pollutant": "CO", "timestep": ts, "derivationIri": deriv, "zIri": z},
]
base = HOST + "/dispersion-interactor/GetPollutantConcentrations?"
for i, p in enumerate(attempts):
    url = base + parse.urlencode({k: str(v) for k, v in p.items()})
    try:
        r = request.urlopen(request.Request(url, headers={"User-Agent": UA}), timeout=20)
        print(i, r.status, r.read(500)[:200])
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        print(i, e, body[:300] if body else "")
