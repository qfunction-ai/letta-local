#!/usr/bin/env python3
"""Measure token correction ratio for a local model.

Sends a representative prompt to the model via Ollama's OpenAI-compatible
endpoint, reads back server-reported prompt_tokens, and computes the ratio:

    server_prompt_tokens / (len(prompt.encode("utf-8")) // 4)

This ratio corrects the naive bytes/4 token estimate used by
letta.local_llm.token_correction.

Usage:
    python scripts/measure_token_correction.py --model mistral:7b --endpoint http://localhost:11434
    python scripts/measure_token_correction.py --model llama3:8b --endpoint http://localhost:11434
"""

import argparse
import json
import sys
import urllib.request


def build_representative_prompt() -> str:
    """Build a prompt that resembles a real agent request.

    Includes a system message, tool definitions, and a user message.
    Roughly matches the token distribution of a typical agent step.
    """
    system_msg = (
        "You are a helpful assistant with access to tools. "
        "Use tools to answer questions when appropriate. "
        "Always respond with a tool call when the user asks you to do something."
    )

    tool1 = json.dumps({
        "name": "send_message",
        "description": "Send a message to the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to send."}
            },
            "required": ["message"],
        },
    })

    tool2 = json.dumps({
        "name": "archival_memory_search",
        "description": "Search archival memory for a specific term.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you're looking for."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter tags."},
                "top_k": {"type": "integer", "description": "Max results."},
            },
            "required": ["query"],
        },
    })

    tool3 = json.dumps({
        "name": "web_search",
        "description": "Search the web for relevant content.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "num_results": {"type": "integer", "description": "Number of results."},
            },
            "required": ["query"],
        },
    })

    user_msg = "What can you tell me about the history of computing?"

    # Assemble in chat format
    prompt = json.dumps({
        "model": "placeholder",
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "tools": [
            {"type": "function", "function": json.loads(tool1)},
            {"type": "function", "function": json.loads(tool2)},
            {"type": "function", "function": json.loads(tool3)},
        ],
    })

    return prompt


def measure_token_correction(model: str, endpoint: str) -> float:
    """Send a request to the model and compute the correction ratio.

    Args:
        model: Model name (e.g. "mistral:7b").
        endpoint: Ollama endpoint URL (e.g. "http://localhost:11434").

    Returns:
        Correction ratio (server_prompt_tokens / bytes4_estimate).
    """
    # Build the request body
    system_msg = "You are a helpful assistant. Answer questions concisely."
    user_msg = "What is 2 + 2?"

    # Include tool definitions to match real agent traffic
    tools = [
        {
            "type": "function",
            "function": {
                "name": "send_message",
                "description": "Send a message to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "The message to send."}
                    },
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for relevant content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."},
                        "num_results": {"type": "integer", "description": "Number of results."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "archival_memory_search",
                "description": "Search archival memory for a specific term.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What you're looking for."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter tags."},
                        "top_k": {"type": "integer", "description": "Max results."},
                    },
                    "required": ["query"],
                },
            },
        },
    ]

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "tools": tools,
        "stream": False,
        "max_tokens": 1,  # We only care about prompt_tokens
    })

    # Compute the bytes/4 estimate from the full request body
    bytes4_estimate = len(body.encode("utf-8")) // 4

    # Send the request
    url = f"{endpoint}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"Sending request to {url}")
    print(f"Model: {model}")
    print(f"Request body: {len(body)} bytes")
    print(f"bytes/4 estimate: {bytes4_estimate}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"ERROR: Could not connect to {url}: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON response: {e}", file=sys.stderr)
        sys.exit(1)

    usage = response.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)

    if prompt_tokens == 0:
        print("ERROR: Server did not return prompt_tokens in usage", file=sys.stderr)
        print(f"Response: {json.dumps(response, indent=2)}", file=sys.stderr)
        sys.exit(1)

    ratio = prompt_tokens / bytes4_estimate if bytes4_estimate > 0 else 0

    print(f"Server-reported prompt_tokens: {prompt_tokens}")
    print(f"Correction ratio: {ratio:.4f}")
    print()
    print("Suggested update for token_correction.py:")
    print(f'    "{model.split(":")[0]}": {ratio:.4f},  # measured by scripts/measure_token_correction.py')

    return ratio


def main():
    parser = argparse.ArgumentParser(description="Measure token correction ratio for a local model.")
    parser.add_argument("--model", required=True, help="Model name (e.g. mistral:7b)")
    parser.add_argument("--endpoint", default="http://localhost:11434", help="Ollama endpoint URL")
    args = parser.parse_args()

    measure_token_correction(args.model, args.endpoint)


if __name__ == "__main__":
    main()
