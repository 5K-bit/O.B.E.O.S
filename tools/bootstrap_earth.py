"""Create a pinned source-checkout desktop environment without starting any services."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def run(arguments, cwd):
    subprocess.run([str(arg) for arg in arguments], cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-client", action="store_true", help="Use an already built client"
    )
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Use Python 3.11 for the supplied locks.")
    root = Path(__file__).resolve().parents[1]
    if not (root / "EarthGlass/earthglass").is_dir():
        raise SystemExit(
            "Initialize the canonical EarthGlass submodule before bootstrapping."
        )
    platform = (
        "windows"
        if os.name == "nt"
        else "linux"
        if sys.platform.startswith("linux")
        else None
    )
    if platform is None:
        raise SystemExit("Supplied desktop locks support Windows x64 and Linux x64.")
    folder = root / ".venv-earth"
    if not folder.exists():
        venv.EnvBuilder(with_pip=True).create(folder)
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "-r",
            root / f"requirements/desktop-{platform}-py311.lock",
        ],
        root,
    )
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            root / "EarthGlass",
            "-e",
            root,
        ],
        root,
    )
    if not args.skip_client:
        pnpm = shutil.which("pnpm")
        if not pnpm:
            raise SystemExit(
                "Install Node.js 24 and pnpm 11.19.0, then rerun bootstrap."
            )
        run([pnpm, "install", "--frozen-lockfile"], root / "EarthGlass/obeos-earth")
        run([pnpm, "run", "build"], root / "EarthGlass/obeos-earth")
    if not (root / "EarthGlass/obeos-earth/dist/index.html").exists():
        raise SystemExit("Earth client is not built.")
    run([python, root / "tools/validate_ecosystem_paths.py"], root)
    print("Ready. Launch with: python launch_obeos.py")


if __name__ == "__main__":
    main()
