from pathlib import Path

from typer.testing import CliRunner

from maxpayne import cli
from maxpayne.explain import ExplanationResult
from maxpayne.core.result import CheckResult

runner = CliRunner()


def test_heal_default_exit_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "apply_default_heal",
        lambda interactive=False: [CheckResult("heal.default", "PASS", "done", "none")],
    )

    result = runner.invoke(cli.app, ["heal"])

    assert result.exit_code == 0


def test_heal_subcommands_exit_zero(monkeypatch) -> None:
    monkeypatch.setattr(cli, "heal_git_config", lambda interactive=False: CheckResult("heal.git", "PASS", "ok", "none"))
    monkeypatch.setattr(cli, "heal_env_files", lambda: CheckResult("heal.env", "PASS", "ok", "none"))
    monkeypatch.setattr(cli, "heal_port", lambda port, interactive=False, dry_run=True: CheckResult(f"heal.port.{port}", "PASS", "ok", "none"))
    monkeypatch.setattr(cli, "heal_dependency", lambda package: CheckResult(f"heal.dependency.{package}", "PASS", "ok", "none"))

    commands = [
        ["heal", "git"],
        ["heal", "env"],
        ["heal", "port", "8000"],
        ["heal", "dependency", "fastapi"],
    ]
    for command in commands:
        result = runner.invoke(cli.app, command)
        assert result.exit_code == 0


def test_explain_command_exit_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "explain_file",
        lambda path, model="llama3.2": ExplanationResult(source="heuristic", explanation="Looks fixable."),
    )

    log_file = tmp_path / "crash.log"
    log_file.write_text("Traceback...", encoding="utf-8")

    result = runner.invoke(cli.app, ["explain", str(log_file)])

    assert result.exit_code == 0
    assert "Looks fixable" in result.output
