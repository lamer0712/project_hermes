import urllib.request as r
import json
req = r.Request('https://api.upbit.com/v1/market/all?isDetails=true', headers={'accept': 'application/json'} )
res = json.loads(r.urlopen(req).read() )
print([m for m in res if m['market'] == 'KRW-VTHO'])
