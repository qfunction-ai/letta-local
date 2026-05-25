from urllib.parse import urljoin

from letta.local_llm.settings.settings import get_completions_settings
from letta.local_llm.utils import post_json_auth_request

LLAMACPP_API_SUFFIX = "/completion"


def get_llamacpp_completion(endpoint, auth_type, auth_key, prompt, context_window, grammar=None):
    """See https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md for instructions on how to run the LLM web server"""
    from letta.utils import printd

    # Approximate token count: bytes / 4 (pre-call estimate)
    # Corrected by model-family factor for local models
    from letta.local_llm.token_correction import get_token_correction

    raw_bytes4_estimate = len(prompt.encode("utf-8")) // 4
    prompt_tokens = int(raw_bytes4_estimate * get_token_correction(None))  # no model param in llama.cpp API
    if prompt_tokens > context_window:
        raise Exception(f"Request exceeds maximum context length ({prompt_tokens} > {context_window} tokens)")

    # Settings for the generation, includes the prompt + stop tokens, max length, etc
    settings = get_completions_settings()
    request = settings
    request["prompt"] = prompt

    # Set grammar
    if grammar is not None:
        request["grammar"] = grammar

    if not endpoint.startswith(("http://", "https://")):
        raise ValueError(f"Provided OPENAI_API_BASE value ({endpoint}) must begin with http:// or https://")

    try:
        # NOTE: llama.cpp server returns the following when it's out of context
        # curl: (52) Empty reply from server
        URI = urljoin(endpoint.strip("/") + "/", LLAMACPP_API_SUFFIX.strip("/"))
        response = post_json_auth_request(uri=URI, json_payload=request, auth_type=auth_type, auth_key=auth_key)
        if response.status_code == 200:
            result_full = response.json()
            printd(f"JSON API response:\n{result_full}")
            result = result_full["content"]
        else:
            raise Exception(
                f"API call got non-200 response code (code={response.status_code}, msg={response.text}) for address: {URI}."
                + f" Make sure that the llama.cpp server is running and reachable at {URI}."
            )

    except:
        # TODO handle gracefully
        raise

    # Pass usage statistics back to main thread
    # These are used to compute memory warning messages
    completion_tokens = result_full.get("tokens_predicted", None)
    # Prefer server-reported tokens_evaluated over the pre-call estimate
    # Note: llama.cpp sometimes reports 0 for tokens_evaluated, which is
    # unreliable. Only use it when it's a positive number.
    server_prompt_tokens = result_full.get("tokens_evaluated", None)
    if server_prompt_tokens is not None and server_prompt_tokens > 0:
        prompt_tokens = server_prompt_tokens
    total_tokens = prompt_tokens + completion_tokens if completion_tokens is not None else None
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

    return result, usage
