"""Tool call policies — the security boundary between LLM tool decisions
and server-side execution.

Agent OS-compatible schema. Field names, types, and YAML format match
Microsoft's Agent Governance Toolkit so policy files are interchangeable.
We own the evaluator — no Agent OS dependency.

The PolicyChecker sits between the model's tool decision and the server's
execution. It returns a PolicyDecision for each tool call:

- ALLOW: the tool call is permitted. Fall through to the existing
  ToolRulesSolver for workflow checks.
- DENY: the tool call is rejected. Log to the audit log and return an
  error to the agent.
- REQUIRE_APPROVAL: the tool call requires human approval. Route to the
  existing approval system (same code path as RequiresApprovalToolRule).
  In V3 (no approval wiring), this is treated as DENY (fail-closed).
- AUDIT: the tool call is permitted but logged. Policy metadata attaches
  to the tool_executed event.

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

from __future__ import annotations

try:
    import regex as re
except ImportError:
    import re  # fallback — Layer 2 validation still rejects pathological patterns
from enum import Enum
from typing import Any, Dict, List, Optional
import hashlib
import json

import yaml
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Regex safety — reject patterns that cause catastrophic backtracking
# ---------------------------------------------------------------------------

# Nested quantifiers: (a+)+, (a*)*, (a+)*, etc. These are the classic
# ReDoS patterns. The regex module (Layer 1) handles them in linear time,
# but there's no reason to accept them — they're almost always a mistake.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*?][^)]*\)[+*?]")


def validate_regex_pattern(pattern: str) -> None:
    """Reject regex patterns likely to cause catastrophic backtracking.

    Called at the API boundary and in PolicyRule.model_post_init.
    Raises ValueError if the pattern is rejected.
    """
    if not pattern:
        return
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError(f"Regex pattern rejected: nested quantifiers cause catastrophic backtracking. Pattern: {pattern!r}")
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}") from e


# ---------------------------------------------------------------------------
# Schema — Agent OS-compatible field names and types
# ---------------------------------------------------------------------------


class PolicyAction(str, Enum):
    """The action to take for a tool call based on the security policy."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"  # fork-specific
    AUDIT = "audit"  # log but allow (matches Agent OS)


class PolicyOperator(str, Enum):
    """Comparison operators for policy conditions.

    Matches Agent OS PolicyOperator exactly.
    """

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    MATCHES = "matches"  # regex
    CONTAINS = "contains"  # substring
    CONTAINS_SECRET = "contains_secret"  # entropy + regex check on tool args
    CONTAINS_INJECTION = "contains_injection"  # prompt injection pattern check on tool args


class PolicyCondition(BaseModel):
    """A condition that must match for a policy rule to fire.

    The ``field`` supports dot-path resolution (e.g., ``tool_args.query``)
    which Agent OS does not support. Flat field names (``tool_name``,
    ``tool_call_count``) are Agent OS-compatible.
    """

    field: str = Field(description="Dot-path: 'tool_name', 'tool_args.query', 'tool_call_count'")
    operator: PolicyOperator = Field(description="Comparison operator")
    value: Any = Field(description="Value to compare against")


class PolicyRule(BaseModel):
    """A single policy rule with a condition, action, and priority.

    Matches Agent OS PolicyRule schema. The ``pattern`` field is an
    Agent OS feature — regex matched against the action's string
    representation (e.g., SQL query text).
    """

    name: str = Field(description="Human-readable rule name")
    condition: PolicyCondition = Field(description="When this rule fires")
    action: PolicyAction = Field(description="What to do when the rule fires")
    priority: int = Field(default=0, description="Higher priority rules override lower ones")
    message: Optional[str] = Field(default=None, description="Human-readable explanation")
    pattern: Optional[str] = Field(
        default=None,
        description="Regex pattern for action params (Agent OS compatible)",
    )

    # Cached compiled regex — populated by model_post_init
    _compiled_pattern: Optional[re.Pattern] = None
    _compiled_condition_pattern: Optional[re.Pattern] = None

    def model_post_init(self, __context: Any) -> None:
        """Compile regex patterns after model initialization.

        Rejects patterns with nested quantifiers (ReDoS prevention).
        Invalid regex patterns raise ValueError.
        """
        if self.pattern is not None:
            validate_regex_pattern(self.pattern)
            self._compiled_pattern = re.compile(self.pattern)
        if self.condition.operator == PolicyOperator.MATCHES and isinstance(self.condition.value, str):
            validate_regex_pattern(self.condition.value)
            self._compiled_condition_pattern = re.compile(self.condition.value)


