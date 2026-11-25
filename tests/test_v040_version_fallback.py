"""v0.16.40 review nit: __version__ has no stale literal.

`version("letta")` raises PackageNotFoundError for missing metadata; it
never returns falsy, so `version(...) or "0.16.40"` was a dead path whose
literal would go stale at the next release (check 20 catches exactly this
drift class). The fallback is now "0+unknown" — honest for a dev checkout,
and the container install (what the release rig gates) always carries real
metadata.
"""

import importlib
import importlib.metadata

import letta


def test_version_fallback_is_not_a_stale_literal():
    assert letta.__version__ != "0.16.40" or letta.__version__ == importlib.metadata.version("letta")


def test_package_not_found_fallback(monkeypatch):
    def raise_pnf(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_pnf)
    importlib.reload(letta)  # re-run the try block in __init__
    assert letta.__version__ == "0+unknown"
    # restore for other tests
    monkeypatch.undo()
    importlib.reload(letta)
