"""Compare direct HTTP and shared adapter with identical isolated inputs."""
import json

import httpx

from blackfong_ecosystem.chat_backends import OllamaChatBackend
from blackfong_ecosystem.daise_identity import IDENTITY_AND_TRUTH_PROMPT


def main():
    model = "qwen2.5-coder:3b"
    messages = [{"role": "system", "content": IDENTITY_AND_TRUTH_PROMPT},
                {"role": "user", "content": "I missed the bus before it started raining. What happened first?"}]
    options = {"num_ctx": 2048, "num_predict": 80, "temperature": 0.2}
    with httpx.Client(timeout=90, trust_env=False) as client:
        response = client.post("http://127.0.0.1:11434/api/chat", json={
            "model": model, "messages": messages, "stream": False, "keep_alive": "5m", "options": options})
        response.raise_for_status()
        print(json.dumps({"path": "direct", "response": response.json()}), flush=True)
    backend = OllamaChatBackend(model=model, timeout=90, trust_env=False)
    result = backend.generate(messages, **options)
    print(json.dumps({"path": "shared_adapter", "text": result.text, "metadata": result.metadata}), flush=True)


if __name__ == "__main__":
    main()
