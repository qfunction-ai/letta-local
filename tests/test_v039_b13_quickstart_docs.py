"""B-13 witnesses (v0.16.39 review B-13): the README quick-start must stay runnable.

Under secure-by-default (v0.16.38+), a password-less client gets a 401, and the
removed upstream cloud embedding preset breaks agent creation. These guards keep
the quick-start honest as the README evolves. The live code path is exercised by
smoke rig check 21 (scripts/smoke/quickstart_witness.py).
"""
import re
from pathlib import Path

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
