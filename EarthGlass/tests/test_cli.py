from typer.testing import CliRunner

from earthglass.cli import app

runner = CliRunner()


def test_cli_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "layers" in result.output
    assert "cache" in result.output


def test_cli_layers_shows_earthquakes() -> None:
    result = runner.invoke(app, ["layers"])
    assert result.exit_code == 0
    assert "earthquakes" in result.output.lower()


def test_cli_cache_status() -> None:
    result = runner.invoke(app, ["cache", "status"])
    assert result.exit_code == 0
