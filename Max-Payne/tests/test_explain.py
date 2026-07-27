from pathlib import Path

from maxpayne import explain


def test_explain_text_falls_back_to_heuristics(monkeypatch) -> None:
    monkeypatch.setattr(explain, "_ollama_available", lambda: False)

    result = explain.explain_text("ModuleNotFoundError: No module named 'fastapi'")

    assert result.source == "heuristic"
    assert "missing module" in result.explanation.lower()


def test_explain_file_reads_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(explain, "_ollama_available", lambda: False)
    trace = tmp_path / "traceback.txt"
    trace.write_text("ConnectionRefusedError: [Errno 111]", encoding="utf-8")

    result = explain.explain_file(trace)

    assert result.source == "heuristic"
    assert "service" in result.explanation.lower() or "connect" in result.explanation.lower()
