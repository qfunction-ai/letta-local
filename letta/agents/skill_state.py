"""Skill state tracking — parse <skill_state> metadata and enforce required tools."""

import json
import logging
import re

from letta.helpers.tool_rule_solver import ToolRulesSolver
from letta.schemas.letta_message_content import TextContent, ToolCallContent
from letta.schemas.message import Message, MessageRole
from letta.schemas.tool_rule import RequiredBeforeExitToolRule

logger = logging.getLogger(__name__)

_SKILL_STATE_RE = re.compile(r'<skill_state>\s*(.*?)\s*</skill_state>', re.DOTALL)


def parse_and_strip_skill_state(
    messages: list[Message],
    tool_rules_solver: ToolRulesSolver,
) -> None:
    """Parse <skill_state> block from user messages, add RequiredBeforeExitToolRule
    entries to the solver, and strip the block from message content so the model
    never sees it.

    Called once at the start of the agent loop, before the first LLM call.
    The solver is per-instance (per-request), so rules are automatically scoped
    to this run. Messages are modified in place.

    Args:
        messages: List of Message objects (in-context + input messages).
            Modified in place — <skill_state> blocks are stripped from content.
        tool_rules_solver: The tool rules solver for this run. Rules are
            appended in place.
    """
    for msg in messages:
        if msg.role != MessageRole.user:
            continue

        # Extract text content
        if isinstance(msg.content, list):
            text = "".join(c.text for c in msg.content if isinstance(c, TextContent))
        elif isinstance(msg.content, str):
            text = msg.content
        else:
            continue

        match = _SKILL_STATE_RE.search(text)
        if not match:
            continue

        # Strip the <skill_state> block from the message content first
        # (do this regardless of JSON parsing success)
        stripped_text = _SKILL_STATE_RE.sub('', text)
        if isinstance(msg.content, list):
            # Rebuild content list with stripped text
            # Preserve non-text content, replace text content
            new_content = []
            text_replaced = False
            for c in msg.content:
                if isinstance(c, TextContent) and not text_replaced:
                    new_content.append(TextContent(text=stripped_text))
                    text_replaced = True
                elif isinstance(c, TextContent):
                    new_content.append(c)  # keep additional text parts
                else:
                    new_content.append(c)  # preserve non-text content
            if not text_replaced and stripped_text:
                new_content.append(TextContent(text=stripped_text))
            msg.content = new_content
        elif isinstance(msg.content, str):
            msg.content = stripped_text

        # Parse the JSON block and add rules (best effort)
        try:
            entries = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning("Failed to parse <skill_state> JSON block")
            break  # Still stripped, just no rules added

        # Add RequiredBeforeExitToolRule for each declared tool
        existing = {r.tool_name for r in tool_rules_solver.required_before_exit_tool_rules}
        for entry in entries:
            skill_name = entry.get("skill_name", "unknown")
            for tool_name in entry.get("required_tools", []):
                if tool_name not in existing:
                    tool_rules_solver.required_before_exit_tool_rules.append(
                        RequiredBeforeExitToolRule(tool_name=tool_name)
                    )
                    existing.add(tool_name)
                    logger.info(
                        f"Added RequiredBeforeExitToolRule for '{tool_name}' "
                        f"from skill '{skill_name}'"
                    )

        # Pre-populate previously_called_tools by scanning message history
        # for tool calls from previous messages in this skill session.
        required_names = {r.tool_name for r in tool_rules_solver.required_before_exit_tool_rules}
        if required_names:
            for hist_msg in messages:
                # Check OpenAI-format tool_calls on assistant messages
                if hist_msg.tool_calls:
                    for tc in hist_msg.tool_calls:
                        tool_name = tc.function.name
                        if tool_name in required_names:
                            tool_rules_solver.mark_previously_called(tool_name)
                # Check ToolCallContent in message content
                if hist_msg.content and isinstance(hist_msg.content, list):
                    for c in hist_msg.content:
                        if isinstance(c, ToolCallContent):
                            if c.name in required_names:
                                tool_rules_solver.mark_previously_called(c.name)

            if tool_rules_solver.previously_called_tools:
                logger.info(
                    f"Pre-registered previously called tools: {tool_rules_solver.previously_called_tools}"
                )

        break  # Only process the first message with skill_state
