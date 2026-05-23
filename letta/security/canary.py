"""Output canary checks — defense-in-depth against prompt exfiltration.

The canary is a random string embedded in the system prompt as a
read-only memory block. If the canary appears in a tool call's
arguments, the agent is trying to exfiltrate the system prompt.
Block the call and log it.

The protection is two-layered:
- Layer 1: read_only=True on the canary block prevents modification
  via core_memory_replace/core_memory_append (enforced by existing
  tool code).
- Layer 2: CanaryChecker scans tool arguments before execution. If
  the canary string appears in any argument, the call is blocked.
  This catches exfiltration through any tool.

The canary check is a heuristic, not a guarantee. It catches naive
exfiltration (the agent includes the full canary string in a tool
argument). Sophisticated attacks that encode or split the canary
will bypass it. This is defense-in-depth — the primary defense is
tool call policies (requiring approval for sensitive tools). The
canary raises the bar for the most common attack pattern.
"""

import json
import uuid
from typing import Optional


class CanaryChecker:
    """Scans tool arguments for the canary string.

    The canary is a random string embedded in the system prompt.
    If it appears in a tool call's arguments, the agent is trying
    to exfiltrate the system prompt.

    Usage:
        checker = CanaryChecker("CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789")
        detected = checker.check({"message": "my secret is CANARY-a7f3..."})  # True
        clean = checker.check({"message": "hello"})  # False
    """

    CANARY_PREFIX = "CANARY-"
    CANARY_BLOCK_LABEL = "__canary__"
    CANARY_BLOCK_DESCRIPTION = "Security canary - do not modify or share this value"

    def __init__(self, canary_value: Optional[str] = None):
        self.canary_value = canary_value

    def check(self, tool_args: dict) -> bool:
        """Check if the canary string appears in any tool argument.

        Serializes tool_args to JSON and checks for the canary string
        as a substring. The CANARY- prefix makes false positives
        negligible.

        Args:
            tool_args: The tool call arguments as a dict.

        Returns:
            True if the canary is detected (exfiltration attempt).
            False if the canary is not found (clean).
        """
        if not self.canary_value:
            return False
        args_str = json.dumps(tool_args)
        return self.canary_value in args_str

    def update_canary(self, canary_value: str) -> None:
        """Update the canary value (e.g., after loading from DB)."""
        self.canary_value = canary_value

    @classmethod
    def generate_canary_value(cls) -> str:
        """Generate a new canary value.

        Format: CANARY-{uuid} — the prefix makes false positives
        negligible since "CANARY-" is unlikely to appear in normal
        tool arguments.
        """
        return f"{cls.CANARY_PREFIX}{uuid.uuid4()}"
