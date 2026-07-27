from maxpayne.checks import windows_check


def test_windows_checks_skip_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(windows_check, "detect_platform", lambda: ("Linux", False))

    results = windows_check.run_windows_checks()

    assert len(results) == 9
    assert all(result.status == "PASS" for result in results)
    assert any(result.name == "windows.wsl_installed" for result in results)
