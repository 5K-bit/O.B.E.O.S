from typer.testing import CliRunner

from {{ package_name }}.cli import app


def test_hello_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "Hello from {{ project_name }}" in result.stdout
