"""Tool output validator — scans tool results for prompt injection before re-entering context.

When a tool (web_search, file_read, etc.) returns content, that content
re-enters the LLM context window with no validation. A poisoned web page
or document can inject instructions through tool output. This module
scans tool results using the same ContentValidator scanner that checks
archival_memory_insert content.

Usage in agent loop:
    from letta.security.tool_output_validator import validate_tool_output
    warning = await validate_tool_output(tool_name, str(result), agent)
    if warning:
        result += warning

Opt-in via agent.tool_output_validation_enabled (default False).
Fail-open: if the validator crashes, the tool result passes through unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from letta.agents.base_agent_v2 import BaseAgentV2

logger = logging.getLogger(__name__)


async def validate_tool_output(
    tool_name: str,
    tool_result: str,
    agent: "BaseAgentV2",
) -> Optional[str]:
    """Scan a tool result for prompt injection patterns. Fail-open.

    Returns a warning string if injection is detected, None if clean.
    The caller should append the warning to the tool result before
    it enters the LLM context window.

    Args:
        tool_name: Name of the tool that produced the result.
        tool_result: The tool's return value as a string.
        agent: The agent instance (for feature flag and audit logger).

    Returns:
        Warning string if injection detected, None if clean or disabled.
    """
    try:
        # Feature flag — default off (Delta unaffected)
        enabled = getattr(agent, "tool_output_validation_enabled", False)
        if not enabled:
            return None

        from letta.security.content_validator import ContentValidator

        label = ContentValidator.check(tool_result)
        if label is not None:
            from letta.security import audit_helpers as _ah

            await _ah.log_injection_detected(
                agent.audit_logger,
                agent.agent_id,
                agent.actor,
                tool_name,
                label,
                getattr(agent, "_current_step_id", None),
                getattr(agent, "_current_run_id", None),
            )
            return f"\n\n[SECURITY WARNING: Potential prompt injection detected in tool output ({label})]"

        return None

    except Exception as e:
        logger.warning(f"Tool output validation failed (fail-open): {e}")
        return None
