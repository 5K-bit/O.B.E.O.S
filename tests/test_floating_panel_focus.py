import pytest

pytest.importorskip("PySide6")
from blackfong_ecosystem.floating_panel_focus import panel_opacity


@pytest.mark.parametrize("floating,overlapping,active,hovered,expected", [
    (True, True, False, False, 0.85),
    (False, True, False, False, 1.0),
    (True, False, False, False, 1.0),
    (True, True, True, False, 1.0),
    (True, True, False, True, 1.0),
])
def test_only_inactive_overlapping_float_is_dimmed(floating, overlapping, active, hovered, expected):
    assert panel_opacity(floating=floating, overlapping=overlapping, active=active,
                         hovered=hovered, inactive_opacity=0.85) == expected
