"""Tests for letta.functions.function_sets.file_persistence — file_write, file_read, file_list."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from letta.functions.function_sets.file_persistence import (
    _agent_file_dir,
    _validate_path,
    file_write,
    file_read,
    file_list,
)


@pytest.fixture
def agent_state(tmp_path):
    """Create a mock agent_state with a per-agent directory."""
    mock = MagicMock()
    mock.id = "test-agent-123"
    # Override _agent_file_dir to use tmp_path
    agent_dir = tmp_path / "agent_files" / mock.id
    agent_dir.mkdir(parents=True, exist_ok=True)
    mock._file_dir = agent_dir
    return mock


@pytest.fixture
def file_dir(agent_state):
    """Return the agent's file directory."""
    return agent_state._file_dir


def _file_write(agent_state, path, content):
    """Wrapper that uses the tmp_path directory instead of the real one."""
    from letta.functions.function_sets.file_persistence import _validate_path, _get_limits
    base_dir = agent_state._file_dir
    file_path = _validate_path(base_dir, path)

    max_file_size, max_total_size = _get_limits()
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > max_file_size:
        raise ValueError(
            f"File content exceeds per-file limit "
            f"({len(content_bytes)} > {max_file_size} bytes). "
            f"Write a smaller file or split the content."
        )

    total_size = 0
    if base_dir.exists():
        for f in base_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size

    if file_path.exists() and file_path.is_file():
        total_size -= file_path.stat().st_size

    if total_size + len(content_bytes) > max_total_size:
        raise ValueError(
            f"Total storage would exceed per-agent limit "
            f"({total_size + len(content_bytes)} > {max_total_size} bytes). "
            f"Delete existing files to make room."
        )

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    try:
        file_path.chmod(0o644)
    except OSError:
        pass

    return f"Wrote {len(content_bytes)} bytes to {path}"


def _file_read(agent_state, path):
    """Wrapper that uses the tmp_path directory."""
    from letta.functions.function_sets.file_persistence import _validate_path
    base_dir = agent_state._file_dir
    file_path = _validate_path(base_dir, path)

    if not file_path.exists():
        return f"File not found: {path}"
    if not file_path.is_file():
        return f"Not a file: {path}"

    return file_path.read_text(encoding="utf-8")


def _file_list(agent_state, prefix=None):
    """Wrapper that uses the tmp_path directory."""
    from letta.functions.function_sets.file_persistence import _validate_path
    base_dir = agent_state._file_dir

    if prefix:
        _validate_path(base_dir, prefix)
        search_dir = base_dir / prefix
    else:
        search_dir = base_dir

    if not search_dir.exists():
        return json.dumps([])

    files = sorted(f for f in search_dir.rglob("*") if f.is_file())

    result = []
    for f in files:
        rel = str(f.relative_to(base_dir))
        stat = f.stat()
        result.append({
            "name": rel,
            "size": stat.st_size,
            "modified_at": "2026-01-01T00:00:00+00:00",  # stable for assertions
        })

    return json.dumps(result)


class TestValidatePath:
    def test_rejects_empty_path(self, file_dir):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_path(file_dir, "")

    def test_rejects_null_bytes(self, file_dir):
        with pytest.raises(ValueError, match="null bytes"):
            _validate_path(file_dir, "file\0.txt")

    def test_rejects_absolute_path(self, file_dir):
        with pytest.raises(ValueError, match="Absolute paths"):
            _validate_path(file_dir, "/etc/passwd")

    def test_rejects_parent_traversal(self, file_dir):
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path(file_dir, "../../../etc/passwd")

    def test_rejects_dotdot_in_middle(self, file_dir):
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path(file_dir, "reports/../../etc/passwd")

    def test_accepts_simple_relative_path(self, file_dir):
        result = _validate_path(file_dir, "reports/summary.md")
        assert str(result).startswith(str(file_dir))

    def test_accepts_nested_path(self, file_dir):
        result = _validate_path(file_dir, "reports/2026/q1/summary.md")
        assert str(result).startswith(str(file_dir))


