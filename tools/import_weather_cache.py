"""One-time import of original weather products; never overwrite existing products."""
import argparse
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    from earthglass.weather.models.metadata import WeatherLayerMetadata

    source = args.source.resolve()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for metadata_file in sorted(source.glob("*.json")):
        try:
            metadata = WeatherLayerMetadata.model_validate_json(metadata_file.read_text(encoding="utf-8"))
        except ValueError:
            continue
        filenames = [Path(value).name for value in metadata.assets.model_dump().values() if value]
        if any(not (source / name).is_file() for name in filenames):
            continue
        if any((destination / name).exists() for name in [*filenames, metadata_file.name]):
            continue
        for name in [*filenames, metadata_file.name]:
            with (source / name).open("rb") as reader, (destination / name).open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            copied.append(name)
    print(json.dumps({"copied": copied, "destination": str(destination)}, indent=2))


if __name__ == "__main__":
    main()
