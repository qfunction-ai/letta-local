#!/usr/bin/env python3
"""B-13 witness: runs the README quick-start code path against a live server.

The README's step-5 snippet, parameterized by env so the smoke rig can run it
against its own server/model values. Validates the two things B-13 fixed:
  - password auth (api_key) instead of an anonymous client (401 under secure mode)
  - a LOCAL embedding model instead of the removed upstream cloud preset

Exit 0 on success; non-zero with a reason line on failure.
"""
import os
import sys

from letta_client import Letta

BASE_URL = os.environ.get("LETTA_BASE_URL", "http://localhost:8383")
PASSWORD = os.environ.get("LETTA_SERVER_PASSWORD", "")
MODEL = os.environ.get("SMOKE_MODEL", "ollama/phi3:mini")
EMBEDDING = os.environ.get("SMOKE_EMBEDDING", "ollama/embeddinggemma")

if not PASSWORD:
    print("FAIL: LETTA_SERVER_PASSWORD not set — quick start requires it (secure mode)")
    sys.exit(1)

client = Letta(base_url=BASE_URL, api_key=PASSWORD)

agent = client.agents.create(
    name="quickstart-witness",
    model=MODEL,
    embedding=EMBEDDING,
)
print(f"AGENT_CREATED: {agent.id}")

response = client.agents.messages.create(
    agent_id=agent.id,
    messages=[{"role": "user", "content": "Hello! Reply with the single word: ready"}],
)
# Not every message type carries content (ReasoningMessage doesn't) — read defensively.
texts = []
for m in response.messages:
    content = getattr(m, "content", None)
    if not content:
        continue
    for c in content:
        if getattr(c, "type", "") == "text" and getattr(c, "text", None):
            texts.append(c.text)
print(f"MESSAGE_RECEIVED: {len(response.messages)} messages, {len(texts)} text chunks")
if not texts:
    print("FAIL: no assistant text in response")
    sys.exit(1)

client.agents.delete(agent.id)
print("QUICKSTART_WITNESS_PASS")
