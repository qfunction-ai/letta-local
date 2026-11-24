"""B-2 witnesses: upstream domain sweep.

RED on 05a7d0af5: 14 letta.com hits across 9 files (see plan §1.2), CORS
append of https://app.letta.com with allow_credentials=True, and silent
letta.com fallbacks in LLMConfig/EmbeddingConfig.default_config("letta").
"""
import re
from pathlib import Path

import pytest

from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.llm_config import LLMConfig

LETTA_ROOT = Path(__file__).resolve().parent.parent / "letta"

# Attribution/copyright notices are the only permitted mentions; the sweep
# removed even the dead-text comments, so this is strict. ONE deliberate
# exception: the startup stored-config checker needs the literal domain to
# detect it in stored configs — allowlisted here, where the rule lives, so
# the exception is visible and bounded (exact file, exact count).
DOMAIN_RE = re.compile(r"[a-z0-9.-]*letta\.com")

# Part B review nit: no string-assembly dodges. The checker writes the
# domain plainly and this allowlist entry is the entire exception surface.
DOMAIN_SWEEP_ALLOWLIST = {
    "server/server.py": 1,  # startup stored-agent/stored-archive endpoint checker
}


def test_no_upstream_domains_in_source_tree():
    violations = []
    for path in LETTA_ROOT.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        rel = str(path.relative_to(LETTA_ROOT))
        allowed = DOMAIN_SWEEP_ALLOWLIST.get(rel, 0)
        hits = list(DOMAIN_RE.finditer(text))
        if len(hits) <= allowed:
            continue
        for m in hits:
            violations.append(f"{rel}: ...{text[max(0, m.start()-30):m.end()+10]!r}")
    assert not violations, "letta.com domains remain in tree:\n" + "\n".join(violations[:20])


def test_cors_never_appends_upstream_origin():
    """AST-level: app.py must not append any origin to cors_origins, and the
    default cors_origins must not contain an upstream origin."""
    import ast

    from letta.settings import settings

    app_src = (LETTA_ROOT / "server" / "rest_api" / "app.py").read_text()
    tree = ast.parse(app_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append":
            recv = node.func.value
            if isinstance(recv, ast.Attribute) and recv.attr == "cors_origins":
                pytest.fail(f"app.py appends to settings.cors_origins at line {node.lineno} — origins must be config-driven only")
    assert "https://app.letta.com" not in settings.cors_origins


def test_llm_config_letta_preset_removed():
    with pytest.raises(ValueError):
        LLMConfig.default_config(model_name="letta")


def test_embedding_config_letta_preset_removed():
    with pytest.raises(ValueError):
        EmbeddingConfig.default_config(model_name="letta")
