"""A minimal Ollama client, written by hand: one POST to /api/chat, no framework, no streaming."""

from __future__ import annotations

import json
import os
import urllib.request

# The Ollama server may run elsewhere (here: inside WSL, reached through localhost).
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# On a CPU a single turn can take minutes, and a request may wait behind another project's.
TIMEOUT_SECONDS = 1800


def chat(model: str, messages: list[dict], tools: list[dict], options: dict,
         format: dict | None = None) -> dict:
    """Send the whole conversation and the tool schemas; return Ollama's response as a dict.

    With `format` (a JSON schema), Ollama constrains the answer to follow that schema.
    """
    body = {"model": model, "messages": messages, "tools": tools, "options": options,
            "stream": False}
    if format is not None:
        body["format"] = format
    request = urllib.request.Request(f"{OLLAMA_URL}/api/chat",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read())
