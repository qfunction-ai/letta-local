"""B-13 witnesses (v0.16.39 review B-13): the README quick-start must stay runnable.

Under secure-by-default (v0.16.38+), a password-less client gets a 401, and the
removed upstream cloud embedding preset breaks agent creation. These guards keep
the quick-start honest as the README evolves. The live code path is exercised by
smoke rig check 21 (scripts/smoke/quickstart_witness.py).
"""
import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"


def quickstart_block():
    text = README.read_text()
    m = re.search(r"## Quick start(.*?)(?=\n## )", text, re.S)
    assert m, "README must have a Quick start section"
    return m.group(1)


def test_client_uses_password():
    """The quick-start client must pass api_key — anonymous clients 401 under secure mode."""
    block = quickstart_block()
    assert re.search(r"Letta\(base_url=.*api_key=", block), (
        "quick-start client must authenticate (api_key) — secure mode is on by default"
    )


def test_no_upstream_cloud_embedding():
    """The removed letta/letta-free preset must not reappear in the quick-start."""
    block = quickstart_block()
    assert "letta/letta-free" not in block, (
        "quick-start must not use the removed upstream cloud embedding (letta/letta-free)"
    )
    assert re.search(r'embedding="ollama/', block), (
        "quick-start must use a local embedding model"
    )


def test_server_start_sets_password():
    """The server start command must set LETTA_SERVER_PASSWORD."""
    block = quickstart_block()
    assert "LETTA_SERVER_PASSWORD" in block, (
        "quick-start server start must set LETTA_SERVER_PASSWORD (secure mode default)"
    )


def test_witness_script_matches_readme_defaults():
    """The witness script's defaults must not drift from the README's model values."""
    witness = (Path(__file__).resolve().parents[1] / "scripts" / "smoke" / "quickstart_witness.py").read_text()
    assert "ollama/embeddinggemma" in witness
    assert "api_key=PASSWORD" in witness


# ---------------------------------------------------------------------------
# D-2 (final check): the "upstream is unmaintained" claim was removed by U-1
# and then reappeared in two new places in the same batch. Guard it: upstream
# Letta retired the V1 SERVER (archived August 2026) but is actively
# developing Letta Code. Saying "upstream is unmaintained" is false.
# ---------------------------------------------------------------------------

_UPSTREAM_UNMAINTAINED_RE = re.compile(
    r"(upstream|letta-ai/letta)[^.\n]{0,60}(unmaintained|no longer maintained)",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "doc", ["README.md", "CONTRIBUTING.md", "SECURITY.md"]
)
def test_docs_do_not_claim_upstream_is_unmaintained(doc):
    """README/CONTRIBUTING/SECURITY must not say upstream Letta is
    unmaintained or no longer maintained. Upstream archived the V1 server
    (August 2026) but actively develops Letta Code."""
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / doc).read_text()
    m = _UPSTREAM_UNMAINTAINED_RE.search(text)
    assert m is None, (
        f"{doc} claims upstream Letta is unmaintained "
        f"(match: {m.group(0)!r}). Upstream retired the V1 server but "
        f"actively develops Letta Code. Say the V1 server is retired/"
        f"archived, not that Letta is unmaintained."
    )
