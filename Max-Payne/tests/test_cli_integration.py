import json

from typer.testing import CliRunner

from maxpayne import cli
from maxpayne.checks import docker_check, git_check, ollama_check, python_check
from maxpayne.core.result import CheckResult
from maxpayne.core.runner import CheckRunner

runner = CliRunner()


def _sample_results() -> list[CheckResult]:
    return [
        CheckResult(
            name="sample.pass",
            status="PASS",
            message="pass result",
            suggestion="none",
            details=None,
        ),
        CheckResult(
            name="sample.warn",
            status="WARN",
            message="warn result",
            suggestion="check this",
            details="warn details",
        ),
        CheckResult(
            name="sample.fail",
            status="FAIL",
            message="fail result",
            suggestion="fix this",
            details="fail details",
        ),
    ]


def test_cli_help_exit_code_zero() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0


def test_cli_diagnose_exit_code_zero(monkeypatch) -> None:
    monkeypatch.setattr(CheckRunner, "run_all", lambda self: _sample_results())

    result = runner.invoke(cli.app, ["diagnose"])
    assert result.exit_code == 0


def test_cli_report_schema_and_status_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(CheckRunner, "run_all", lambda self: _sample_results())

    output_path = tmp_path / "maxpayne-report.json"
    result = runner.invoke(cli.app, ["report", "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert {"generated_at", "platform", "summary", "results"} <= set(payload)
    assert {"system", "is_wsl"} <= set(payload["platform"])
    assert {"pass", "warn", "fail"} <= set(payload["summary"])
    assert isinstance(payload["results"], list)
    assert payload["results"]

    allowed_statuses = {"pass", "warn", "fail"}
    for row in payload["results"]:
        assert {"name", "status", "message", "suggestion", "details"} <= set(row)
        assert row["status"] in allowed_statuses


def test_doctor_subcommands_exit_zero_when_tools_missing(monkeypatch) -> None:
    monkeypatch.setattr(python_check, "command_exists", lambda *_args: (False, None))
    monkeypatch.setattr(git_check, "command_exists", lambda *_args: (False, None))
    monkeypatch.setattr(docker_check, "command_exists", lambda *_args: (False, None))
    monkeypatch.setattr(ollama_check, "command_exists", lambda *_args: (False, None))
    monkeypatch.setattr(
        ollama_check,
        "_is_ollama_server_reachable",
        lambda: (False, "connection refused"),
    )

    commands = [
        ["doctor", "python"],
        ["doctor", "git"],
        ["doctor", "docker"],
        ["doctor", "ollama"],
    ]

    for command in commands:
        result = runner.invoke(cli.app, command)
        assert result.exit_code == 0


def test_runner_isolates_failing_check_group() -> None:
    def _failing_group() -> list[CheckResult]:
        raise RuntimeError("check exploded")

    custom_runner = CheckRunner(
        checks={
            "healthy": lambda: [CheckResult("healthy.ok", "PASS", "ok", "none")],
            "broken": _failing_group,
        }
    )

    results = custom_runner.run_all()
    assert len(results) == 2
    assert results[0].name == "healthy.ok"
    assert results[1].name == "broken.runtime"
    assert results[1].status == "FAIL"
