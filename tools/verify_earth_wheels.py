"""Inspect clean release contents and smoke-test their installed imports and local API."""
import json
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

root=Path(__file__).resolve().parents[1]
target=root/"artifacts"/("wheel-probe-"+uuid.uuid4().hex[:10]);target.mkdir(parents=True)
for name in ("earthglass","blackfong_ecosystem"):
    wheel=next((root/"artifacts/wheels").glob(name+"-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.namelist():
            assert (target/entry).resolve().is_relative_to(target.resolve())
            assert not entry.startswith(("tests/",".venv/","artifacts/")),entry
        archive.extractall(target)
code="""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import earthglass,blackfong_ecosystem,httpx
from blackfong_ecosystem.earth.runtime import EarthRuntime
target=Path(sys.argv[1]).resolve()
assert Path(earthglass.__file__).is_relative_to(target)
assert Path(blackfong_ecosystem.__file__).is_relative_to(target)
assets=Path(earthglass.__file__).parent/"desktop_assets"
for name in ("index.html","presence.html","models/aircraft.gltf","cesium/LICENSE.md","react-LICENSE.txt"):
    assert (assets/name).exists(),name
runtime=EarthRuntime(target/"state",assets,offline=True,isolated=True)
try:
    origin=runtime.start()
    with httpx.Client(base_url=origin,headers={"Authorization":"Bearer "+runtime.token},timeout=10) as client:
        page=client.get("/")
        assert page.status_code==200
        assert "script-src" in page.headers["content-security-policy"]
        providers=client.get("/api/v2/catalog").json()["providers"]
        assert len(providers)>=50
        assert {p["credential"] for p in providers if p["credential"]} == {"firms_map_key","ny511_api_key","mta_bustime_api_key"}
        assert all(not p["enabled"] for p in providers if p["credential"])
        assert client.get("/api/v2/imagery/catalog").status_code==200
        assert client.get("/api/v2/mobility/providers").status_code==200
        assert client.get("/private/v2/presence").json()["enabled"] is False
        assert "arcgisonline" not in page.headers["content-security-policy"]
        response=client.post("/api/control",json={"schema_version":"1.0","scene_id":"home","request_id":"wheel-probe","command":"earth.layers.list","arguments":{}})
        assert response.json()["ok"]
    from earthglass.spatial.places import search_places
    assert search_places("Seattle")[0]["label"].startswith("Seattle")
    result={"status":"passed","provider_count":len(providers),"startup_seconds":runtime.started_seconds,"assets_bundled":True,"offline_city_search":True,"optional_free_keys_only":True,"offline_remote_assets_blocked":True}
finally:runtime.stop()
assert not runtime.shutdown_errors,runtime.shutdown_errors
print(json.dumps(result))
"""
completed=subprocess.run([sys.executable,"-I","-c",code,str(target)],cwd=target,capture_output=True,text=True,timeout=60)
if completed.returncode:raise SystemExit(completed.stdout+completed.stderr)
result=json.loads(completed.stdout.strip().splitlines()[-1])
(root/"docs/validation/wheel-smoke.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
print(json.dumps(result,indent=2))
