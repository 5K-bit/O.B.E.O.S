"""Build release wheels from fresh staging trees, excluding runtime state and stale build outputs."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

root = Path(__file__).resolve().parents[1]
stage = root / "artifacts" / ("release-staging-" + uuid.uuid4().hex[:10])
stage.mkdir(parents=True)
assert stage.resolve().is_relative_to((root / "artifacts").resolve())
output = root / "artifacts/wheels"
output.mkdir(exist_ok=True)
ignore = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", "*.sqlite3", "*.log"
)
for name in ("earthglass", "obeos"):
    target = stage / name
    target.mkdir()
    source = root / "EarthGlass" if name == "earthglass" else root
    package = "earthglass" if name == "earthglass" else "blackfong_ecosystem"
    shutil.copytree(source / package, target / package, ignore=ignore)
    for filename in ("pyproject.toml", "README.md", "LICENSE", "LICENSE.md"):
        if (source / filename).is_file():
            shutil.copy2(source / filename, target / filename)
    environment = os.environ.copy()
    if name == "earthglass":
        shutil.copy2(source / "hatch_build.py", target / "hatch_build.py")
        shutil.copytree(source / "obeos-earth/dist", target / "obeos-earth/dist")
        environment["EARTHGLASS_BUNDLE_DESKTOP"] = "1"
    else:
        shutil.copy2(root / "desktop_shell.py", target / "desktop_shell.py")
    log = root / "artifacts" / f"build-{name}.log"
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(output),
                str(target),
            ],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=environment,
            timeout=180,
        )
    if result.returncode:
        raise SystemExit(f"Build failed: {log}")
    print(f"Built {name}; log: {log}")
