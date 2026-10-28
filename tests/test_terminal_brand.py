import importlib.util
import io
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("terminal_brand", Path(__file__).resolve().parents[1] / 'blackfong_ecosystem' / "terminal_brand.py")
brand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brand)

class Terminal(io.StringIO):
    def isatty(self):
        return True

def test_redirected_streams_are_clean():
    out, err = io.StringIO(), io.StringIO()
    with patch.object(brand.sys, 'stdout', out), patch.object(brand.sys, 'stderr', err):
        brand.show_banner()
    assert out.getvalue() == err.getvalue() == ''

def test_piped_stdout_suppresses_banner():
    out, err = io.StringIO(), Terminal()
    with patch.object(brand.sys, 'stdout', out), patch.object(brand.sys, 'stderr', err):
        brand.show_banner()
    assert out.getvalue() == err.getvalue() == ''

def test_explicit_banner_is_stderr_only():
    out, err = io.StringIO(), io.StringIO()
    with patch.object(brand.sys, 'stdout', out), patch.object(brand.sys, 'stderr', err):
        brand.show_banner(force=True)
    assert out.getvalue() == ''
    assert brand.PRODUCT in err.getvalue()
    assert 'BLACKFONG / PROJECTS' in err.getvalue()
    assert '\x1b' not in err.getvalue()

def test_no_color_and_dumb_terminal():
    for env in ({'NO_COLOR': ''}, {'TERM': 'dumb'}):
        out, err = Terminal(), Terminal()
        with patch.object(brand.sys, 'stdout', out), patch.object(brand.sys, 'stderr', err), patch.dict(brand.os.environ, env, clear=True):
            brand.show_banner()
        assert brand.PRODUCT in err.getvalue()
        assert '\x1b' not in err.getvalue()

def test_version_uses_metadata_with_source_fallback():
    with patch.object(brand, 'version', return_value='9.8.7'):
        assert brand.product_version() == '9.8.7'
    with patch.object(brand, 'version', side_effect=brand.PackageNotFoundError):
        assert brand.product_version() == brand.SOURCE_VERSION

def test_root_cli_help_version_and_machine_output():
    # Exercise the actual root decorators/callback without starting optional AI/GUI imports.
    import ast
    import click
    from click.testing import CliRunner
    source = Path(__file__).resolve().parents[1] / 'blackfong_ecosystem' / 'cli.py'
    tree = ast.parse(source.read_text())
    root = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'cli')
    namespace = {'click': click, 'PRODUCT': brand.PRODUCT, 'product_version': brand.product_version, 'show_banner': brand.show_banner}
    exec(compile(ast.Module(body=[root], type_ignores=[]), str(source), 'exec'), namespace)
    cli = namespace['cli']
    @cli.command()
    def probe():
        click.echo('{"ok":true}')
    runner = CliRunner()
    assert runner.invoke(cli, ['--help']).exit_code == 0
    assert 'O.B.E.O.S.' in runner.invoke(cli, ['--version']).output
    result = runner.invoke(cli, ['probe'])
    assert result.exit_code == 0
    assert result.output.strip() == '{"ok":true}'
