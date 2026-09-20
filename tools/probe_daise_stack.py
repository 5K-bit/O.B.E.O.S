"""Real shared-chat smoke probe with disposable, isolated runtime authorities.

Run with python -m tools.probe_daise_stack. This is not UI acceptance and does
not claim to test installed knowledge retrieval from the empty evaluation store.
"""
import json
import argparse
import os
from pathlib import Path
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true", help="Exercise the actual Qt ChatPane offscreen")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = repo / "data" / "evaluations" / "daise-recovery" / ("stack-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    sys.path.insert(0, str(repo))
    os.chdir(root)
    os.environ.update({
        "BLACKFONG_DAISE_BACKEND": "ollama",
        "BLACKFONG_DAISE_MODEL": "qwen2.5-coder:7b",
        "BLACKFONG_DAISE_NUM_CTX": "2048",
        "BLACKFONG_DAISE_NUM_PREDICT": "180",
        "BLACKFONG_KNOWLEDGE_BUNDLE_PATH": str(root / "empty-seed"),
    })
    from blackfong_ecosystem import config
    config.paths = config.Paths.from_root(root)
    print(json.dumps({"evaluation_root": str(root), "stage": "initializing"}), flush=True)
    from blackfong_ecosystem.operator_chat import OperatorChatService
    from blackfong_ecosystem.unified_orchestrator import unified_orchestrator
    service = OperatorChatService(orchestrator=unified_orchestrator)
    session = str(uuid.uuid4())
    pane = None
    if args.native:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from desktop_shell import ChatPane
        app = QApplication.instance() or QApplication([])
        pane = ChatPane(operator_chat=service, memory_service=service.memory_service)
        pane.session_id = session
        pane.resize(1000, 800)
        pane.show()
        app.processEvents()
    prompts = [
        "Good evening. I had a difficult day. Can we talk?",
        "I missed the bus before it started raining. What happened first?",
        "Actually it was a train, not a bus. What did I miss?",
    ]
    if args.native:
        prompts += [
            "Why does rain sometimes smell pleasant? Answer in two sentences.",
            "What is that smell called?",
            "What does D.A.I.S.E. stand for?",
            "Did you actually inspect my files during this conversation?",
            "I am planning a fictional picnic on Tuesday at Cedar Park. Remember that for this chat.",
            "Change the picnic to Thursday, same place. Summarize the plan.",
            "Give me exactly two vegetarian picnic foods as bullets.",
            "Which of those would travel better without spilling?",
            "Explain why the Moon has phases, in one sentence.",
            "Is that caused by Earth's shadow?",
            "Back to the picnic: what day and place did we settle on?",
            "For this fictional plan, the backup venue is Pine Hall. What are the two venues now?",
            "This is a conversational test, not training. Tell me a short joke instead of starting a quiz.",
            "Explain the joke briefly.",
            "Are you doing work for me between these messages?",
            "What do you know for certain about my real location?",
            "Summarize only the fictional picnic decisions we made. Do not invent details.",
        ]
    for index, prompt in enumerate(prompts, 1):
        started = time.perf_counter()
        tokens = []
        first = []
        def on_token(token):
            if not first:
                first.append((time.perf_counter() - started) * 1000)
            tokens.append(token)
        ui_metrics = {}
        if pane is not None:
            completed = []
            pane.chat_finished.connect(completed.append)
            pane.input.setPlainText(prompt)
            pane.send_message()
            pane._worker.token_received.connect(on_token)
            deadline = time.monotonic() + 180
            last_tick = time.monotonic()
            max_gap = 0.0
            while not completed and time.monotonic() < deadline:
                app.processEvents()
                now = time.monotonic()
                max_gap = max(max_gap, now - last_tick)
                last_tick = now
                time.sleep(0.01)
            if not completed:
                pane._worker.cancel()
                pane._worker.wait(10000)
                raise RuntimeError("Native turn exceeded evaluation deadline")
            app.processEvents()
            result = completed[0]
            pane.chat_finished.disconnect(completed.append)
            pane._worker.wait(10000)
            response = str((result.get("result") or {}).get("response") or "")
            ui_metrics = {"max_event_loop_gap_ms": max_gap * 1000,
                          "full_response_rendered": bool(response) and response in pane.transcript.toPlainText(),
                          "status_label": pane.status_label.text()}
        else:
            result = service.send_message(message=prompt, session_id=session,
                                      analysis_mode="standard", stream_callback=on_token,
                                      include_messages=True, include_trace=True,
                                      context={"input_origin": "evaluation"})
        record = {"turn": index, "prompt": prompt, "result": result,
                  "first_token_ms": first[0] if first else None,
                  "total_ms": (time.perf_counter() - started) * 1000,
                  "stream_text": "".join(tokens), "ui": ui_metrics}
        with (root / "turns.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, default=str) + "\n")
        print(json.dumps({"turn": index, "status": result.get("status"),
                          "result": result.get("result"), "total_ms": record["total_ms"]}, default=str), flush=True)


if __name__ == "__main__":
    main()
