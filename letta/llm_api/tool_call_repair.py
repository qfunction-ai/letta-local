"""Tool call repair — handles malformed tool call arguments.

When a model produces a tool call with unparseable JSON arguments,
this module decides whether to retry or give up. Extracted from
the V3 agent loop to keep the HIGH-activity shared file clean.

Usage in agent files:
    from letta.llm_api import tool_call_repair as _tcr
    retry_spec = _tcr.handle_malformed_args(self, call_id, name, raw_args_str, args, active_llm_config)
    if retry_spec:
        exec_specs.append(retry_spec)
        continue
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from letta.schemas.llm_config import LLMConfig


def handle_malformed_args(
    agent,
    call_id: str,
    tool_name: str,
    raw_args_str: str,
    parsed_args: dict,
    active_llm_config: "LLMConfig",
) -> Optional[Dict[str, Any]]:
    """Handle a tool call with malformed JSON arguments.

    If the model supports tool call retry (via constraints.tool_call_retry_count),
    inject a structured error so the model can retry with valid JSON. If the retry
    limit is exceeded, reset the counter and return None (proceed with empty args).

    Args:
        agent: The agent object (needs _tool_call_retry_count attribute and logger).
        call_id: The tool call ID.
        tool_name: The tool name.
        raw_args_str: The raw, unparseable arguments string.
        parsed_args: The result of parsing (empty dict if failed).
        active_llm_config: The active LLM config with constraints.

    Returns:
        An exec_spec dict if retry applies (caller should append and continue),
        None if no retry (caller should proceed normally).
    """
    # Only retry if parsing failed and retry is configured
    if parsed_args or not raw_args_str:
        return None

    constraints = getattr(active_llm_config, "constraints", None)
    if not constraints or not constraints.tool_call_retry_count or constraints.tool_call_retry_count <= 0:
        return None

    retry_count = getattr(agent, "_tool_call_retry_count", 0)
    max_retries = constraints.tool_call_retry_count

    if retry_count < max_retries:
        agent._tool_call_retry_count = retry_count + 1
        return {
            "id": call_id,
            "name": tool_name,
            "args": {},
            "violated": False,
            "error": (
                f"Tool call JSON parsing failed. Your tool call arguments were malformed JSON "
                f"that could not be repaired. Please retry the tool call with valid JSON. "
                f"Raw arguments: {raw_args_str[:200]}"
            ),
        }
    else:
        agent._tool_call_retry_count = 0
        agent.logger.warning(
            f"Tool call retry limit ({max_retries}) exceeded for {tool_name}. "
            f"Proceeding with empty args."
        )
        return None
