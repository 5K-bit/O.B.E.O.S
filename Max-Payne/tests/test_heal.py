from pathlib import Path

from maxpayne.heal import build_sanitized_env_example, heal_dependency, heal_env_files


def test_build_sanitized_env_example_creates_placeholders() -> None:
    source = "API_KEY=secret\nDEBUG=true\n# comment\n"

    rendered = build_sanitized_env_example(source)

    assert "API_KEY=<set-me>" in rendered
    assert "DEBUG=<set-me>" in rendered
    assert "# comment" in rendered


def test_heal_env_files_creates_example_when_missing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TOKEN=abc123\n", encoding="utf-8")

    result = heal_env_files(project_dir=tmp_path)

    assert result.status == "PASS"
    generated = tmp_path / ".env.example"
    assert generated.exists()
    assert "TOKEN=<set-me>" in generated.read_text(encoding="utf-8")


def test_heal_dependency_rejects_invalid_package_name() -> None:
    result = heal_dependency("bad package")

    assert result.status == "FAIL"
    assert "Invalid package name" in result.message
