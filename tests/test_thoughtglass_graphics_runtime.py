from __future__ import annotations

from blackfong_ecosystem.thoughtglass_scene_3d import _opengl_runtime_allowed


def test_wsl_defaults_to_safe_2d_graphics() -> None:
    assert not _opengl_runtime_allowed(
        environ={"WSL_DISTRO_NAME": "Ubuntu"},
        kernel_release="6.6.87.2-microsoft-standard-WSL2",
    )


def test_wsl_3d_can_be_explicitly_enabled() -> None:
    assert _opengl_runtime_allowed(
        environ={
            "WSL_DISTRO_NAME": "Ubuntu",
            "BLACKFONG_THOUGHTGLASS_3D": "1",
        },
        kernel_release="6.6.87.2-microsoft-standard-WSL2",
    )


def test_3d_can_be_disabled_on_native_systems() -> None:
    assert not _opengl_runtime_allowed(
        environ={"BLACKFONG_THOUGHTGLASS_3D": "off"},
        kernel_release="6.8.0-generic",
    )


def test_native_systems_keep_3d_enabled_by_default() -> None:
    assert _opengl_runtime_allowed(environ={}, kernel_release="6.8.0-generic")
