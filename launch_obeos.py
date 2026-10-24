"""Canonical source-checkout launcher. Run bootstrap_earth.py once before using it."""
from pathlib import Path
import os
import subprocess
import sys

def main():
    root=Path(__file__).resolve().parent
    relative=Path("Scripts/python.exe") if os.name=="nt" else Path("bin/python")
    candidates=[root/".venv-earth"/relative,root/".venv"/relative]
    python=next((path for path in candidates if path.exists()),None)
    if python is None:
        raise SystemExit("Run: python tools/bootstrap_earth.py")
    return subprocess.call([str(python),"-m","blackfong_ecosystem.earth.desktop",*sys.argv[1:]],cwd=root)
if __name__=="__main__":raise SystemExit(main())
