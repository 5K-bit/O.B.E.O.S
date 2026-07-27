import importlib.util
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None
pytestmark = pytest.mark.skipif(not CV2_AVAILABLE, reason="cv2 unavailable")

if CV2_AVAILABLE:
    from three_thousand import cli


runner = CliRunner()


def test_status_handles_unavailable_camera(monkeypatch) -> None:
    monkeypatch.setattr(cli, "is_camera_available", lambda _idx: False)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "unavailable" in result.stdout.lower()


def test_snapshot_command_uses_capture_and_save(monkeypatch, tmp_path) -> None:
    fake_frame = np.zeros((8, 8, 3), dtype=np.uint8)
    expected_path = tmp_path / "snapshot.jpg"

    monkeypatch.setattr(cli, "capture_frame", lambda _idx: fake_frame)
    monkeypatch.setattr(cli, "save_frame", lambda _frame, _dir: Path(expected_path))

    result = runner.invoke(cli.app, ["snapshot"])
    assert result.exit_code == 0
    assert str(expected_path) in result.stdout


def test_snapshot_command_exits_when_frame_missing(monkeypatch) -> None:
    monkeypatch.setattr(cli, "capture_frame", lambda _idx: None)
    result = runner.invoke(cli.app, ["snapshot"])
    assert result.exit_code == 1
