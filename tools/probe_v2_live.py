"""Record bounded live provider verification separately from deterministic fixtures."""
import sys,json,concurrent.futures
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"EarthGlass"))
from earthglass.spatial.network import PublicHTTP
from earthglass.spatial.models import Viewport
from earthglass.spatial.feeds.documents import SpaceWeatherFeed
from earthglass.spatial.feeds.planetary import NWSAlertFeed,AviationWeatherFeed,EonetFeed
from earthglass.spatial.feeds.orbits import OrbitalFeed
from earthglass.mobility.traffic import NYCDOTFeed
from earthglass.mobility.transit import GTFSRealtimeFeed
from earthglass.mobility.static_gtfs import import_gtfs
root=Path(__file__).resolve().parents[1]/"artifacts"/"v2-live";root.mkdir(parents=True,exist_ok=True)
http=PublicHTTP()
feeds=[SpaceWeatherFeed(http),NWSAlertFeed(http),AviationWeatherFeed(http),AviationWeatherFeed(http,product="sigmet"),EonetFeed(http),OrbitalFeed(http),NYCDOTFeed(http),GTFSRealtimeFeed(http)]
viewport=Viewport(west=-75,south=40,east=-73,north=41)
now=datetime.now(timezone.utc)
def probe(feed):
    feed.configure_cache(root/"cache")
    try:
        observations=feed.fetch(Viewport(west=-180,south=-90,east=180,north=90) if isinstance(feed,OrbitalFeed) else viewport,now)
        data=feed.document() if hasattr(feed,"document") else {}
        (root/(feed.spec.id+".json")).write_text(json.dumps({"observations":[o.wire() for o in observations[:100]],"document":data},indent=2),encoding="utf-8")
        return {"provider":feed.spec.id,"observations":len(observations),"rejected":feed.rejected,"document_available":bool(data),"result":"received"}
    except Exception as exc:return {"provider":feed.spec.id,"result":"unavailable","error":type(exc).__name__,"code":getattr(exc,"code",None)}
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(probe,feeds))
    try:
        body=(root/"gtfs-manhattan.zip").read_bytes() if (root/"gtfs-manhattan.zip").exists() else http.bytes("https://rrgtfsfeeds.s3.amazonaws.com/gtfs_m.zip",max_bytes=32*1024*1024)[0]
        imported=import_gtfs(body,agency="mta_manhattan",source_url="https://www.mta.info/developers",attribution="Data obtained from MTA; stored locally by OBEOS")
        (root/"gtfs-manhattan.zip").write_bytes(body)
        (root/"gtfs-manhattan.json").write_text(json.dumps(imported),encoding="utf-8")
        results.append({"provider":"gtfs_manhattan","result":"imported_official_archive","bytes":len(body),"routes":imported["route_count"],"stops":imported["stop_count"]})
    except Exception as exc:results.append({"provider":"gtfs_manhattan","result":"unavailable","error":type(exc).__name__,"code":getattr(exc,"code",None),"message":str(exc) if isinstance(exc,ValueError) else None})
finally:http.close()
report={"at":now.isoformat(),"viewport":viewport.model_dump(),"results":results}
(root/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report,indent=2))
