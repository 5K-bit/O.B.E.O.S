"""Prepare one local NYC PMTiles candidate with a bounded workspace and resumable artifacts."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

BBOX = (-74.33, 40.42, -73.63, 40.98)
BUDGET = 25_000_000_000
REQUIRED_ASSETS = {
    "style": "style.json",
    "fonts": "fonts/",
    "sprites": "sprites/",
    "terrain": "terrain/",
}
ROOT = Path(__file__).resolve().parents[1]


def workspace_bytes(root):
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def asset_present(root: Path, relative: str) -> bool:
    path = root / relative
    return path.is_file() if path.suffix else path.is_dir() and any(path.iterdir())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="https://build.protomaps.com/20260913.pmtiles")
    parser.add_argument("--pmtiles", type=Path, default=ROOT / "artifacts/nyc-map/tools/pmtiles.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "data/maps/nyc")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("Preparation must remain in the canonical OBEOS project")
    if args.pmtiles.resolve() == output / "nyc.pmtiles":
        parser.error("The PMTiles preparation tool must not overwrite the output archive")
    output.mkdir(parents=True, exist_ok=True)
    prepared = output / "nyc.pmtiles"
    partial = output / "nyc.partial.pmtiles"
    if prepared.exists() or partial.exists():
        parser.error("Existing candidate preserved; choose a new output directory")
    temporary = output / "preparation"
    temporary.mkdir(exist_ok=True)
    env = dict(os.environ, TMP=str(temporary), TEMP=str(temporary))
    command = [str(args.pmtiles.resolve()), "extract", args.source, str(partial),
               "--bbox=" + ",".join(map(str, BBOX)), "--maxzoom=15", "--download-threads=2"]
    start = time.monotonic()
    with (output / "prepare.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while process.poll() is None:
                used = workspace_bytes(ROOT / "data/maps") + workspace_bytes(ROOT / "artifacts/nyc-map")
                if used > BUDGET - 100_000_000:
                    raise RuntimeError("Map preparation stopped before exhausting the 25 GB budget")
                if time.monotonic() - start > 900:
                    raise RuntimeError("Map preparation exceeded its 15-minute deadline; partial artifact retained")
                time.sleep(1)
            if process.returncode:
                raise RuntimeError("PMTiles extraction failed; see prepare.log")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    subprocess.run([str(args.pmtiles.resolve()), "verify", str(partial)], check=True, timeout=120)
    metadata = subprocess.run([str(args.pmtiles.resolve()), "show", str(partial), "--metadata"], check=True, timeout=30, capture_output=True, text=True).stdout
    header = subprocess.run([str(args.pmtiles.resolve()), "show", str(partial), "--header-json"], check=True, timeout=30, capture_output=True, text=True).stdout
    partial.rename(prepared)
    (output / "metadata.json").write_text(metadata, encoding="utf-8")
    (output / "header.json").write_text(header, encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "status": "tiles_verified_assets_pending",
        "source": args.source,
        "coverage": "Conservative rectangle enclosing all five NYC boroughs and at least a 5 km buffer",
        "bbox": BBOX,
        "maxzoom": 15,
        "budget_bytes": BUDGET,
        "archive_bytes": prepared.stat().st_size,
        "attribution": "Protomaps / OpenStreetMap contributors (ODbL)",
        "capabilities": {
            "vector_tiles": True,
            "building_attributes": True,
            "local_style": asset_present(output, REQUIRED_ASSETS["style"]),
            "local_fonts": asset_present(output, REQUIRED_ASSETS["fonts"]),
            "local_sprites": asset_present(output, REQUIRED_ASSETS["sprites"]),
            "terrain": asset_present(output, REQUIRED_ASSETS["terrain"]),
            "height_data": False,
            "kml_kmz_import": False,
            "usgs_import": False,
        },
        "missing": [
            "local style",
            "local fonts and sprites",
            "bounded USGS terrain sample",
            "matched renderer measurements",
        ],
        "unsupported": [
            "continuous building height data until terrain is bundled",
            "KML/KMZ and USGS import until the selected City architecture is specified",
        ],
        "prepared_seconds": time.monotonic() - start,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
