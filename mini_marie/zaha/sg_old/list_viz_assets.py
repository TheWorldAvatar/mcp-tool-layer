from urllib import request
import re
u = "https://sg-old.theworldavatar.io/visualisation/"
b = request.urlopen(request.Request(u, headers={"User-Agent": "curl/8.0"})).read().decode()
for s in sorted(set(re.findall(r'src="([^"]+)"', b))):
    print(s)
