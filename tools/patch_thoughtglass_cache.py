from pathlib import Path

path = Path("blackfong_ecosystem/operational_interfaces.py")
text = path.read_text(encoding="utf-8")
old = '''            snapshot = self._build_cognition_snapshot(
                session_id=gesture.get("session_id"),
                request_id=gesture.get("request_id"),
                layer=self.cognition_focus.get("layer", "live_brain"),
            )
            self.cognition_router.emit_focus(snapshot["focus"])
'''
new = '''            snapshot = self._build_cognition_snapshot(
                session_id=gesture.get("session_id"),
                request_id=gesture.get("request_id"),
                layer=self.cognition_focus.get("layer", "live_brain"),
                use_cache=False,
            )
            self._cognition_snapshot_cache = None
            self.cognition_router.emit_focus(snapshot["focus"])
'''

if new in text:
    raise SystemExit(0)
if old not in text:
    raise SystemExit("Expected cognition gesture snapshot block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
