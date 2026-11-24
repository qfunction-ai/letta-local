"""B-11 witness: the smoke rig supports an optional RIG_TARGET pin.

When RIG_TARGET is set, check 20 hard-fails on any server-version
mismatch; when unset, the unfixed-first protocol (running pre-release
images) keeps working. Guard asserts the shipped script contains the
RIG_TARGET read, both BAD failure modes, and their case-statement arms
(verdict lines that always print — no vacuous pass). RED on baseline
(RIG_TARGET absent), GREEN after.
"""
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/smoke/smoke_test.sh"


def test_rig_target_read_and_mismatch_modes_present():
    s = SCRIPT.read_text()
    assert 'os.environ.get("RIG_TARGET"' in s, "rig does not read RIG_TARGET"
    assert "BAD:rig-target-mismatch:" in s, "rig lacks the mismatch failure mode"
    assert "BAD:rig-target-not-strict-numeric:" in s, "rig lacks the non-numeric failure mode"


def test_rig_target_case_arms_print_verdicts():
    s = SCRIPT.read_text()
    # every BAD:rig-target-* mode must have a case arm ending in a bad() call
    for mode in ("rig-target-not-strict-numeric", "rig-target-mismatch"):
        pattern = rf'"BAD:{mode}:"\*\)\n\s*bad "20\.[^\n]*{mode}[^\n]*" ;;'
        assert re.search(pattern, s), f"no case arm with printed verdict for {mode}"


def test_unset_rig_target_keeps_unfixed_first_path():
    s = SCRIPT.read_text()
    # the gate must only branch when RIG_TARGET is non-empty
    assert re.search(r'rig_target = os\.environ\.get\("RIG_TARGET", ""\)\.strip\(\)\nif rig_target:', s), \
        "RIG_TARGET must be optional — unset behavior must be unchanged"
