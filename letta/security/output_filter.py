"""Output filter pipeline — scans agent responses before they reach the user.

Runs canary output filter and any future output filters on assistant
messages in the LettaResponse. Fail-open: if any filter crashes, the
original response is returned unmodified.

Usage in agent files:
    from letta.security import output_filter as _outf
    response = await _outf.apply_output_filters(self, response)

Streaming coverage (v0.16.37 status):
    Streaming IS filtered — the StreamingCanaryFilter with holdback buffer
    (v0.16.25) covers the SSE path; this module covers the non-streaming
    LettaResponse path. Known, deliberate gaps:
      - reasoning content is unfiltered BY DESIGN on both paths (recorded
        v0.16.25 decision; reasoning is not shown as the assistant answer)
      - tool-call chunk holdback is closed by A8 this release (v0.16.37)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letta.log import get_logger
from letta.security.canary_output_filter import apply_canary_filter_to_message

if TYPE_CHECKING:
    from letta.schemas.letta_response import LettaResponse

logger = get_logger(__name__)


async def apply_output_filters(agent, response: "LettaResponse") -> "LettaResponse":
    """Run output filters on assistant messages in the response. Fail-open.

    Iterates response.messages, applies the canary output filter to
    assistant_message types, and logs audit events for any redactions.

    The filter runs BEFORE log_message_sent() so the audit trail
    reflects the redacted content, not the raw canary value.

    Args:
        agent: The agent object. Needs canary_checker.canary_value,
               audit_logger, agent_id, and actor attributes.
        response: The LettaResponse to filter.

    Returns:
        The (possibly redacted) LettaResponse. Original is returned
        unmodified if any exception occurs.
    """
    try:
        canary_value = getattr(
            agent.canary_checker, "canary_value", None
        )
        if not canary_value:
            return response

        # Check if output filter is enabled (default: True)
        enabled = getattr(
            agent, "canary_output_filter_enabled", True
        )
        if not enabled:
            return response

        modified = False
        filtered_messages = []

        for msg in response.messages:
            filtered = apply_canary_filter_to_message(msg, canary_value)
            if filtered is not msg:
                modified = True
                # Audit: log the redaction
                from letta.security import audit_helpers as _ah
                await _ah.log_canary_output_detected(
                    agent.audit_logger,
                    agent.agent_id,
                    agent.actor,
                    None,  # step_id not available at this point
                    None,  # run_id not available at this point
                )
            else:
                # v0.16.37 Part B: skeleton-tier audit-only scan. Strict was
                # silent — check for garbled/deleted canary variants and log
                # a suspected event. Output untouched (fuzzy redaction
                # false-positives on legitimate transcripts; fuzziness stays
                # out of the output path).
                from letta.security import audit_helpers as _ah
                from letta.security.canary_output_filter import scan_skeleton_message

                _skel = scan_skeleton_message(msg, canary_value)
                if _skel is not None:
                    await _ah.log_canary_output_suspected(
                        agent.audit_logger,
                        agent.agent_id,
                        agent.actor,
                        _skel,
                        None,  # step_id not available at this point
                        None,  # run_id not available at this point
                    )
            filtered_messages.append(filtered)

        if modified:
            response.messages = filtered_messages

        return response

    except Exception as e:
        logger.warning(f"Output filter pipeline failed (fail-open): {e}")
        return response
