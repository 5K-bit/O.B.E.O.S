from types import SimpleNamespace

import pytest

from blackfong_ecosystem import session_turn as turns


def test_one_turn_per_session_without_blocking_other_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(turns, "paths", SimpleNamespace(data_root=tmp_path))
    with turns.session_turn("first"):
        with pytest.raises(turns.SessionBusy):
            with turns.session_turn("first"):
                pytest.fail("duplicate admitted")
        with turns.session_turn("second"):
            pass
    with turns.session_turn("first"):
        pass


def test_turn_lease_released_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(turns, "paths", SimpleNamespace(data_root=tmp_path))
    with pytest.raises(ValueError):
        with turns.session_turn("first"):
            raise ValueError("generation failed")
    with turns.session_turn("first"):
        pass
