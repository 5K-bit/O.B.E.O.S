import pytest
from blackfong_ecosystem.cancellation import (
    CancellationToken,
    RequestCancelled,
    cancellation_scope,
    cancellation_resource,
)


def test_cancel_closes_transport_and_stops_tokens():
    token = CancellationToken()
    closed = []
    with cancellation_scope(token):
        with cancellation_resource(lambda: closed.append(True)):
            token.cancel()
            with pytest.raises(RequestCancelled):
                token.raise_if_cancelled()
    assert closed == [True]


def test_cancelled_worker_never_reports_success():
    pytest.importorskip("PySide6")
    from desktop_shell import ChatWorker

    class Chat:
        def send_message(self, **kwargs):
            worker.cancel()
            return {"status": "complete", "result": {"response": "late"}}

    worker = ChatWorker(
        operator_chat=Chat(),
        message="test",
        session_id="test",
        agent_override=None,
        analysis_mode="standard",
    )
    results = []
    worker.finished.connect(results.append)
    worker.run()
    assert results[-1]["status"] == "cancelled"
    assert "result" not in results[-1]

def test_warmup_callbacks_run_on_qt_owner_thread():
    import threading
    import time
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from blackfong_ecosystem.ui.desktop_workers import WarmupStatusBridge
    # Use QApplication (not the more restrictive QCoreApplication) so that if this
    # test creates the process-wide singleton first, later GUI tests in the same
    # pytest run can still get a real QApplication from .instance() instead of
    # crashing (Qt only allows one application instance per process).
    app=QApplication.instance() or QApplication([])
    delivered=[]
    bridge=WarmupStatusBridge(lambda value:delivered.append((value,QThread.currentThread())))
    worker=threading.Thread(target=lambda:bridge.status.emit({"state":"ready"}))
    worker.start();worker.join()
    assert not delivered
    deadline=time.monotonic()+2
    while not delivered and time.monotonic()<deadline:app.processEvents()
    assert delivered==[({"state":"ready"},app.thread())]
