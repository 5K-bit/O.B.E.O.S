from __future__ import annotations

import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def pytest_collection_modifyitems(config, items):
    del config
    for item in items:
        node = item.nodeid.replace("\\", "/").lower()
        item.add_marker(pytest.mark.full)
        if "test_camera.py" in node or "test_motion.py" in node or "test_cli.py" in node:
            item.add_marker(pytest.mark.vision)
        else:
            item.add_marker(pytest.mark.quick)
