# Pytest root conftest: ensure package is importable when running from repo root.
import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

for project_root in (
    "3000-main",
    "Blackforge",
    "Blackforge/earthglass",
    "EarthGlass",
    "Max-Payne",
    "Night-watch-OS",
    "QuietDrop/QuietDrop-main",
):
    candidate = root / project_root
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))


def pytest_collection_modifyitems(config, items):
    del config
    for item in items:
        node = item.nodeid.replace("\\", "/").lower()
        item.add_marker(pytest.mark.full)
        if "3000-main/tests/test_camera.py" in node or "3000-main/tests/test_motion.py" in node or "3000-main/tests/test_cli.py" in node:
            item.add_marker(pytest.mark.vision)
        elif "remaster" in node or "vm_acceptance" in node:
            item.add_marker(pytest.mark.remaster)
        elif "installer" in node or "docker" in node or "packaging" in node:
            item.add_marker(pytest.mark.packaging)
        elif "integration" in node or "benchmark" in node or "verification_and_integrations" in node:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.quick)
