from pathlib import Path
import sys,json
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"EarthGlass"))
from earthglass.spatial.feeds.documents import ImageryFeed
root=Path(__file__).resolve().parents[1]
class HTTP:
 def text(self,*a,**k):return (root/"artifacts/v2-source-contracts/nasa-gibs.txt").read_text(encoding="utf-8")
feed=ImageryFeed(HTTP())
feed.fetch(None,datetime.now(timezone.utc))
target=root/"EarthGlass/earthglass/spatial/data/imagery-catalog.json"
target.write_text(json.dumps(feed.document(),indent=2),encoding="utf-8")
print(json.dumps([{k:p.get(k) for k in ("id","available","source_layer")} for p in feed.document()["data"]]))
