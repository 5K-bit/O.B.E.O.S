"""Credential references in configuration; secret values only in the OS key store."""

from __future__ import annotations
import json
from pathlib import Path
import threading

CREDENTIALS = {
    "firms_map_key": "NASA FIRMS (optional free map key)",
    "ny511_api_key": "511NY (optional free developer key)",
    "mta_bustime_api_key": "MTA Bus Time (optional free developer key)",
}


class CredentialBroker:
    def __init__(self, path: Path, backend=None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._backend = backend
        self.error = None
        self._references = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._references = {
                k: v
                for k, v in data.get("references", {}).items()
                if k in CREDENTIALS and v == "obeos-earth:" + k
            }

    def _secure_backend(self):
        if self._backend is None:
            import keyring

            backend = keyring.get_keyring()
            module = type(backend).__module__
            if module not in {
                "keyring.backends.Windows",
                "keyring.backends.SecretService",
                "keyring.backends.kwallet",
                "keyring.backends.macOS",
            }:
                raise RuntimeError("OS-protected credential storage is unavailable")
            self._backend = backend
        return self._backend

    def get(self, name):
        if name not in self._references:
            return None
        try:
            with self._lock:
                return self._secure_backend().get_password("OBEOS Earth", name)
        except Exception:
            self.error = "OS credential storage is locked or unavailable"
            return None

    def set(self, name, secret):
        if (
            name not in CREDENTIALS
            or not isinstance(secret, str)
            or not 1 <= len(secret.strip()) <= 4096
        ):
            raise ValueError("Invalid provider credential")
        with self._lock:
            self._secure_backend().set_password("OBEOS Earth", name, secret.strip())
            self._references[name] = "obeos-earth:" + name
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"version": 1, "references": self._references}, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            self.error = None

    def status(self):
        return {
            "providers": [
                {
                    "reference": name,
                    "label": label,
                    "configured": name in self._references,
                }
                for name, label in CREDENTIALS.items()
            ],
            "error": self.error,
        }
