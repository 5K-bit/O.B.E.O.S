"""Record tested dependency constraints without credentials or unrelated package sources.

The optional weather stack (xarray/cfgrib/ecCodes) is emitted to its own weather.in
so the desktop lock stays lean; only the weather CI job installs it."""
import importlib.metadata as metadata
import tomllib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
requirements=[];weather=[]
for path in [root/"pyproject.toml",root/"EarthGlass/pyproject.toml"]:
    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    requirements.extend(project["dependencies"])
    weather.extend(project.get("optional-dependencies", {}).get("weather", []))
requirements.extend(["build==1.4.2","hatchling==1.32.0","ruff==0.16.7","uv==0.12.12","wheel","setuptools","editables"])
directory=root/"requirements";directory.mkdir(exist_ok=True)
(directory/"desktop.in").write_text("# Python 3.11 desktop runtime and validation tools; local packages installed separately.\n"+"\n".join(dict.fromkeys(requirements))+"\n",encoding="utf-8")
(directory/"weather.in").write_text("# Optional GFS weather decoding stack; install alongside the desktop lock.\n"+"\n".join(dict.fromkeys(weather))+"\n",encoding="utf-8")
constraints=root/"artifacts"/"tested-constraints.txt";constraints.parent.mkdir(exist_ok=True)
constraints.write_text("\n".join(sorted({dist.metadata["Name"]+"=="+dist.version for dist in metadata.distributions() if dist.metadata["Name"] and dist.metadata["Name"].lower() not in {"blackfong-ecosystem","blackfong_ecosystem","earthglass"}}))+"\n",encoding="utf-8")
print("Wrote desktop.in, weather.in and local tested-version constraints.")
