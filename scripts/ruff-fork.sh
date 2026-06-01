#!/usr/bin/env bash
# ruff-fork.sh — Run ruff on fork-only and fork-modified files.
# Catches undefined names (F821) and unused imports (F401) that
# would crash at runtime or indicate dead code.
#
# Usage: ./scripts/ruff-fork.sh
# Exit 1 if any issues found.

set -euo pipefail
cd "$(dirname "$0")/.."

# Fork-only directories
FORK_ONLY="letta/security/"

# Fork-modified files (upstream files we've added code to)
FORK_MODIFIED=(
    letta/llm_api/tool_capability_probe.py
    letta/llm_api/tool_call_repair.py
    letta/functions/function_sets/files.py
    letta/functions/function_sets/file_persistence.py
    letta/services/tool_sandbox/
    letta/services/tool_executor/sandbox_tool_executor.py
    letta/services/tool_executor/files_tool_executor.py
    letta/services/helpers/tool_parser_helper.py
    letta/helpers/tool_execution_helper.py
    letta/interfaces/openai_streaming_interface.py
    letta/schemas/llm_config.py
    letta/settings.py
)

echo "Running ruff F821/F401 on fork files..."
ruff check $FORK_ONLY "${FORK_MODIFIED[@]}" --select F821,F401
echo "Clean."
