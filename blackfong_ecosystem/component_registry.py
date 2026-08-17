from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MANIFEST_NAME = "obeos.manifest.json"
SCHEMA_VERSION = 1
ALLOWED_COMPONENT_TYPES = {
    "agent", "connector", "device", "event-consumer", "hud", "service", "skill", "worker"
}


class ManifestError(ValueError):
    """Raised when an OBEOS component manifest violates the v1 contract."""


@dataclass(frozen=True, slots=True)
class ComponentManifest:
    name: str
    component_type: str
    version: str
    entrypoint: str
    interfaces: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    lifecycle: str = "on-demand"
    dependencies: tuple[str, ...] = ()
    health: dict[str, Any] = field(default_factory=dict)
    source_repository: str | None = None
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, path: Path | None = None) -> "ComponentManifest":
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError(f"{path or MANIFEST_NAME}: schema_version must be {SCHEMA_VERSION}")
        required = ("name", "component_type", "version", "entrypoint")
        missing = [key for key in required if not isinstance(data.get(key), str) or not data.get(key).strip()]
        if missing:
            raise ManifestError(f"{path or MANIFEST_NAME}: missing/invalid fields: {', '.join(missing)}")
        component_type = str(data["component_type"])
        if component_type not in ALLOWED_COMPONENT_TYPES:
            raise ManifestError(f"{path or MANIFEST_NAME}: unsupported component_type {component_type!r}")
        for key in ("interfaces", "capabilities", "dependencies"):
            value = data.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise ManifestError(f"{path or MANIFEST_NAME}: {key} must be a list of non-empty strings")
        health = data.get("health", {})
        if not isinstance(health, dict):
            raise ManifestError(f"{path or MANIFEST_NAME}: health must be an object")
        return cls(
            name=str(data["name"]),
            component_type=component_type,
            version=str(data["version"]),
            entrypoint=str(data["entrypoint"]),
            interfaces=tuple(data.get("interfaces", [])),
            capabilities=tuple(data.get("capabilities", [])),
            lifecycle=str(data.get("lifecycle") or "on-demand"),
            dependencies=tuple(data.get("dependencies", [])),
            health=dict(health),
            source_repository=data.get("source_repository"),
            path=path,
            raw=dict(data),
        )

    @classmethod
    def load(cls, path: Path) -> "ComponentManifest":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Could not read {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ManifestError(f"{path}: manifest root must be an object")
        return cls.from_dict(payload, path=path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "component_type": self.component_type,
            "version": self.version,
            "entrypoint": self.entrypoint,
            "interfaces": list(self.interfaces),
            "capabilities": list(self.capabilities),
            "lifecycle": self.lifecycle,
            "dependencies": list(self.dependencies),
            "health": self.health,
            "source_repository": self.source_repository,
            "manifest_path": str(self.path) if self.path else None,
        }


@dataclass(slots=True)
class ComponentRegistry:
    components: dict[str, ComponentManifest] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def register(self, manifest: ComponentManifest) -> None:
        current = self.components.get(manifest.name)
        if current and current.path != manifest.path:
            raise ManifestError(f"Duplicate OBEOS component name: {manifest.name}")
        self.components[manifest.name] = manifest

    def discover(self, root: Path, *, extra_roots: Iterable[Path] = ()) -> "ComponentRegistry":
        roots = (root, *tuple(extra_roots))
        seen: set[Path] = set()
        for base in roots:
            base = base.expanduser().resolve()
            if not base.exists():
                continue
            candidates = [base / MANIFEST_NAME]
            candidates.extend(base.glob(f"*/{MANIFEST_NAME}"))
            for path in candidates:
                path = path.resolve()
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                try:
                    self.register(ComponentManifest.load(path))
                except ManifestError as exc:
                    self.errors.append(str(exc))
        return self

    def get(self, name: str) -> ComponentManifest | None:
        return self.components.get(name)

    def by_capability(self, capability: str) -> list[ComponentManifest]:
        return [item for item in self.components.values() if capability in item.capabilities]

    def unresolved_dependencies(self) -> dict[str, list[str]]:
        known = set(self.components)
        return {
            name: [dep for dep in manifest.dependencies if dep not in known]
            for name, manifest in self.components.items()
            if any(dep not in known for dep in manifest.dependencies)
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "components": {name: item.to_dict() for name, item in sorted(self.components.items())},
            "errors": list(self.errors),
            "unresolved_dependencies": self.unresolved_dependencies(),
            "count": len(self.components),
        }


def discover_components(root: Path | str) -> ComponentRegistry:
    return ComponentRegistry().discover(Path(root))