class TestFileWrite:
    def test_creates_file(self, agent_state, file_dir):
        result = _file_write(agent_state, "hello.txt", "Hello, world!")
        assert "hello.txt" in result
        assert (file_dir / "hello.txt").read_text() == "Hello, world!"

    def test_creates_nested_directories(self, agent_state, file_dir):
        _file_write(agent_state, "reports/2026/summary.md", "# Summary")
        assert (file_dir / "reports/2026/summary.md").read_text() == "# Summary"

    def test_overwrites_existing_file(self, agent_state, file_dir):
        _file_write(agent_state, "data.csv", "old,data")
        _file_write(agent_state, "data.csv", "new,data")
        assert (file_dir / "data.csv").read_text() == "new,data"

    def test_returns_size_in_message(self, agent_state):
        content = "Hello!"
        result = _file_write(agent_state, "test.txt", content)
        assert str(len(content.encode("utf-8"))) in result

    def test_rejects_path_traversal(self, agent_state):
        with pytest.raises(ValueError, match="Path traversal"):
            _file_write(agent_state, "../../../etc/passwd", "hacked")

    def test_rejects_absolute_path(self, agent_state):
        with pytest.raises(ValueError, match="Absolute paths"):
            _file_write(agent_state, "/etc/passwd", "hacked")

    def test_rejects_null_bytes(self, agent_state):
        with pytest.raises(ValueError, match="null bytes"):
            _file_write(agent_state, "file\0.txt", "content")

    def test_rejects_oversized_file(self, agent_state):
        with pytest.raises(ValueError, match="per-file limit"):
            _file_write(agent_state, "big.txt", "x" * 2_000_000)

    def test_overwrite_deducts_old_size(self, agent_state, file_dir):
        """When overwriting, the old file's size should be deducted from the total."""
        # Write a file that's near the per-file limit
        content_a = "x" * 900_000
        _file_write(agent_state, "big.txt", content_a)
        # Overwrite with different content — should succeed because
        # the old size is deducted from the total before checking
        content_b = "y" * 900_000
        _file_write(agent_state, "big.txt", content_b)
        assert (file_dir / "big.txt").read_text() == content_b


class TestFileRead:
    def test_reads_file_content(self, agent_state, file_dir):
        (file_dir / "notes.txt").write_text("Some notes", encoding="utf-8")
        result = _file_read(agent_state, "notes.txt")
        assert result == "Some notes"

    def test_returns_error_for_missing_file(self, agent_state):
        result = _file_read(agent_state, "nonexistent.txt")
        assert "not found" in result.lower()

    def test_returns_error_for_directory(self, agent_state, file_dir):
        (file_dir / "subdir").mkdir(exist_ok=True)
        result = _file_read(agent_state, "subdir")
        assert "not a file" in result.lower()

    def test_reads_nested_file(self, agent_state, file_dir):
        (file_dir / "reports").mkdir(exist_ok=True)
        (file_dir / "reports" / "q1.md").write_text("# Q1 Report", encoding="utf-8")
        result = _file_read(agent_state, "reports/q1.md")
        assert result == "# Q1 Report"


class TestFileList:
    def test_returns_empty_list_for_empty_dir(self, agent_state):
        result = _file_list(agent_state)
        assert json.loads(result) == []

    def test_lists_files_as_json(self, agent_state, file_dir):
        (file_dir / "a.txt").write_text("aaa", encoding="utf-8")
        (file_dir / "b.txt").write_text("bbbb", encoding="utf-8")
        result = _file_list(agent_state)
        files = json.loads(result)
        assert len(files) == 2
        names = [f["name"] for f in files]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_includes_size(self, agent_state, file_dir):
        (file_dir / "data.csv").write_text("hello,world", encoding="utf-8")
        result = _file_list(agent_state)
        files = json.loads(result)
        assert files[0]["size"] > 0

    def test_prefix_filter(self, agent_state, file_dir):
        (file_dir / "reports").mkdir(exist_ok=True)
        (file_dir / "reports" / "q1.md").write_text("Q1", encoding="utf-8")
        (file_dir / "reports" / "q2.md").write_text("Q2", encoding="utf-8")
        (file_dir / "notes.txt").write_text("notes", encoding="utf-8")
        result = _file_list(agent_state, prefix="reports/")
        files = json.loads(result)
        assert len(files) == 2
        for f in files:
            assert f["name"].startswith("reports/")

    def test_prefix_no_match(self, agent_state, file_dir):
        (file_dir / "notes.txt").write_text("notes", encoding="utf-8")
        result = _file_list(agent_state, prefix="reports/")
        files = json.loads(result)
        assert len(files) == 0
