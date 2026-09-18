import sys,importlib.metadata as m
print(sys.executable)
for name in ("earthglass","pydantic","fastapi","protobuf","gtfs-realtime-bindings","tzdata","uv","ruff"):
    try:print(name,m.version(name))
    except m.PackageNotFoundError:print(name,"missing")
