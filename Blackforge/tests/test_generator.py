from pathlib import Path

import pytest

from blackforge.core.generator import GenerationError, generate_project


@pytest.mark.parametrize(
    ("template_name", "project_name", "expected_files"),
    [
        (
            "cli",
            "my-tool",
            [
                "README.md",
                "pyproject.toml",
                ".gitignore",
                "tests/test_smoke.py",
                "my_tool/cli.py",
            ],
        ),
        (
            "fastapi",
            "my-api",
            [
                "README.md",
                "pyproject.toml",
                ".gitignore",
                "app/main.py",
                "tests/test_health.py",
            ],
        ),
        (
            "python-package",
            "my-package",
            [
                "README.md",
                "pyproject.toml",
                ".gitignore",
                "tests/test_package.py",
                "my_package/__init__.py",
            ],
        ),
    ],
)
def test_generate_project_creates_required_files(
    tmp_path: Path,
    template_name: str,
    project_name: str,
    expected_files: list[str],
) -> None:
    result = generate_project(template_name, project_name, output_dir=tmp_path)

    assert result.destination == tmp_path / project_name
    assert result.destination.is_dir()

    for relative_path in expected_files:
        assert (result.destination / relative_path).exists(), relative_path


def test_generate_project_renders_jinja_variables(tmp_path: Path) -> None:
    result = generate_project("cli", "my-tool", output_dir=tmp_path)
    readme = (result.destination / "README.md").read_text(encoding="utf-8")
    assert "# my-tool" in readme
    assert "Generated with BlackForge on" in readme


def test_fastapi_template_contains_health_route(tmp_path: Path) -> None:
    result = generate_project("fastapi", "my-api", output_dir=tmp_path)
    main_py = (result.destination / "app/main.py").read_text(encoding="utf-8")
    health_test = (result.destination / "tests/test_health.py").read_text(encoding="utf-8")
    assert '@app.get("/health")' in main_py
    assert 'client.get("/health")' in health_test


def test_generate_project_blocks_overwrite_without_force(tmp_path: Path) -> None:
    generate_project("python-package", "my-package", output_dir=tmp_path)

    with pytest.raises(GenerationError):
        generate_project("python-package", "my-package", output_dir=tmp_path)


def test_generate_project_allows_overwrite_with_force(tmp_path: Path) -> None:
    result = generate_project("python-package", "my-package", output_dir=tmp_path)
    package_init = result.destination / "my_package/__init__.py"
    package_init.write_text("changed", encoding="utf-8")

    generate_project("python-package", "my-package", output_dir=tmp_path, force=True)

    assert '"""my-package package."""' in package_init.read_text(encoding="utf-8")


def test_generate_project_preview_does_not_write_files(tmp_path: Path) -> None:
    result = generate_project("cli", "my-tool", output_dir=tmp_path, preview=True)

    assert result.preview is True
    assert result.created_files
    assert not result.destination.exists()


@pytest.mark.parametrize("project_name", ["../escape", "nested/project", "nested\\project"])
def test_generate_project_rejects_path_traversal(tmp_path: Path, project_name: str) -> None:
    with pytest.raises(GenerationError):
        generate_project("cli", project_name, output_dir=tmp_path)
