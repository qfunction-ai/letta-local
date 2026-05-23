"""Tool call policies — the security boundary between LLM tool decisions
and server-side execution.

The PolicyChecker sits between the model's tool decision and the server's
execution. It returns one of three actions for each tool call:

- ALLOW: the tool call is permitted. Fall through to the existing
  ToolRulesSolver for workflow checks.
- DENY: the tool call is rejected. Log to the audit log and return an
  error to the agent.
- REQUIRE_APPROVAL: the tool call requires human approval. Route to the
  existing approval system (same code path as RequiresApprovalToolRule).

The PolicyChecker is separate from ToolRulesSolver. They answer different
questions:

- ToolRulesSolver: "Which tools are available at this step of the workflow?"
  (workflow control)
- PolicyChecker: "Is this tool call allowed by the security policy?"
  (security boundary)

The PolicyChecker runs FIRST. If it denies or requires approval, the
ToolRulesSolver check is never reached. This prevents double approval
when both the policy and the workflow require approval for the same tool.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PolicyAction(str, Enum):
    """The action to take for a tool call based on the security policy."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ToolCallPolicy(BaseModel):
    """Per-agent security policy for tool calls.

    Two lists: denied_tools (always deny) and approval_required_tools
    (always require human approval). Tools not in either list are
    allowed by default.

    The default is permissive (empty policy = allow all). This is
    backward-compatible — existing agents continue to work without
    any policy configuration. Organizations that want a secure-by-
    default posture should set policies on all agents.
    """

    denied_tools: list[str] = Field(
        default_factory=list,
        description="Tools that are always denied by the security policy.",
    )
    approval_required_tools: list[str] = Field(
        default_factory=list,
        description="Tools that always require human approval before execution.",
    )


class PolicyChecker:
    """Checks tool calls against the per-agent security policy.

    Usage:
        checker = PolicyChecker(ToolCallPolicy(
            denied_tools=["web_search"],
            approval_required_tools=["archival_memory_insert"],
        ))
        action = checker.check("web_search")       # DENY
        action = checker.check("archival_memory_insert")  # REQUIRE_APPROVAL
        action = checker.check("core_memory_append")      # ALLOW
    """

    def __init__(self, policy: Optional[ToolCallPolicy] = None):
        self.policy = policy or ToolCallPolicy()

    def check(self, tool_name: str) -> PolicyAction:
        """Check a tool call against the security policy.

        Args:
            tool_name: The name of the tool being called.

        Returns:
            PolicyAction: ALLOW, DENY, or REQUIRE_APPROVAL.
        """
        if tool_name in self.policy.denied_tools:
            return PolicyAction.DENY
        if tool_name in self.policy.approval_required_tools:
            return PolicyAction.REQUIRE_APPROVAL
        return PolicyAction.ALLOW

    def update_policy(self, policy: ToolCallPolicy) -> None:
        """Update the policy (e.g., after loading from DB)."""
        self.policy = policy
