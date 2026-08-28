"""Source guard for the abandoned-run noise fixes (v0.16.28).

Pins:
1. V3 and V2 post-completion step-tracking blocks catch NoResultFound
   FIRST (info line, no traceback) ahead of the broad Exception
   handler. V2's broad case normalized from ERROR to WARNING.
2. sqlalchemy_base renders single-element identifier lists without
   brackets (was: id='['run-...']' — list f-stringed whole).

Log plumbing; source-guard + release-time manual verification is
proportionate (a direct unit test would drag in ORM query machinery).
"""
from pathlib import Path


def test_v3_narrowed_handler():
    src = Path("letta/agents/letta_agent_v3.py").read_text()
    narrow = src.find("except NoResultFound:")
    broad = src.find('self.logger.warning(f"Error during post-completion step tracking: {e}")')
    assert narrow != -1 and broad != -1
    assert narrow < broad, "NoResultFound handler must precede the broad Exception handler"
    assert "Skipping post-completion step tracking" in src
    # No ERROR-level twin left in V3
    assert 'self.logger.error(f"Error during post-completion step tracking' not in src


def test_v2_narrowed_and_normalized():
    src = Path("letta/agents/letta_agent_v2.py").read_text()
    narrow = src.find("except NoResultFound:")
    broad = src.find('self.logger.warning(f"Error during post-completion step tracking: {e}")')
    assert narrow != -1 and broad != -1
    assert narrow < broad
    # V2 previously logged the broad case at ERROR — must be normalized away
    assert 'self.logger.error(f"Error during post-completion step tracking' not in src


def test_identifier_formatting_no_brackets():
    src = Path("letta/orm/sqlalchemy_base.py").read_text()
    # The list-whole f-string is gone
    assert 'query_conditions.append(f"id=\'{identifiers}\'")' not in src
    # Single-element renders the scalar; multi renders an IN clause
    assert "query_conditions.append(f\"id='{identifiers[0]}'\")" in src
    assert 'query_conditions.append("id IN ("' in src
