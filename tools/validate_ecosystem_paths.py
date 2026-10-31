"""Validate canonical repository pins and manifest paths without launching services."""
from __future__ import annotations
import argparse
import configparser
from pathlib import Path
import subprocess
import tomllib
ROOT=Path(__file__).resolve().parents[1]
def validate(root=ROOT, *, allow_uninitialized=False):
    root=Path(root).resolve()
    manifest=tomllib.loads((root/"obeos_ecosystem.toml").read_text(encoding="utf-8"))
    modules=configparser.ConfigParser();modules.read(root/".gitmodules",encoding="utf-8")
    canonical={modules[section]["path"] for section in modules.sections()}
    errors=[]
    for project in manifest["projects"]:
        relative=project["path"];path=(root/relative).resolve()
        if relative not in canonical:errors.append(f"{project['id']}: not a canonical pinned repository")
        if not path.is_relative_to(root):errors.append(f"{project['id']}: path escapes workspace")
        if not (path/".git").exists():
            if not allow_uninitialized:errors.append(f"{project['id']}: submodule is not initialized")
            continue
        for command in project.get("commands",[]):
            cwd=(root/command.get("cwd",relative)).resolve()
            if not cwd.is_relative_to(path) or not cwd.is_dir():errors.append(f"{project['id']}: invalid command directory")
        for check in project.get("health_checks",[]):
            if check["type"]=="path_exists" and not check.get("optional") and not (root/check["path"]).exists():
                errors.append(f"{project['id']}: missing {check['path']}")
    return errors
if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--allow-uninitialized",action="store_true")
    args=parser.parse_args();errors=validate(allow_uninitialized=args.allow_uninitialized)
    print("\n".join(errors) if errors else "Canonical repository paths and initialized component checks passed.")
    raise SystemExit(bool(errors))
