"""Node.js and npm checks."""

from __future__ import annotations

from maxpayne.core.result import CheckResult
from maxpayne.core.system import command_exists


def run_node_checks() -> list[CheckResult]:
    node_available, node_command = command_exists("node", "node.exe")
    npm_available, npm_command = command_exists("npm", "npm.cmd", "npm.exe")

    return [
        CheckResult(
            name="node.available",
            status="PASS" if node_available else "FAIL",
            message=(
                f"node is available via `{node_command}`."
                if node_available
                else "node was not found in PATH."
            ),
            suggestion=(
                "No action required."
                if node_available
                else "Install Node.js and ensure `node` is available in your PATH."
            ),
        ),
        CheckResult(
            name="node.npm",
            status="PASS" if npm_available else "FAIL",
            message=(
                f"npm is available via `{npm_command}`."
                if npm_available
                else "npm was not found in PATH."
            ),
            suggestion=(
                "No action required."
                if npm_available
                else "Install npm and ensure it is available in your PATH."
            ),
        ),
    ]