class PolicyDefaults(BaseModel):
    """Default policy settings when no rule matches.

    Matches Agent OS PolicyDefaults schema.
    """

    action: PolicyAction = Field(default=PolicyAction.ALLOW, description="Default action when no rule matches")
    max_tool_calls: Optional[int] = Field(default=None, description="Global per-run tool call limit")
    max_tokens: Optional[int] = Field(default=None, description="Token limit (future)")
    timeout_seconds: Optional[int] = Field(default=None, description="Timeout limit (future)")

    # Unknown keys must fail loudly (same rationale as LoopDetectionConfig).
    model_config = ConfigDict(extra="forbid")


class LoopDetectionConfig(BaseModel):
    """Configuration for tool loop detection.

    Detects when the same tool is called with the same arguments
    repeatedly within a sliding window of recent calls. This catches
    agents stuck in loops that would not be caught by per-tool rate
    limits alone (e.g., calling web_search with the same query 5 times
    when the per-tool limit is 50).
    """
    enabled: bool = Field(default=False, description="Enable tool loop detection.")
    window: int = Field(default=5, ge=2, description="Number of recent calls to examine.")
    threshold: int = Field(default=3, ge=2, description="Number of identical calls within the window that triggers a denial.")

    # Unknown keys (e.g. window_size) must fail loudly, not silently
    # match nothing - a silent mismatch here burned an Epsilon audit cycle.
    model_config = ConfigDict(extra="forbid")


class ToolCallPolicy(BaseModel):
    """Per-agent security policy for tool calls.

    Backwards-compatible with the existing two-list model (denied_tools,
    approval_required_tools). New fields add argument-level rules,
    rate limiting, and Agent OS-compatible defaults.

    The default is permissive (empty policy = allow all). This is
    backward-compatible — existing agents continue to work without
    any policy configuration. Organizations that want a secure-by-
    default posture should set policies on all agents.
    """

    # Legacy fields (backwards compatible with existing DB blob)
    denied_tools: List[str] = Field(
        default_factory=list,
        description="Tools that are always denied by the security policy.",
    )
    approval_required_tools: List[str] = Field(
        default_factory=list,
        description="Tools that always require human approval before execution.",
    )
    # New fields
    rules: List[PolicyRule] = Field(
        default_factory=list,
        description="Ordered list of policy rules (Agent OS compatible).",
    )
    max_calls_per_tool: Dict[str, int] = Field(
        default_factory=dict,
        description="Per-tool per-run call limit. Overrides defaults.max_tool_calls for specific tools.",
    )
    defaults: Optional[PolicyDefaults] = Field(
        default=None,
        description="Default policy settings when no rule matches.",
    )
    loop_detection: Optional[LoopDetectionConfig] = Field(
        default=None,
        description="Tool loop detection configuration.",
    )


class PolicyDecision(BaseModel):
    """The result of a policy check — replaces the old PolicyAction return.

    Includes the matched rule, action, reason, and audit metadata.
    This is richer than Agent OS's boolean allow/deny — it carries
    enough context for audit logging without a second lookup.
    """

    allowed: bool = Field(default=True, description="Whether the tool call is allowed")
    matched_rule: Optional[str] = Field(default=None, description="Name of the matched rule, if any")
    action: PolicyAction = Field(default=PolicyAction.ALLOW, description="The action taken")
    reason: str = Field(default="No rules matched; default action applied", description="Why this decision was made")
    audit_entry: Dict[str, Any] = Field(
        default_factory=dict,
        description="Audit metadata: {tool_name, matched_rule, action, reason, tool_category}",
    )
    audit_warning: Optional[str] = Field(default=None, description="Warning text for AUDIT action — appended to tool result")


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _resolve_field(context: Dict[str, Any], field: str) -> Any:
    """Resolve a dot-path field from the evaluation context.

    Examples:
        _resolve_field(ctx, "tool_name") -> ctx["tool_name"]
        _resolve_field(ctx, "tool_args.query") -> ctx["tool_args"]["query"]
        _resolve_field(ctx, "tool_args.nested.key") -> ctx["tool_args"]["nested"]["key"]

    Returns None if the path doesn't exist.
    """
    parts = field.split(".")
    current = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _coerce_type(value: Any, target: Any) -> Any:
    """Coerce a condition value to match the target's type.

    YAML loads "10" as a string. If the context field is an int,
    we need to compare int to int. This handles the common cases
    without trying to be too clever.
    """
    if target is None:
        return value
    if isinstance(value, type(target)):
        return value
    try:
        if isinstance(target, int):
            return int(value)
        if isinstance(target, float):
            return float(value)
        if isinstance(target, bool):
            return bool(value)
    except (ValueError, TypeError):
        return value
    return value


