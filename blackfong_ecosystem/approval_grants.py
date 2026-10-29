"""Single-use, action-bound approval grants issued by Blackfong's trusted operator surface."""

import hashlib
import json
import secrets
import sys
import threading
import time
from typing import Callable, Dict, IO, Optional


class ApprovalGrants:
    def __init__(self, policy):
        self.policy = policy
        self._grants = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(action, parameters):
        return hashlib.sha256(
            json.dumps(
                {"action": action, "parameters": parameters},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def issue(self, action, parameters, *, operator_confirmed=False, ttl_seconds=60):
        if not operator_confirmed:
            raise PermissionError("A trusted operator confirmation is required")
        decision = self.policy.evaluate(
            "operator.remediation",
            {"approval_granted": True, "source": "operator", "transport": "local"},
        )
        if not decision.approved:
            raise PermissionError("Blackfong policy denied this operation")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._grants = {
                key: value
                for key, value in self._grants.items()
                if value[1] > time.monotonic()
            }
            self._grants[token] = (
                self._digest(action, parameters),
                time.monotonic() + min(300, max(1, ttl_seconds)),
            )
        return token

    def consume(self, action, parameters, authorization):
        if not authorization:
            return False
        token = authorization.removeprefix("Bearer ")
        with self._lock:
            record = self._grants.pop(token, None)
        return bool(
            record
            and record[1] > time.monotonic()
            and secrets.compare_digest(record[0], self._digest(action, parameters))
        )


def build_maxpayne_app(authority):
    from maxpayne.server import create_app

    return create_app(authorize_remediation=authority.consume)


def run_approval_console(
    authority: "ApprovalGrants",
    *,
    input_stream: Optional[IO[str]] = None,
    write: Optional[Callable[[str], None]] = None,
) -> None:
    """Interactive trusted-operator console that mints single-use remediation grants.

    This must only be driven from a local, trusted terminal - never exposed over a
    network transport - since typing 'y' here is the operator confirmation that
    ``ApprovalGrants.issue`` requires. Runs until the operator types 'quit'/'exit'
    or sends EOF.
    """
    reader = input_stream or sys.stdin
    emit = write or sys.stdout.write

    def line(text: str = "") -> None:
        emit(text + "\n")

    def prompt(text: str) -> str:
        emit(text)
        return reader.readline()

    registry = None
    try:
        from maxpayne.core.remediation import default_remediation_registry

        registry = default_remediation_registry()
    except ImportError:
        registry = None

    line("Blackfong trusted-operator console.")
    line("Applied remediation over HTTP is grant-gated and never self-approves from the web API.")
    line("Commands: list | approve <remediation_id> [key=value ...] | help | quit")

    while True:
        raw = prompt("\nmaxpayne> ")
        if not raw:
            line()
            return
        command = raw.strip()
        if not command:
            continue
        if command in {"quit", "exit"}:
            return
        if command in {"help", "?"}:
            line("list                     Show remediations MaxPayne can apply.")
            line("approve <id> [k=v ...]   Confirm and mint a single-use grant for <id>.")
            line("quit                     Leave the console (the server keeps running).")
            continue
        if command == "list":
            if registry is None:
                line("MaxPayne is not importable from this environment.")
                continue
            for definition in registry.list():
                params = ", ".join(definition["parameters"]) or "none"
                line(f"- {definition['remediation_id']} [{definition['safety']}] params=({params}): {definition['description']}")
            continue

        parts = command.split()
        if parts[0] != "approve" or len(parts) < 2:
            line("Unknown command. Type 'help'.")
            continue

        remediation_id = parts[1]
        parameters: Dict[str, str] = {}
        malformed = False
        for token in parts[2:]:
            if "=" not in token:
                malformed = True
                break
            key, _, value = token.partition("=")
            parameters[key] = value
        if malformed:
            line("Parameters must be key=value pairs, e.g. approve port.free port=3000")
            continue

        description, safety = remediation_id, "UNKNOWN"
        if registry is not None:
            try:
                definition = registry.get(remediation_id)
            except ValueError as exc:
                line(str(exc))
                continue
            description, safety = definition.description, definition.safety

        line(f"About to authorize: {remediation_id} [{safety}] {description}")
        line(f"Parameters: {parameters if parameters else 'none'}")
        answer = prompt("Confirm as trusted operator? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            line("Not confirmed. No grant issued.")
            continue

        try:
            token = authority.issue(remediation_id, parameters, operator_confirmed=True)
        except PermissionError as exc:
            line(f"Denied: {exc}")
            continue

        line("Grant issued (single-use, expires in 60s):")
        line(f"  Authorization: Bearer {token}")
        line("Use it immediately, e.g.:")
        line(f"  curl -X POST 'http://127.0.0.1:8788/api/remediate/{remediation_id}?apply=true' \\")
        line("       -H \"Authorization: Bearer " + token + "\" -H 'Content-Type: application/json' \\")
        line(f"       -d '{json.dumps(parameters)}'")
