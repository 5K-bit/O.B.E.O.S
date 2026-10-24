from pathlib import Path
import json, re, xml.etree.ElementTree as ET, yaml
p=Path(__file__).resolve().parents[1]/"artifacts/v2-source-contracts"
ns={"w":"http://www.opengis.net/wmts/1.0","o":"http://www.opengis.net/ows/1.1"}
root=ET.fromstring((p/"nasa-gibs.txt").read_bytes())
wanted=("GOES-East_ABI_GeoColor","GOES-East_ABI_Band13","GOES-East_ABI_Band8","VIIRS_SNPP_CorrectedReflectance_TrueColor","MODIS_Terra_Aerosol","MODIS_Terra_Snow_Cover","MODIS_Terra_NDVI","MODIS_Aqua_Sea_Surface_Temp")
selected=[]
for layer in root.findall(".//w:Layer",ns):
 ident=layer.findtext("o:Identifier",namespaces=ns)
 if any(ident.startswith(w) for w in wanted):
  selected.append({"id":ident,"format":layer.findtext("w:Format",namespaces=ns),"matrix":layer.findtext("w:TileMatrixSetLink/w:TileMatrixSet",namespaces=ns),"resource":layer.find("w:ResourceURL",ns).attrib if layer.find("w:ResourceURL",ns) is not None else {},"time":[e.text for e in layer.findall("w:Dimension/w:Value",ns)],"default":layer.findtext("w:Dimension/w:Default",namespaces=ns)})
print("GIBS",json.dumps(selected))
schema=yaml.safe_load((p/"awc-schema.txt").read_text())
print("AWC",json.dumps({k:v for k,v in schema["paths"].items() if any(x in k for x in ("metar","taf","pirep","airsigmet","gairmet","isigmet"))})[:18000])
print("DOT LINKS",json.dumps([x for x in re.findall(r'href=["\']([^"\']+)',(p/"nyc-dot.txt").read_text()) if any(k in x.lower() for k in ("speed","nyctmc","data.city","resource"))]))
for name in ("eonet","swpc-kp","swpc-alerts","firms-downloads","mta-feeds"):
 print(name,(p/(name+".txt")).read_text()[:1500])