def _evaluate_condition(condition: PolicyCondition, context: Dict[str, Any], rule: PolicyRule) -> bool:
    """Evaluate a single policy condition against the evaluation context.

    Returns True if the condition matches, False otherwise.
    """
    field_value = _resolve_field(context, condition.field)

    # If the field doesn't exist in context, the condition doesn't match
    if field_value is None:
        return False

    # Coerce condition value to match field type
    compare_value = _coerce_type(condition.value, field_value)

    op = condition.operator

    if op == PolicyOperator.EQ:
        return field_value == compare_value
    elif op == PolicyOperator.NE:
        return field_value != compare_value
    elif op == PolicyOperator.GT:
        try:
            return field_value > compare_value
        except TypeError:
            return False
    elif op == PolicyOperator.LT:
        try:
            return field_value < compare_value
        except TypeError:
            return False
    elif op == PolicyOperator.GTE:
        try:
            return field_value >= compare_value
        except TypeError:
            return False
    elif op == PolicyOperator.LTE:
        try:
            return field_value <= compare_value
        except TypeError:
            return False
    elif op == PolicyOperator.IN:
        if isinstance(compare_value, (list, tuple, set)):
            return field_value in compare_value
        return False
    elif op == PolicyOperator.NOT_IN:
        if isinstance(compare_value, (list, tuple, set)):
            return field_value not in compare_value
        return False
    elif op == PolicyOperator.MATCHES:
        # Use cached compiled regex from model_post_init
        pattern = rule._compiled_condition_pattern
        if pattern is None:
            try:
                pattern = re.compile(str(compare_value))
            except re.error:
                return False
        if isinstance(field_value, str):
            return bool(pattern.search(field_value))
        return bool(pattern.search(str(field_value)))
    elif op == PolicyOperator.CONTAINS:
        if isinstance(field_value, str) and isinstance(compare_value, str):
            return compare_value in field_value
        if isinstance(field_value, (list, tuple)):
            return compare_value in field_value
        return False
    elif op == PolicyOperator.CONTAINS_SECRET:
        # Check all tool_args values for secrets using entropy + regex
        # Recursively walk nested dicts/lists to find secrets at any depth
        from letta.security.secret_scanner import SecretPatternChecker
        tool_args = context.get("tool_args", {})

        def _collect_strings(obj):
            if isinstance(obj, str):
                return [obj]
            strings = []
            if isinstance(obj, dict):
                for v in obj.values():
                    strings.extend(_collect_strings(v))
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    strings.extend(_collect_strings(item))
            return strings

        for value in _collect_strings(tool_args):
            result = SecretPatternChecker.check(value)
            if result is not None:
                return True
        return False

    elif op == PolicyOperator.CONTAINS_INJECTION:
        # Check all tool_args values for prompt injection patterns
        # Recursively walk nested dicts/lists to find injection at any depth
        from letta.security.content_validator import ContentValidator
        tool_args = context.get("tool_args", {})

        def _collect_injection_strings(obj):
            if isinstance(obj, str):
                return [obj]
            strings = []
            if isinstance(obj, dict):
                for v in obj.values():
                    strings.extend(_collect_injection_strings(v))
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    strings.extend(_collect_injection_strings(item))
            return strings

        for value in _collect_injection_strings(tool_args):
            result = ContentValidator.check(value)
            if result is not None:
                return True
        return False

    return False


# ---------------------------------------------------------------------------
# PolicyChecker
# ---------------------------------------------------------------------------


