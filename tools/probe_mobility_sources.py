"""Read-only bounded probes; no keys or private-device information."""
import concurrent.futures,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"EarthGlass"))
from earthglass.spatial.network import PublicHTTP
root=Path(__file__).resolve().parents[1]/"artifacts"/"mobility-source-contracts";root.mkdir(parents=True,exist_ok=True)
sources={
"nyc-speeds":"https://linkdata.nyctmc.org/data/LinkSpeedQuery.txt",
"mta-js":"https://api.mta.info/static/js/main.bc5d42b5.js",
"bustime-doc":"https://bustime.mta.info/wiki/Developers/SIRIVehicleMonitoring",
"nws":"https://api.weather.gov/alerts?active=true",
"swpc-wind":"https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json",
"awc-metar":"https://aviationweather.gov/api/data/metar?format=geojson&bbox=40,-75,41,-73",
"awc-sigmet":"https://aviationweather.gov/api/data/airsigmet?format=geojson",
}
def fetch(item):
    name,url=item
    try:
        with PublicHTTP() as http:body,kind=http.bytes(url,max_bytes=8*1024*1024)
        (root/(name+".txt")).write_bytes(body)
        return {"source":name,"url":url,"bytes":len(body),"type":kind,"result":"received"}
    except Exception as exc:return {"source":name,"url":url,"result":"unavailable","error":type(exc).__name__}
# PublicHTTP has explicit close ownership.
def fetch(item):
    name,url=item;http=PublicHTTP()
    try:
        body,kind=http.bytes(url,max_bytes=8*1024*1024);(root/(name+".txt")).write_bytes(body)
        return {"source":name,"url":url,"bytes":len(body),"type":kind,"result":"received"}
    except Exception as exc:return {"source":name,"url":url,"result":"unavailable","error":type(exc).__name__}
    finally:http.close()
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(fetch,sources.items()))
(root/"index.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
print(json.dumps(results,indent=2))
