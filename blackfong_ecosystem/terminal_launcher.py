"""Open an external Windows Terminal session as a separate process.

Decision 10, phase 1: the Workspace never hosts or controls the shell. The terminal is
started detached, with an argument list (never ``shell=True``), so closing the
Workspace does not close it and no operator text is ever interpolated into a command line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def resolve_terminal_command(
    start_directory: Optional[str] = None,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Optional[List[str]]:
    """Return the argv for a new terminal window, or None when none is available."""
    directory = str(Path(start_directory)) if start_directory else None
    wt = which("wt.exe") or which("wt")
    if wt:
        argv = [wt, "new-tab"]
        if directory:
            argv += ["-d", directory]
        return argv
    powershell = which("pwsh.exe") or which("powershell.exe")
    if powershell:
        return [powershell, "-NoLogo", "-NoExit"]
    return None


def open_terminal(
    start_directory: Optional[str] = None,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    popen: Callable[..., Any] = subprocess.Popen,
    platform: str = sys.platform,
) -> Dict[str, Any]:
    """Start a detached terminal. Returns a result dict; never raises for expected failures."""
    if platform != "win32":
        return {"success": False, "error": "Windows Terminal is only available on Windows."}
    if start_directory and not Path(start_directory).is_dir():
        return {"success": False, "error": f"Start directory does not exist: {start_directory}"}
    argv = resolve_terminal_command(start_directory, which=which)
    if argv is None:
        return {"success": False, "error": "Neither Windows Terminal nor PowerShell was found on PATH."}
    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": start_directory or None,
        "creationflags": _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
    }
    try:
        popen(list(argv), **kwargs)
    except OSError as exc:
        return {"success": False, "error": f"Could not start terminal: {exc}", "argv": list(argv)}
    return {"success": True, "argv": list(argv)}
