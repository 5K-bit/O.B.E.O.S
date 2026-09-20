"""Isolated real-model probe; never invokes operator memory/profile learning."""
import json
import time

from blackfong_ecosystem.llamacpp_process import LlamaCppServerManager
from blackfong_ecosystem.llamacpp_backend import LlamaCppChatBackend


def main():
    manager = LlamaCppServerManager(on_status=lambda state: print(json.dumps(state), flush=True))
    manager.start()
    try:
        deadline = time.monotonic() + 130
        while not manager.is_ready() and time.monotonic() < deadline:
            if manager.result.get("status") in {"error", "failed", "unconfigured", "disabled"}:
                raise RuntimeError(manager.result)
            time.sleep(1)
        if not manager.is_ready():
            raise RuntimeError("Readiness deadline exceeded")
        messages = [{"role": "system", "content": (
            "You are D.A.I.S.E., Direct Artificial Intelligence System Environment. "
            "Answer naturally and briefly. Do not claim tool access.")},
            {"role": "user", "content": "Good evening. I have had a long day. Can we talk for a minute?"}]
        result = LlamaCppChatBackend().generate_stream(
            messages, num_predict=100, timeout=90,
            on_token=lambda token: print(token, end="", flush=True))
        print("\nRESULT", json.dumps({"text": result.text, "metadata": result.metadata}), flush=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
