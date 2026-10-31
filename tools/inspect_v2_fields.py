from pathlib import Path
import xml.etree.ElementTree as ET,json,yaml
p=Path(__file__).resolve().parents[1]/"artifacts/v2-source-contracts"
ns={"w":"http://www.opengis.net/wmts/1.0","o":"http://www.opengis.net/ows/1.1"}
root=ET.fromstring((p/"nasa-gibs.txt").read_bytes())
ids=[e.findtext("o:Identifier",namespaces=ns) for e in root.findall(".//w:Layer",ns)]
print("SNOW/SEA",json.dumps([i for i in ids if any(k in i for k in ("Snow_Cover","Sea_Surface_Temp"))][:60]))
doc=yaml.safe_load((p/"awc-schema.txt").read_text())
for name in ("METARGeoJSON","TAFGeoJSON","PirepGeoJSON","ISigmetGeoJSON","GairmetGeoJSON"):
 print(name,json.dumps(doc["components"]["schemas"].get(name))[:7000])
