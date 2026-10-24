"""Temporary local review host for the bundled Earth UI; isolated data only."""
import sys, time, json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "EarthGlass"))
sys.path.insert(0, str(root))
from blackfong_ecosystem.earth.runtime import EarthRuntime
runtime = EarthRuntime(root / "artifacts" / "native-v2-offline", root / "EarthGlass" / "obeos-earth" / "dist", offline=True, isolated=True)
stop = root / "artifacts" / "v2-review-stop"
try:
    runtime.start()
    previous_app = runtime.application.middleware_stack
    async def traced(scope, receive, send):
        if scope["type"] != "http":
            return await previous_app(scope, receive, send)
        path = scope["path"]
        started = time.monotonic()
        status, size = 0, 0
        async def traced_send(message):
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)
        try:
            await previous_app(scope, receive, traced_send)
        finally:
            with (root / "artifacts" / "v2-request-trace.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps({"path":path.split("?")[0], "method":scope["method"],"status":status, "bytes":size,"seconds":round(time.monotonic()-started,3)})+"\n")
    runtime.application.middleware_stack = traced
    print(json.dumps({"url":runtime.browser_url()}), flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline and not stop.exists():
        time.sleep(1)
        if int(time.monotonic()) % 5 == 0:
            import httpx
            with httpx.Client(headers={'Authorization':'Bearer '+runtime.token}, timeout=20) as client:
                response=client.get(runtime.origin+'/api/v2/observations',params={'limit':2000, **runtime.engine.viewport.model_dump()})
                data=response.json()
                (root/'artifacts/v2-browser-query.json').write_text(json.dumps({'status':response.status_code,'viewport':runtime.engine.viewport.model_dump(),'count':len(data.get('observations',[])),'error':data.get('detail')},indent=2),encoding='utf-8')
finally:
    runtime.stop()
