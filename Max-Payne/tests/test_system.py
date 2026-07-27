import sys

from maxpayne.core.system import run_command


def test_run_command_times_out() -> None:
    result = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)

    assert result.timed_out is True
    assert result.returncode is None
    assert "Timed out" in (result.error or "")
