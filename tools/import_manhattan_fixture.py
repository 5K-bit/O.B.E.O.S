import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"EarthGlass"))
from earthglass.spatial.network import PublicHTTP
from earthglass.mobility.static_gtfs import import_gtfs
root=Path(__file__).resolve().parents[1]/"artifacts"/"v2-live"
http=PublicHTTP(max_bytes=32*1024*1024,timeout=30)
try:
    body,_=http.bytes("https://rrgtfsfeeds.s3.amazonaws.com/gtfs_m.zip",max_bytes=32*1024*1024)
    document=import_gtfs(body,agency="mta_manhattan",source_url="https://www.mta.info/developers",attribution="Data obtained from MTA; stored locally by OBEOS")
    (root/"gtfs-manhattan.zip").write_bytes(body);(root/"gtfs-manhattan.json").write_text(json.dumps(document),encoding="utf-8")
    print(json.dumps({"bytes":len(body),"routes":document["route_count"],"stops":document["stop_count"],"geographic_objects":len(document["observations"])}))
finally:http.close()
