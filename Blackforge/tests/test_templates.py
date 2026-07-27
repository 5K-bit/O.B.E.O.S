from blackforge.core.templates import list_templates


def test_list_templates_discovers_builtin_templates() -> None:
    assert list_templates() == ["cli", "fastapi", "python-package"]
