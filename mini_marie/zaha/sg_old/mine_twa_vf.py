from urllib import request

url = "https://sg-old.theworldavatar.io/visualisation/lib/twa-vf.min.js"
body = request.urlopen(request.Request(url, headers={"User-Agent": "curl/8.0"})).read().decode("utf-8", errors="replace")

for token in ["feature-info-agent/get", "/timeseries", "timeseriesHandler", "queryData", "getData"]:
    i = 0
    n = 0
    while n < 5:
        i = body.find(token, i)
        if i < 0:
            break
        print(f"\n--- {token} @ {i} ---")
        print(body[max(0, i - 150) : i + 350])
        i += len(token)
        n += 1
