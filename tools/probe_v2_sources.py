"""Read bounded public metadata/examples to verify V2 source contracts without keys."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json, re, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EarthGlass"))
from earthglass.spatial.network import PublicHTTP
target = Path(__file__).resolve().parents[1] / "artifacts/v2-source-contracts"
target.mkdir(parents=True, exist_ok=True)
sources = {
 "awc-schema": "https://aviationweather.gov/data/schema/openapi.yaml",
 "nasa-gibs": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml",
 "firms-downloads": "https://firms.modaps.eosdis.nasa.gov/active_fire/",
 "nyc-dot": "https://www.nyc.gov/html/dot/html/about/datafeeds.shtml",
 "eonet": "https://eonet.gsfc.nasa.gov/api/v3/events?limit=2&days=30",
 "nws": "https://api.weather.gov/alerts/active?status=actual&limit=2",
 "swpc-kp": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
 "swpc-wind": "https://services.swpc.noaa.gov/products/solar-wind/plasma-2-hour.json",
 "swpc-alerts": "https://services.swpc.noaa.gov/products/alerts.json",
 "mta-feeds": "https://api.mta.info/",
}
def probe(item):
 name,url=item
 http=PublicHTTP(timeout=18)
 try:
  body,kind=http.bytes(url)
  (target/(name+".txt")).write_bytes(body)
  return {"name":name,"url":url,"bytes":len(body),"content_type":kind,"status":"received"}
 except Exception as exc:
  return {"name":name,"url":url,"status":"unavailable","reason":getattr(exc,"code",type(exc).__name__)}
 finally:http.close()
with ThreadPoolExecutor(max_workers=3) as pool:
 result=list(pool.map(probe,sources.items()))
(target/"index.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result,indent=2))
