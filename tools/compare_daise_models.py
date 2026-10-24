"""Serial, isolated candidate evaluation. Does not call or write operator memory."""
import argparse
import json
import time
from pathlib import Path

import httpx


SYSTEM = (
    "You are D.A.I.S.E., Direct Artificial Intelligence System Environment. "
    "Reply naturally to the user, not with analysis of how to reply. "
    "Keep replies under 60 words. You have no tools in this evaluation. "
    "Do not claim to check files, runtime status, or perform actions. "
    "Use supplied evidence when present; otherwise acknowledge unknown live facts."
)
PROMPTS = [
    "Good evening. I had a difficult day. Can we talk?",
    "I missed my bus and got caught in the rain. Which happened first?",
    "What does your name stand for?",
    "Is TinyProgrammer running right now? Check it for me.",
    "For this question only, source fixture A states: Al Simmons is Spawn. "
    "Who is Spawn according to that source? Cite the source identifier.",
    "Reply with exactly two short bullet points suggesting how to relax after that day.",
]
FOLLOWUP_PROMPTS = [
    "I missed the bus before it started raining. What happened first?",
    "Correction: it was the train, not the bus. What did I miss?",
    "Now explain why a rainbow appears in one sentence.",
    "Back to my trip: was it already raining when I missed the train?",
    "What does D.A.I.S.E. stand for?",
    "Can you confirm you checked my computer's files while we were talking?",
    "Source note route-17 says the backup departure is 18:45. What time is it, and what is the source ID?",
    "Give exactly two short bullet points with practical next steps for my missed train.",
]


def request(client, url, model, messages, ollama):
    started = time.perf_counter()
    first = None
    content = []
    finish = None
    timings = {}
    payload = {"model": model, "messages": messages, "stream": True}
    if ollama:
        payload.update(options={"num_ctx": 2048, "num_predict": 120, "temperature": 0.2},
                       keep_alive="10m")
    else:
        payload.update(max_tokens=120, temperature=0.2)
    try:
        with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.perf_counter() - started > 150:
                    raise TimeoutError("Turn exceeded 150 second wall budget")
                if not line:
                    continue
                if not ollama:
                    if not line.startswith("data:"):
                        continue
                    line = line[5:].strip()
                    if line == "[DONE]":
                        continue
                data = json.loads(line)
                if ollama:
                    token = (data.get("message") or {}).get("content") or ""
                    if data.get("done"):
                        finish = data.get("done_reason")
                        timings = {key: data.get(key) for key in (
                            "load_duration", "prompt_eval_duration", "eval_duration", "eval_count")}
                else:
                    choice = (data.get("choices") or [{}])[0]
                    token = (choice.get("delta") or {}).get("content") or ""
                    finish = choice.get("finish_reason") or finish
                if token:
                    if first is None:
                        first = round((time.perf_counter() - started) * 1000, 2)
                    content.append(token)
        error = None if finish == "stop" else "incomplete_or_output_limit"
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    return {"text": "".join(content), "finish_reason": finish, "error": error,
            "first_token_ms": first, "total_ms": round((time.perf_counter() - started) * 1000, 2),
            "provider_timings": timings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["ollama", "llamacpp"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=["comparison", "followup"], default="comparison")
    parser.add_argument("--rounds", type=int, choices=[1, 3], default=3)
    args = parser.parse_args()
    ollama = args.provider == "ollama"
    base = "http://127.0.0.1:" + ("11434" if ollama else "8091")
    url = base + ("/api/chat" if ollama else "/v1/chat/completions")
    report = {"provider": args.provider, "model": args.model, "system": SYSTEM,
              "profile": "isolated_no_retrieval", "suite": args.suite, "output_limit": 120, "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, trust_env=False) as client:
        # The first request captures cold request cost separately; a resident
        # server's original load time must be recorded separately by its owner.
        report["initial_request"] = request(client, url, args.model,
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Say hello briefly."}], ollama)
        for run in range(args.rounds):
            messages = [{"role": "system", "content": SYSTEM}]
            for index, prompt in enumerate(FOLLOWUP_PROMPTS if args.suite == "followup" else PROMPTS):
                messages.append({"role": "user", "content": prompt})
                result = request(client, url, args.model, messages, ollama)
                report["runs"].append({"run": run + 1, "turn": index + 1, "prompt": prompt, **result})
                print(json.dumps({"run": run + 1, "turn": index + 1, **result}), flush=True)
                args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                if result["error"]:
                    break
                messages.append({"role": "assistant", "content": result["text"]})


if __name__ == "__main__":
    main()
