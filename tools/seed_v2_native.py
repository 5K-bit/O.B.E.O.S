"""Prepare an isolated offline native validation workspace using verified public samples."""
import sys,json,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/"EarthGlass")]
from earthglass.spatial.engine import SpatialEngine
from earthglass.spatial.models import Observation
from earthglass.mobility.static_gtfs import StaticGTFSFeed
from blackfong_ecosystem.earth.control import EarthControl
target=root/"artifacts"/"native-v2-offline"
if target.exists() and any(target.iterdir()):raise SystemExit("Use a fresh native-v2-offline test directory")
target.mkdir(parents=True,exist_ok=True)
engine=SpatialEngine(target/"history");control=EarthControl(engine,target/"scenes.sqlite3")
try:
    count=0
    for name in ("noaa_awc_metar","nyc_dot","mta_lirr"):
        path=root/"artifacts"/"v2-live"/(name+".json")
        if path.exists():
            values=[Observation.model_validate(v) for v in json.loads(path.read_text(encoding="utf-8"))["observations"]]
            engine.history.ingest(values);count+=len(values)
    document=json.loads((root/"artifacts"/"v2-live"/"gtfs-manhattan.json").read_text(encoding="utf-8"))
    engine._entries["local_gtfs"]["feed"].install(document)
    for name in ("nasa_gibs","noaa_swpc"):
        path=root/"artifacts"/"v2-live"/"cache"/(name+"-document.json")
        if path.exists():shutil.copy2(path,target/"history"/"provider-cache"/path.name)
    for index,(command,args) in enumerate([
        ("earth.network.set",{"mode":"offline"}),
        ("earth.layer.enable",{"layer":"local_gtfs"}),
        ("earth.layer.enable",{"layer":"local_gtfs_stops"}),
        ("earth.layer.enable",{"layer":"mta_lirr"}),
        ("earth.layer.enable",{"layer":"nyc_dot"}),
        ("earth.layer.enable",{"layer":"noaa_awc_metar"}),
        ("earth.goto",{"latitude":40.78,"longitude":-73.97,"altitude_m":45000}),
    ]):
        result=control.execute({"schema_version":"1.0","scene_id":"home","request_id":"native-v2-"+str(index),"command":command,"arguments":args})
        assert result["ok"],result
    print(json.dumps({"directory":str(target),"cached_public_samples":count,"gtfs_routes":document["route_count"],"gtfs_stops":document["stop_count"],"offline":True,"private_tracking":False}))
finally:control.close();engine.stop()
