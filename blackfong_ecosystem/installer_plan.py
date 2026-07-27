from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import Event
from .spine_adapters import PROJECTS_ROOT
from .spine_contract import ADAPTER_CONTRACT_VERSION
from .storage import EventStore


DEFAULT_INSTALLER_ROOT = PROJECTS_ROOT / "Blackfong-Installer-main" / "Blackfong-Installer-main"
INSTALL_BUNDLES = [
    "obeos_core",
    "daise_service",
    "jarvis_agent",
    "learning_agents",
    "tinyprogrammer",
    "vision_tools",
    "memory_chamber",
    "sentinel",
    "dream_layer",
    "blackbox_archive",
    "skill_forge",
    "forge_device_builder",
]
INSTALL_BUNDLE_DISPLAY_NAMES = {
    "obeos_core": "O.B.E.O.S Core",
    "daise_service": "D.A.I.S.E Service",
    "jarvis_agent": "D.A.I.S.E Compatibility Assistant Path",
    "learning_agents": "Learning Agents",
    "tinyprogrammer": "TinyProgrammer",
    "vision_tools": "Vision Tools",
    "memory_chamber": "Memory Chamber",
    "sentinel": "Sentinel",
    "dream_layer": "Dream Layer",
    "blackbox_archive": "Blackbox Archive",
    "skill_forge": "Skill Forge",
    "forge_device_builder": "Forge Device Builder",
}
INSTALLER_DOCTRINE_NOTES = (
    "Canonical O.B.E.O.S. v1 session doctrine is Kubuntu 24.04 LTS plus KDE Plasma.",
    "The external Blackfong installer project remains migration/build input until its desktop doctrine is fully aligned with O.B.E.O.S. v1.",
    "jarvis_agent remains a compatibility bundle name; D.A.I.S.E. is the visible assistant identity.",
)


@dataclass(frozen=True)
class InstallerPlan:
    profile: str
    installer_root: str
    bundles: List[str]
    services: List[str]
    bundle_display_names: Dict[str, str] = field(default_factory=dict)
    installer_status: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter_contract_version": self.adapter_contract_version,
            "profile": self.profile,
            "installer_root": self.installer_root,
            "bundles": self.bundles,
            "bundle_display_names": self.bundle_display_names,
            "installer_status": self.installer_status,
            "services": self.services,
            "notes": self.notes,
            "destructive": False,
            "requires_explicit_hardware_approval": True,
        }


class InstallerPlanService:
    def __init__(
        self,
        *,
        installer_root: Path = DEFAULT_INSTALLER_ROOT,
        event_store: Optional[EventStore] = None,
    ) -> None:
        self.installer_root = installer_root
        self.event_store = event_store or EventStore()

    def current_status(self) -> Dict[str, Any]:
        return {
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
            "state": "migration_input_only",
            "shipping_ready": False,
            "installer_root": str(self.installer_root),
            "installer_root_exists": self.installer_root.exists(),
            "recommended_v1_path": "kubuntu_materialize_remaster",
            "summary": (
                "The external Blackfong installer repo is currently migration/build input only, "
                "not the final shipping O.B.E.O.S. v1 installer."
            ),
            "doctrine_gap": "Desktop implementation still follows an XFCE/Xubuntu-style migration baseline.",
            "promotion_gate": "Align the external installer implementation with Kubuntu 24.04 LTS + KDE Plasma before promoting it as a shipping installer.",
        }

    def build_plan(self, profile: str = "amd64-pc") -> InstallerPlan:
        profile_path = self.installer_root / "manifests" / "profiles" / f"{profile}.yaml"
        notes = list(INSTALLER_DOCTRINE_NOTES)
        if not profile_path.exists():
            notes.append(f"Profile manifest not found yet: {profile_path}")
        return InstallerPlan(
            profile=profile,
            installer_root=str(self.installer_root),
            bundles=list(INSTALL_BUNDLES),
            bundle_display_names={bundle: INSTALL_BUNDLE_DISPLAY_NAMES.get(bundle, bundle) for bundle in INSTALL_BUNDLES},
            installer_status=self.current_status(),
            services=["daise", "blackfong-hud", "tinyprogrammer"],
            notes=notes,
        )

    def validate_plan(self, plan: InstallerPlan) -> Dict[str, Any]:
        root = Path(plan.installer_root)
        required_paths = [
            root / "manifests" / "base.yaml",
            root / "manifests" / "features.yaml",
            root / "manifests" / "profiles" / f"{plan.profile}.yaml",
        ]
        checks = [
            {"path": str(path), "ok": path.exists()}
            for path in required_paths
        ]
        missing_bundles = [bundle for bundle in INSTALL_BUNDLES if bundle not in plan.bundles]
        ok = all(check["ok"] for check in checks) and not missing_bundles
        return {
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
            "ok": ok,
            "installer_status": plan.installer_status,
            "checks": checks,
            "missing_bundles": missing_bundles,
            "plan": plan.to_dict(),
        }

    def persist_plan(self, plan: InstallerPlan) -> str:
        self.event_store.init_db()
        event = Event.new(
            source="installer",
            type="installer_plan_generated",
            payload=plan.to_dict(),
            confidence=0.9,
            tags=["installer", "plan", plan.profile],
        )
        self.event_store.add_event(event)
        return event.id

    def export_plan(self, profile: str, output: Path) -> Dict[str, Any]:
        plan = self.build_plan(profile)
        validation = self.validate_plan(plan)
        event_id = self.persist_plan(plan)
        payload = {"event_id": event_id, "validation": validation, "plan": plan.to_dict()}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
