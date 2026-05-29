import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from letta.functions.types import FileOpenRequest

if TYPE_CHECKING:
    from letta.schemas.agent import AgentState
    from letta.schemas.file import FileMetadata


async def open_files(agent_state: "AgentState", file_requests: List[FileOpenRequest], close_all_others: bool = False) -> str:
    """Open one or more files and load their contents into files section in core memory. Maximum of 5 files can be opened simultaneously.

    Use this when you want to:
    - Inspect or reference file contents during reasoning
    - View specific portions of large files (e.g. functions or definitions)
    - Replace currently open files with a new set for focused context (via `close_all_others=True`)

    Examples:
        Open single file belonging to a directory named `project_utils` (entire content):
            file_requests = [FileOpenRequest(file_name="project_utils/config.py")]

        Open multiple files with different view ranges:
            file_requests = [
                FileOpenRequest(file_name="project_utils/config.py", offset=0, length=50),     # Lines 1-50
                FileOpenRequest(file_name="project_utils/main.py", offset=100, length=100),    # Lines 101-200
                FileOpenRequest(file_name="project_utils/utils.py")                            # Entire file
            ]

        Close all other files and open new ones:
            open_files(agent_state, file_requests, close_all_others=True)

    Args:
        file_requests (List[FileOpenRequest]): List of file open requests, each specifying file name and optional view range.
        close_all_others (bool): If True, closes all other currently open files first. Defaults to False.

    Returns:
        str: A status message
    """
    raise NotImplementedError("Tool not implemented. Please contact the Letta team.")


async def grep_files(
    agent_state: "AgentState",
    pattern: str,
    include: Optional[str] = None,
    context_lines: Optional[int] = 1,
    offset: Optional[int] = None,
) -> str:
    """
    Searches file contents for pattern matches with surrounding context.

    Results are paginated - shows 20 matches per call. The response includes:
    - A summary of total matches and which files contain them
    - The current page of matches (20 at a time)
    - Instructions for viewing more matches using the offset parameter

    Example usage:
        First call: grep_files(pattern="TODO")
        Next call: grep_files(pattern="TODO", offset=20)  # Shows matches 21-40

    Returns search results containing:
    - Summary with total match count and file distribution
    - List of files with match counts per file
    - Current page of matches (up to 20)
    - Navigation hint for next page if more matches exist

    Args:
        pattern (str): Keyword or regex pattern to search within file contents.
        include (Optional[str]): Optional keyword or regex pattern to filter filenames to include in the search.
        context_lines (Optional[int]): Number of lines of context to show before and after each match.
                                       Equivalent to `-C` in grep_files. Defaults to 1.
        offset (Optional[int]): Number of matches to skip before showing results. Used for pagination.
                                For example, offset=20 shows matches starting from the 21st match.
                                Use offset=0 (or omit) for first page, offset=20 for second page,
                                offset=40 for third page, etc. The tool will tell you the exact
                                offset to use for the next page.
    """
    from letta.functions.function_sets.file_persistence import _agent_file_dir

    base_dir = _agent_file_dir(agent_state)
    if context_lines is None:
        context_lines = 1
    if offset is None:
        offset = 0

    # Compile the search pattern
    try:
        pattern_re = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex pattern: {e}"

    # Compile the include filter if provided
    include_re = None
    if include:
        try:
            include_re = re.compile(include)
        except re.error as e:
            return f"Invalid include regex pattern: {e}"

    # Collect all matches
    all_matches = []  # List of (rel_path, line_num, line_text, context_before, context_after)
    file_match_counts = {}  # rel_path -> count

    for dirpath, dirnames, filenames in os.walk(base_dir):
        # Skip .staging directory
        if ".staging" in dirnames:
            dirnames.remove(".staging")

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, base_dir)

            # Apply include filter
            if include_re and not include_re.search(rel_path):
                continue

            # Skip binary files (detect via null bytes in first 8KB)
            try:
                with open(fpath, "rb") as f:
                    chunk = f.read(8192)
                if b"\x00" in chunk:
                    continue
            except OSError:
                continue

            # Read file and search
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue

            file_count = 0
            for i, line in enumerate(lines):
                if pattern_re.search(line):
                    # Collect context
                    ctx_before = []
                    ctx_after = []
                    for j in range(max(0, i - context_lines), i):
                        ctx_before.append(lines[j].rstrip("\n"))
                    for j in range(i + 1, min(len(lines), i + 1 + context_lines)):
                        ctx_after.append(lines[j].rstrip("\n"))

                    all_matches.append((
                        rel_path,
                        i + 1,  # 1-indexed line number
                        line.rstrip("\n"),
                        ctx_before,
                        ctx_after,
                    ))
                    file_count += 1

            if file_count > 0:
                file_match_counts[rel_path] = file_count

    total_matches = len(all_matches)

    if total_matches == 0:
        return "No matches found."

    # Pagination
    page_size = 20
    start = offset
    end = min(offset + page_size, total_matches)
    page_matches = all_matches[start:end]

    # Format output
    output_lines = []
    output_lines.append(f"Found {total_matches} matches in {len(file_match_counts)} files (showing matches {start + 1}-{end}):")
    output_lines.append("")

    current_file = None
    for rel_path, line_num, line_text, ctx_before, ctx_after in page_matches:
        if rel_path != current_file:
            current_file = rel_path
            count = file_match_counts[rel_path]
            output_lines.append(f"--- {rel_path} ({count} matches) ---")

        for ctx_line in ctx_before:
            output_lines.append(f"  {ctx_line}")
        output_lines.append(f"Line {line_num}: {line_text}")
        for ctx_line in ctx_after:
            output_lines.append(f"  {ctx_line}")
        output_lines.append("")

    if end < total_matches:
        output_lines.append(f"Use offset={end} to see the next {min(page_size, total_matches - end)} matches.")

    return "\n".join(output_lines)


async def semantic_search_files(agent_state: "AgentState", query: str, limit: int = 5) -> List["FileMetadata"]:
    """
    Searches file contents using semantic meaning rather than exact matches.

    Ideal for:
    - Finding conceptually related information across files
    - Discovering relevant content without knowing exact keywords
    - Locating files with similar topics or themes

    Args:
        query (str): The search query text to find semantically similar content.
        limit: Maximum number of results to return (default: 5)

    Returns:
        List[FileMetadata]: List of matching files.
    """
    raise NotImplementedError("Tool not implemented. Please contact the Letta team.")
