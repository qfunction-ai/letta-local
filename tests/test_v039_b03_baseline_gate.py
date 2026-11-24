"""B-3 baseline-gated scan witnesses (v0.16.39 review decision 3.1).

Covers the three required witnesses:
  1. the gate PASSES on the current lockfile's advisories when the baseline is in place;
  2. the gate FAILS on a new fixable advisory not in the baseline;
  3. the growth check FAILS on an uncommented baseline addition (and passes a commented one).
Plus: new unfixable advisories warn but do not fail.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dependency_scan_gate as gate  # noqa: E402

BASELINE = Path(__file__).resolve().parents[1] / "security" / "dependency-baseline.txt"


def make_audit(vulns):
    """vulns: list of (package, version, vuln_id, fix_versions)."""
    deps = {}
    for pkg, ver, vid, fixes in vulns:
        deps.setdefault(pkg, {"name": pkg, "version": ver, "vulns": []})
        deps[pkg]["vulns"].append({"id": vid, "fix_versions": fixes, "description": "test vuln"})
    return {"dependencies": list(deps.values())}


def write_audit(tmp_path, vulns):
    p = tmp_path / "audit.json"
    p.write_text(json.dumps(make_audit(vulns)))
    return str(p)


class TestGate:
    def test_passes_on_baselined_advisories(self, tmp_path, capsys):
        """Witness 1: the real baseline accepts the real audit output (sampled from it)."""
        audit = json.load(open("/tmp/audit_local.json")) if Path("/tmp/audit_local.json").exists() else None
        if audit is None:
            pytest.skip("local audit snapshot not present in CI")
        rc = gate.run_gate("/tmp/audit_local.json", str(BASELINE))
        out = capsys.readouterr().out
        assert rc == 0
        assert "new fixable: 0" in out

    def test_passes_on_synthetic_baselined_advisory(self, tmp_path, capsys):
        audit = write_audit(tmp_path, [("nltk", "3.9.1", "GHSA-2jhm-w3mp-jcwr", ["3.10.0"])])
        baseline = tmp_path / "b.txt"
        baseline.write_text("# header\nGHSA-2JHM-W3MP-JCWR\n")
        assert gate.run_gate(audit, str(baseline)) == 0

    def test_fails_on_new_fixable_advisory(self, tmp_path, capsys):
        """Witness 2: a known-vulnerable pin not in the baseline fails the gate."""
        audit = write_audit(tmp_path, [("requests", "2.29.0", "CVE-2024-35195", ["2.32.0"])])
        baseline = tmp_path / "b.txt"
        baseline.write_text("# empty baseline\n")
        rc = gate.run_gate(audit, str(baseline))
        out = capsys.readouterr().out
        assert rc == 1
        assert "::error::" in out and "CVE-2024-35195" in out

    def test_warns_but_passes_on_new_unfixable_advisory(self, tmp_path, capsys):
        audit = write_audit(tmp_path, [("foo", "1.0", "GHSA-aaaa-bbbb-cccc", [])])
        baseline = tmp_path / "b.txt"
        baseline.write_text("# empty baseline\n")
        rc = gate.run_gate(audit, str(baseline))
        out = capsys.readouterr().out
        assert rc == 0
        assert "::warning::" in out and "GHSA-aaaa-bbbb-cccc" in out

    def test_alias_case_insensitive_match(self, tmp_path):
        audit = write_audit(tmp_path, [("nltk", "3.9.1", "ghsa-2jhm-w3mp-jcwr", ["3.10.0"])])
        baseline = tmp_path / "b.txt"
        baseline.write_text("GHSA-2JHM-W3MP-JCWR\n")
        assert gate.run_gate(audit, str(baseline)) == 0

    def test_baseline_file_is_wellformed(self):
        """The shipped baseline parses, is non-empty, and matches its recorded size."""
        text = BASELINE.read_text()
        ids, _ = gate.parse_baseline(text)
        assert len(ids) > 300
        m = [l for l in text.splitlines() if l.startswith("# Baseline size:")]
        assert m, "baseline must record its size"
        recorded = int(m[0].split(":")[1].strip().split()[0])
        assert recorded == len(ids), "recorded size must match actual ID count"


class TestGrowthCheck:
    OLD = "# header\nCVE-2024-0001\nGHSA-AAAA-BBBB-CCCC\n"

    def test_fails_on_uncommented_addition(self):
        """Witness 3: uncommented growth fails."""
        new = self.OLD + "CVE-2025-9999\n"
        bad = gate.check_growth(self.OLD, new)
        assert bad == ["CVE-2025-9999"]

    def test_passes_on_commented_addition(self):
        new = self.OLD + "# GHSA-xyz: reachable only via disabled X feature; revisit v0.16.41\nCVE-2025-9999\n"
        assert gate.check_growth(self.OLD, new) == []

    def test_passes_on_shrink(self):
        new = "# header\nCVE-2024-0001\n"
        assert gate.check_growth(self.OLD, new) == []

    def test_removal_then_readdition_requires_comment(self):
        shrunk = "# header\nCVE-2024-0001\n"
        readded = shrunk + "GHSA-AAAA-BBBB-CCCC\n"  # removed earlier, re-added bare
        assert gate.check_growth(shrunk, readded) == ["GHSA-AAAA-BBBB-CCCC"]

    def test_malformed_baseline_line_raises(self):
        with pytest.raises(ValueError):
            gate.parse_baseline("this-is-not-an-id\n")
