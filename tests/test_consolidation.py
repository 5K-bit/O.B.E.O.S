import ast
from pathlib import Path
from types import SimpleNamespace, ModuleType


def test_tiny_stream_only_publishes_without_native_window(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "TinyProgrammer-main/display/terminal.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_flip")
    frames = []
    module = ModuleType("display.frame_stream")
    module.put_frame = frames.append
    monkeypatch.setitem(__import__("sys").modules, "display.frame_stream", module)
    namespace = {"__package__": "display", "time": SimpleNamespace(time=lambda: 10),
                 "config": SimpleNamespace(WEB_STREAM_ENABLED=True)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    surface = object()
    terminal = SimpleNamespace(_last_flip=0, _min_flip_interval=0.033, _dirty=True,
                               fb_writer=None, _window=None, screen=surface)
    namespace["_flip"](terminal)
    assert frames == [surface]


def test_daise_model_does_not_change_other_agents():
    from blackfong_ecosystem.config import ChatSettings
    settings = ChatSettings()
    assert settings.model_for_agent("daise") == "gemma4-heretic:latest"
    assert settings.model_for_agent("jarvis") == "gemma4-heretic:latest"
    assert settings.model_for_agent("coding") == settings.coding_model
