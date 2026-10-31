"""Cross-process admission for one active conversational turn per session."""
from contextlib import contextmanager
from hashlib import sha256

from .config import paths
from .earth.lease import RuntimeLease


class SessionBusy(RuntimeError):
    pass


@contextmanager
def session_turn(session_id):
    key = sha256(str(session_id).encode("utf-8")).hexdigest()
    try:
        lease = RuntimeLease(paths.data_root / "chat-turns" / key)
    except RuntimeError as exc:
        raise SessionBusy("This session already has a pending turn; wait or cancel it before sending again.") from exc
    try:
        yield
    finally:
        lease.close()
