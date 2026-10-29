"""Seed an explicitly simulated, isolated desktop acceptance dataset. No providers are contacted."""
import argparse
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/"EarthGlass"),str(root/"EarthGlass/tools")]
from benchmark_spatial import tracks
from earthglass.spatial.engine import SpatialEngine
from blackfong_ecosystem.earth.control import EarthControl

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("directory",type=Path)
    args=parser.parse_args()
    directory=args.directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise SystemExit("Use a new empty directory for the simulated probe.")
    engine=SpatialEngine(directory/"history")
    control=EarthControl(engine,directory/"scenes.sqlite3")
    try:
        values=tracks()
        for offset in range(0,len(values),1000):engine.history.ingest(values[offset:offset+1000])
        for index,(name,arguments) in enumerate([
            ("earth.layer.enable",{"layer":"aircraft"}),
            ("earth.goto",{"latitude":40.7,"longitude":-74,"altitude_m":400000}),
            ("earth.entity.inspect",{"entity_id":"simulated:05050"}),
            ("earth.camera.orbit",{"entity_id":"simulated:05050","distance_m":150000})]):
            result=control.execute({"schema_version":"1.0","scene_id":"home","request_id":f"fixture-{index}","command":name,"arguments":arguments})
            assert result["ok"],result
        print(f"Seeded {len(values)} simulated tracks in {directory}")
    finally:
        control.close();engine.stop()
if __name__=="__main__":main()
