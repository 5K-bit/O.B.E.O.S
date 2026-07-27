from maxpayne.core.result import CheckResult


def test_check_result_fields() -> None:
    result = CheckResult(
        name="python.version",
        status="PASS",
        message="Python version is valid.",
        suggestion="No action required.",
    )

    assert result.name == "python.version"
    assert result.status == "PASS"
    assert result.message == "Python version is valid."
    assert result.suggestion == "No action required."
    assert result.details is None