class PolicyChecker:
    """Checks tool calls against the per-agent security policy.

    Supports both the legacy two-list model (denied_tools,
    approval_required_tools) and the new Agent OS-compatible rule
    engine with argument-level conditions, rate limiting, and
    regex matching.

    Usage:
        checker = PolicyChecker(ToolCallPolicy(
            denied_tools=["web_search"],
            rules=[
                PolicyRule(
                    name="block-internal-queries",
                    condition=PolicyCondition(field="tool_args.query", operator="matches", value="internal|secret"),
                    action=PolicyAction.DENY,
                    priority=80,
                ),
            ],
        ))
        decision = checker.check("web_search", eval_context={"tool_name": "web_search"})
        # decision.allowed == False, decision.matched_rule == "denied_tools"
    """

    def __init__(self, policy: Optional[ToolCallPolicy] = None):
        self.policy = policy or ToolCallPolicy()
        self.deny_all = False  # Set to True when policy load fails (fail-closed)
        self._call_counts: Dict[str, int] = {}  # tool_name -> count (per-run)
        self._total_calls: int = 0  # total tool calls this run
        self._call_window: list[tuple[str, str]] = []  # (tool_name, args_hash) for loop detection

    def check(self, tool_name: str, eval_context: Optional[Dict[str, Any]] = None) -> PolicyDecision:
        """Check a tool call against the security policy.

        Args:
            tool_name: The name of the tool being called.
            eval_context: Optional evaluation context with tool_args,
                tool_call_count, actor_id, agent_id, etc. If not
                provided, only the legacy two-list check is used.

        Returns:
            PolicyDecision with allowed, matched_rule, action, reason,
            and audit_entry.
        """
        if self.deny_all:
            return PolicyDecision(
                allowed=False,
                action="deny",
                matched_rule="fail_closed",
                reason="Policy load failed — fail-closed mode",
                audit_entry={"tool_name": tool_name, "matched_rule": "fail_closed", "action": "deny", "reason": "Policy load failed"},
            )

        # --- Legacy two-list check (highest priority) ---
        if tool_name in self.policy.denied_tools:
            return PolicyDecision(
                allowed=False,
                action="deny",
                matched_rule="denied_tools",
                reason=f"Tool '{tool_name}' is in denied_tools list",
                audit_entry={"tool_name": tool_name, "matched_rule": "denied_tools", "action": "deny", "reason": "denied_tools list"},
            )

        if tool_name in self.policy.approval_required_tools:
            return PolicyDecision(
                allowed=False,  # not auto-allowed — needs approval
                action="require_approval",
                matched_rule="approval_required_tools",
                reason=f"Tool '{tool_name}' requires human approval",
                audit_entry={
                    "tool_name": tool_name,
                    "matched_rule": "approval_required_tools",
                    "action": "require_approval",
                    "reason": "approval_required_tools list",
                },
            )

        # --- Rate limiting (before rule evaluation) ---
        if eval_context is not None:
            rate_limit_decision = self._check_rate_limits(tool_name)
            if rate_limit_decision is not None:
                return rate_limit_decision

        # --- Loop detection (after rate limiting, before rule evaluation) ---
        if eval_context is not None:
            loop_decision = self._check_loop_detection(tool_name, eval_context)
            if loop_decision is not None:
                return loop_decision

        # --- Rule evaluation (Agent OS-compatible) ---
        if eval_context is not None and self.policy.rules:
            # Ensure tool_name is in the context
            eval_context.setdefault("tool_name", tool_name)

            # Sort rules by priority (highest first) for first-match semantics
            sorted_rules = sorted(self.policy.rules, key=lambda r: r.priority, reverse=True)

            for rule in sorted_rules:
                if _evaluate_condition(rule.condition, eval_context, rule):
                    # Condition matched — check pattern if present
                    if rule.pattern is not None and rule._compiled_pattern is not None:
                        # Pattern matches against the tool's string representation
                        # For tool calls, this is typically the tool args serialized
                        action_str = str(eval_context.get("tool_args", ""))
                        if not rule._compiled_pattern.search(action_str):
                            continue  # condition matched but pattern didn't — skip

                    action = rule.action
                    allowed = action in (PolicyAction.ALLOW, PolicyAction.AUDIT)

                    return PolicyDecision(
                        allowed=allowed,
                        action=action,
                        matched_rule=rule.name,
                        reason=rule.message or f"Matched rule '{rule.name}'",
                        audit_entry={
                            "tool_name": tool_name,
                            "matched_rule": rule.name,
                            "action": action.value,
                            "reason": rule.message or f"Matched rule '{rule.name}'",
                        },
                    )

        # --- Default action (no rule matched) ---
        defaults = self.policy.defaults
        if defaults is not None:
            default_action = defaults.action
            allowed = default_action in (PolicyAction.ALLOW, PolicyAction.AUDIT)
            return PolicyDecision(
                allowed=allowed,
                action=default_action,
                matched_rule=None,
                reason=f"No rules matched; default action is {default_action.value}",
                audit_entry={"tool_name": tool_name, "matched_rule": None, "action": default_action.value, "reason": "default action"},
            )

        # No rules, no defaults — allow (backwards compatible)
        return PolicyDecision(
            allowed=True,
            action="allow",
            matched_rule=None,
            reason="No rules matched; default action applied",
            audit_entry={"tool_name": tool_name, "matched_rule": None, "action": "allow", "reason": "default"},
        )

    def _check_rate_limits(self, tool_name: str) -> Optional[PolicyDecision]:
        """Check per-tool and global rate limits. Returns a DENY decision if exceeded."""
        # Per-tool rate limit (takes precedence)
        per_tool_limit = self.policy.max_calls_per_tool.get(tool_name)
        if per_tool_limit is not None:
            current = self._call_counts.get(tool_name, 0)
            if current >= per_tool_limit:
                return PolicyDecision(
                    allowed=False,
                    action="deny",
                    matched_rule=f"max_calls_per_tool/{tool_name}",
                    reason=f"Tool '{tool_name}' exceeded per-run limit of {per_tool_limit} calls",
                    audit_entry={
                        "tool_name": tool_name,
                        "matched_rule": f"max_calls_per_tool/{tool_name}",
                        "action": "deny",
                        "reason": f"Rate limit: {current}/{per_tool_limit}",
                    },
                )

        # Global rate limit
        defaults = self.policy.defaults
        if defaults is not None and defaults.max_tool_calls is not None:
            if self._total_calls >= defaults.max_tool_calls:
                return PolicyDecision(
                    allowed=False,
                    action="deny",
                    matched_rule="defaults/max_tool_calls",
                    reason=f"Global tool call limit of {defaults.max_tool_calls} exceeded",
                    audit_entry={
                        "tool_name": tool_name,
                        "matched_rule": "defaults/max_tool_calls",
                        "action": "deny",
                        "reason": f"Global rate limit: {self._total_calls}/{defaults.max_tool_calls}",
                    },
                )

        return None

    def _check_loop_detection(self, tool_name: str, eval_context: Optional[Dict[str, Any]]) -> Optional[PolicyDecision]:
        """Check for tool call loops — same tool + same args repeated within a window.

        Returns a DENY decision if the loop threshold is exceeded, None otherwise.
        """
        config = self.policy.loop_detection
        if config is None or not config.enabled:
            return None

        # Hash the args for comparison (avoid storing raw args)
        tool_args = eval_context.get("tool_args", {}) if eval_context else {}
        args_hash = hashlib.sha256(
            json.dumps(tool_args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        current = (tool_name, args_hash)

        # Count occurrences of current call in the window
        count = sum(1 for c in self._call_window if c == current)

        if count >= config.threshold - 1:  # -1 because we haven't appended yet
            return PolicyDecision(
                allowed=False,
                action="deny",
                matched_rule="loop_detection",
                reason=f"Tool loop detected: '{tool_name}' called with same args {count + 1} times within window of {config.window}",
                audit_entry={
                    "tool_name": tool_name,
                    "matched_rule": "loop_detection",
                    "action": "deny",
                    "reason": f"Loop: {count + 1}/{config.threshold} identical calls in window {config.window}",
                },
            )

        return None

    def record_call(self, tool_name: str, tool_args: dict | None = None) -> None:
        """Record a tool call for rate limiting and loop detection.

        Called after a successful policy check.
        """
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1
        self._total_calls += 1
        # Loop detection tracking
        config = self.policy.loop_detection
        if config is not None and config.enabled:
            args_hash = hashlib.sha256(
                json.dumps(tool_args or {}, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            self._call_window.append((tool_name, args_hash))
            # Trim to window size
            if len(self._call_window) > config.window:
                self._call_window = self._call_window[-config.window:]

    def get_call_count(self, tool_name: str) -> int:
        """Get the number of calls made to a specific tool this run."""
        return self._call_counts.get(tool_name, 0)

    def reset_call_counts(self) -> None:
        """Reset per-run call counts. Called at the start of each agent run."""
        self._call_counts.clear()
        self._total_calls = 0
        self._call_window.clear()

    def update_policy(self, policy: ToolCallPolicy) -> None:
        """Update the policy (e.g., after loading from DB)."""
        self.policy = policy
        self.deny_all = False  # Successful update clears the fail-closed flag

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a single rule to the current policy.

        Used for injecting default rules (e.g., secret detection)
        that aren't stored in the DB but should be present.
        """
        if self.policy is None:
            self.policy = ToolCallPolicy()
        self.policy.rules.append(rule)


# ---------------------------------------------------------------------------
# YAML policy loader
# ---------------------------------------------------------------------------


def _yaml_required(key: str, rule_data: dict) -> str:
    """Raise ValueError for missing required keys instead of KeyError."""
    raise ValueError(f"Missing required key '{key}' in rule: {rule_data}")


def load_policies_from_yaml(yaml_text: str) -> ToolCallPolicy:
    """Load a ToolCallPolicy from a YAML string in Agent OS format.

    The YAML format matches Agent OS exactly:

        version: "1.0"
        name: my-policy
        rules:
          - name: block-destructive-sql
            condition:
              field: tool_name
              operator: eq
              value: database_query
            pattern: "DROP|TRUNCATE"
            action: deny
            priority: 100
            message: "Destructive SQL blocked"
        defaults:
          action: allow
          max_tool_calls: 100

    Raises:
        ValueError: If the YAML is invalid or missing required fields.
        pydantic.ValidationError: If rule schema validation fails.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"YAML must be a mapping (dict), got {type(data).__name__}")

    # Parse rules
    rules = []
    for rule_data in data.get("rules", []):
        if not isinstance(rule_data, dict):
            raise ValueError(f"Each rule must be a mapping, got {type(rule_data).__name__}")

        condition_data = rule_data.get("condition")
        if not isinstance(condition_data, dict):
            raise ValueError(f"Rule '{rule_data.get('name', '?')}' must have a 'condition' mapping")

        condition = PolicyCondition(
            field=condition_data.get("field") or _yaml_required("field", rule_data),
            operator=PolicyOperator(condition_data.get("operator") or _yaml_required("operator", rule_data)),
            value=condition_data.get("value") if condition_data.get("value") is not None else _yaml_required("value", rule_data),
        )

        action_str = rule_data.get("action", "allow")
        action = PolicyAction(action_str)

        rule = PolicyRule(
            name=rule_data.get("name") or _yaml_required("name", rule_data),
            condition=condition,
            action=action,
            priority=rule_data.get("priority", 0),
            message=rule_data.get("message"),
            pattern=rule_data.get("pattern"),
        )
        rules.append(rule)

    # Parse defaults
    defaults_data = data.get("defaults")
    defaults = None
    if defaults_data is not None:
        if not isinstance(defaults_data, dict):
            raise ValueError(f"'defaults' must be a mapping, got {type(defaults_data).__name__}")

        defaults = PolicyDefaults(
            action=PolicyAction(defaults_data.get("action", "allow")),
            max_tool_calls=defaults_data.get("max_tool_calls"),
            max_tokens=defaults_data.get("max_tokens"),
            timeout_seconds=defaults_data.get("timeout_seconds"),
        )

    # Parse max_calls_per_tool (not in Agent OS schema — our extension)
    max_calls_per_tool = data.get("max_calls_per_tool", {})

    # Parse loop_detection (not in Agent OS schema — our extension)
    loop_detection_data = data.get("loop_detection")
    loop_detection = None
    if loop_detection_data is not None:
        if not isinstance(loop_detection_data, dict):
            raise ValueError(f"'loop_detection' must be a mapping, got {type(loop_detection_data).__name__}")
        loop_detection = LoopDetectionConfig(
            enabled=loop_detection_data.get("enabled", False),
            window=loop_detection_data.get("window", 5),
            threshold=loop_detection_data.get("threshold", 3),
        )

    return ToolCallPolicy(
        rules=rules,
        defaults=defaults,
        max_calls_per_tool=max_calls_per_tool,
        loop_detection=loop_detection,
    )


def load_policies_from_yaml_file(path: str) -> ToolCallPolicy:
    """Load a ToolCallPolicy from a YAML file."""
    with open(path, "r") as f:
        return load_policies_from_yaml(f.read())
