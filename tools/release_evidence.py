#!/usr/bin/env python3
"""Generate reproducible release evidence for O.B.E.O.S.

Records the exact commit and pinned component commits, the environment, the
automated test results, and a dated list of manual checks, then writes them
to release-evidence/<short-sha>-<UTC timestamp>/RELEASE-EVIDENCE.md.

    python tools/release_evidence.py                  # current checkout (records whether it is dirty)
    python tools/release_evidence.py --fresh          # clean clone + new venv + pip install, then test
    python tools/release_evidence.py --markers quick,integration
    python tools/release_evidence.py --fresh --ref <sha> --no-install   # skip venv/pip (uses this Python)

Only the standard library is used. The exit code is pytest's exit code, so CI and
scripts can gate on it. A passing run never means "release ready": the manual
checks in docs/release-manual-checks.json are reported separately.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL_CHECKS = Path("docs") / "release-manual-checks.json"


def run(cmd, cwd=None, timeout=None, capture=True):
    """Run a command; never raise on a non-zero exit."""
    try:
        done = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, text=True, errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
        return done.returncode, (done.stdout or "")
    except FileNotFoundError as exc:
        return 127, f"command not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {' '.join(map(str, cmd))}"


def git(args, cwd):
    code, out = run(["git", *args], cwd=cwd)
    return out.strip() if code == 0 else ""


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def submodule_pins(root: Path):
    """Pinned component commits, read from the tree (works without initializing submodules)."""
    pins = []
    for line in git(["ls-tree", "HEAD"], root).splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "commit":
            sub = root / path
            initialized = (sub / ".git").exists()
            actual = git(["rev-parse", "HEAD"], sub) if initialized else ""
            pins.append({
                "path": path, "pinned": parts[2], "initialized": initialized,
                "checked_out": actual, "matches_pin": bool(actual) and actual == parts[2],
            })
    return pins


def parse_junit(path: Path):
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0, "failed_ids": []}
    if not path.exists():
        return summary
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            summary[key] += int(suite.get(key, 0))
        summary["time"] += float(suite.get("time", 0))
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            summary["failed_ids"].append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return summary


def load_manual_checks(root: Path):
    path = root / MANUAL_CHECKS
    if not path.exists():
        return [], f"{MANUAL_CHECKS} not found"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("checks", []), ""
    except (ValueError, OSError) as exc:
        return [], f"{MANUAL_CHECKS} unreadable: {exc}"


def prepare_fresh(args, workdir: Path):
    """Clone into workdir, init submodules, optionally build a venv. Returns (root, python, notes)."""
    notes = []
    repo = args.repo or git(["remote", "get-url", "origin"], REPO_ROOT)
    if not repo:
        sys.exit("no origin remote; pass --repo <url-or-path>")
    ref = args.ref or git(["rev-parse", "HEAD"], REPO_ROOT)
    root = workdir / "checkout"
    code, out = run(["git", "clone", repo, str(root)], timeout=1800)
    if code != 0:
        sys.exit(f"clone failed:\n{out}")
    code, out = run(["git", "checkout", "--quiet", ref], cwd=root)
    if code != 0:
        sys.exit(f"ref {ref} not found in {repo} (is it pushed?):\n{out}")
    code, out = run(["git", "submodule", "update", "--init", "--recursive"], cwd=root, timeout=1800)
    notes.append(f"submodule init exit code {code}" + ("" if code == 0 else " (see submodule table below)"))
    if args.no_install:
        notes.append("--no-install: used the invoking Python and its existing packages, not a new venv")
        return root, Path(sys.executable), notes, ""
    venv = workdir / "venv"
    code, out = run([sys.executable, "-m", "venv", str(venv)])
    if code != 0:
        sys.exit(f"venv creation failed:\n{out}")
    py = venv_python(venv)
    code, out = run([str(py), "-m", "pip", "install", "-e", str(root)], timeout=3600)
    install_log = out
    notes.append(f"pip install -e . exit code {code}")
    return root, py, notes, install_log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh", action="store_true", help="test a clean clone in a temp dir with a new venv")
    ap.add_argument("--repo", help="clone source for --fresh (default: this checkout's origin)")
    ap.add_argument("--ref", help="commit to test with --fresh (default: this checkout's HEAD; must be pushed)")
    ap.add_argument("--no-install", action="store_true", help="with --fresh: skip venv and pip install")
    ap.add_argument("--markers", default="quick", help="pytest marker expression (default: quick)")
    ap.add_argument("--python", help="Python for the in-place run (default: this interpreter)")
    ap.add_argument("--out", help="output directory (default: release-evidence/<sha>-<timestamp>)")
    ap.add_argument("--timeout", type=int, default=3600, help="pytest timeout in seconds")
    args = ap.parse_args()

    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    tmp = None
    notes, install_log = [], ""
    if args.fresh:
        tmp = Path(tempfile.mkdtemp(prefix="obeos-evidence-"))
        root, py, notes, install_log = prepare_fresh(args, tmp)
        mode = "fresh clone"
    else:
        root, py, mode = REPO_ROOT, Path(args.python or sys.executable), "current checkout"

    sha = git(["rev-parse", "HEAD"], root)
    short = sha[:10] or "unknown"
    dirty = git(["status", "--porcelain"], root)
    out_dir = Path(args.out) if args.out else REPO_ROOT / "release-evidence" / f"{short}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if install_log:
        (out_dir / "pip-install.log").write_text(install_log, encoding="utf-8")

    _, pyver = run([str(py), "--version"])
    _, pytest_ver = run([str(py), "-m", "pytest", "--version"])
    _, freeze = run([str(py), "-m", "pip", "freeze"])
    (out_dir / "pip-freeze.txt").write_text(freeze, encoding="utf-8")

    junit = out_dir / "junit.xml"
    cmd = [str(py), "-m", "pytest", "tests/", "-m", args.markers, "-v", f"--junitxml={junit}"]
    t0 = time.monotonic()
    code, output = run(cmd, cwd=root, timeout=args.timeout)
    duration = time.monotonic() - t0
    (out_dir / "pytest-run.log").write_text(f"{output}\nEXIT_CODE={code}\n", encoding="utf-8")
    results = parse_junit(junit)
    pins = submodule_pins(root)
    checks, checks_note = load_manual_checks(root)
    pending = [c for c in checks if c.get("status") != "done"]

    if code == 0 and results["tests"] > 0:
        automated = "passed"
    elif results["tests"] == 0:
        automated = "no tests ran (collection or setup error; see pytest-run.log)"
    else:
        automated = "failed"

    data = {
        "generated_utc": started.isoformat(timespec="seconds"), "mode": mode, "commit": sha,
        "branch": git(["rev-parse", "--abbrev-ref", "HEAD"], root), "working_tree_clean": not dirty,
        "uncommitted_files": dirty.splitlines(), "python": pyver.strip(), "pytest": pytest_ver.strip(),
        "os": platform.platform(), "command": " ".join(cmd), "exit_code": code,
        "duration_seconds": round(duration, 1), "results": results, "submodules": pins,
        "manual_checks": checks, "notes": notes,
    }
    (out_dir / "evidence.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    md = [
        "# O.B.E.O.S. Release Evidence", "",
        f"Generated {data['generated_utc']} by `tools/release_evidence.py`. "
        "This file supersedes `docs/release-evidence-latest.md` for any claim it makes.", "",
        "## Summary", "",
        "| | |", "|---|---|",
        f"| Commit | `{sha}` |", f"| Branch | `{data['branch']}` |",
        f"| Mode | {mode} |",
        f"| Working tree | {'clean' if not dirty else f'DIRTY ({len(dirty.splitlines())} uncommitted files): not a clean-checkout result'} |",
        f"| OS | {data['os']} |", f"| Python | {data['python']} |", f"| pytest | {data['pytest']} |",
        f"| Automated tests | **{automated}** |",
        f"| Manual checks | **{len(pending)} of {len(checks)} pending** |", "",
        "## Automated tests", "", f"Command: `{data['command']}`", "",
        f"Exit code {code}; {results['tests']} tests, {results['failures']} failed, {results['errors']} errors, "
        f"{results['skipped']} skipped, {duration:.0f}s. Full output: `pytest-run.log`, `junit.xml`; "
        "installed packages: `pip-freeze.txt`.", "",
    ]
    if results["failed_ids"]:
        md += ["Failing or erroring tests (first 25):", ""] + [f"- `{i}`" for i in results["failed_ids"][:25]] + [""]
    md += ["## Pinned components", "", "| Path | Pinned commit | Initialized | Matches pin |", "|---|---|---|---|"]
    md += [f"| `{p['path']}` | `{p['pinned']}` | {'yes' if p['initialized'] else 'no'} | "
           f"{'yes' if p['matches_pin'] else 'no'} |" for p in pins] or ["| none | | | |"]
    md += ["", "Component tests under `3000-main/`, `Blackforge/`, `EarthGlass/` and the other submodules are not "
           "run by `tests/`; a component that is not initialized was not tested at all.", ""]
    md += ["## Manual checks", ""]
    if checks_note:
        md += [f"_{checks_note}_", ""]
    else:
        md += ["| Status | Check | Last verified | Evidence / note |", "|---|---|---|---|"]
        md += [f"| {c.get('status', 'pending')} | {c.get('description', c.get('id', ''))} | "
               f"{c.get('verified_on') or 'never'} | {c.get('note', '')} |" for c in checks]
        md += [""]
    if notes:
        md += ["## Run notes", ""] + [f"- {n}" for n in notes] + [""]
    md += [
        "## What this does and does not show", "",
        "- Shows: the tests selected by the marker expression ran against the commit above, in the environment above.",
        "- Does not show: that the release gate in `docs/obeos-v0.9-release-gate.md` is met. Pending manual checks, "
        "uninitialized components, and any unselected marker profile (`integration`, `packaging`, `remaster`, `vision`) remain unverified.", "",
        "## Reproduce", "", "```", f"python tools/release_evidence.py {'--fresh ' if args.fresh else ''}--markers {args.markers}", "```", "",
    ]
    report = out_dir / "RELEASE-EVIDENCE.md"
    report.write_text("\n".join(md), encoding="utf-8")
    print(f"Automated tests: {automated} ({results['tests']} tests, exit {code})")
    print(f"Manual checks pending: {len(pending)} of {len(checks)}")
    print(f"Evidence written to {report}")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
