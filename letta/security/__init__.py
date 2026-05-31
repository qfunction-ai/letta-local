"""Security modules for the Letta agent fork.

- audit: Unified audit log for security events
- audit_helpers: Audit logging convenience functions
- agent_security: Security entry points (policy check, canary, circuit breaker)
- block_guard: Read-only block mutation guards
- canary: Canary token generation and management
- canary_output_filter: Redact canary tokens from assistant messages
- output_filter: Output filtering dispatcher
- policy: Tool call policy engine (conditions, rules, YAML loading)
- secret_scanner: Entropy + regex secret detection
"""
