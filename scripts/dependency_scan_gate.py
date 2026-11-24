#!/usr/bin/env python3
"""Baseline-gated dependency-scan logic for LettaLocal (B-3, v0.16.39 review decision 3.1).

Policy:
  - Advisories listed in security/dependency-baseline.txt are accepted (ignored).
  - A NEW advisory WITH a fix version fails the gate.
  - A NEW advisory WITHOUT a fix version is reported (warning) but does not fail.
  - The baseline may only shrink: an added entry requires a justification comment
    on the line directly above it; uncommented additions fail.

Alias-drift caveat: the baseline stores advisory IDs as the scanner reports them
(GHSA or CVE). If a future scan reports the same advisory under a different
alias, it surfaces as "new" and fails the gate. That is deliberate: the gate
fails toward scrutiny, never toward silence.

Usage (see .github/workflows/dependency-scan.yml):
  dependency_scan_gate.py --audit audit.json --baseline security/dependency-baseline.txt
                          [--growth-check] [--head-baseline <git-ref:path>]
Exit codes: 0 = pass, 1 = gate failure (new fixable advisory or bad baseline growth).
"""
import argparse
import json
import re
import sys

ID_RE = re.compile(r"^(GHSA|CVE|PYSEC|OSV)-[A-Za-z0-9\-:.]+$", re.IGNORECASE)


def parse_baseline(text: str):
    """Return (ids, commented_ids). A commented line directly above an ID line justifies it."""
    ids = set()
    commented = set()
    prev_comment = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            prev_comment = None
            continue
        if stripped.startswith("#"):
            prev_comment = stripped
            continue
        if ID_RE.match(stripped):
            ids.add(stripped.upper())
            if prev_comment:
                commented.add(stripped.upper())
                prev_comment = None
        else:
            raise ValueError(f"Unparseable baseline line: {line!r}")
    return ids, commented


def check_growth(old_text: str, new_text: str) -> list:
    """Fail on any added ID without a justification comment directly above it."""
    old_ids, _ = parse_baseline(old_text)
    new_ids, new_commented = parse_baseline(new_text)
    return sorted(i for i in new_ids - old_ids if i not in new_commented)


def run_gate(audit_path: str, baseline_path: str) -> int:
    with open(audit_path) as f:
        data = json.load(f)
    with open(baseline_path) as f:
        baseline_ids, _ = parse_baseline(f.read())

    fixable, unfixable = [], []
    baseline_hits = 0
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            vid = (vuln.get("id") or "").upper()
            if vid in baseline_ids:
                baseline_hits += 1
                continue
            entry = f"{dep.get('name')}=={dep.get('version')}: {vuln.get('id')} ({(vuln.get('description') or '')[:120]})"
            (fixable if vuln.get("fix_versions") else unfixable).append(entry)

    for e in unfixable:
        print(f"::warning::NEW advisory with NO fix available (reported, not failing): {e}")
    for e in fixable:
        print(f"::error::NEW advisory with a fix available — update the lockfile or justify+baseline it: {e}")

    print(f"baseline accepted: {baseline_hits} advisory hits; new fixable: {len(fixable)}; new unfixable: {len(unfixable)}")
    return 1 if fixable else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--growth-check", action="store_true",
                    help="also verify the baseline did not grow with uncommented IDs (vs --head-baseline)")
    ap.add_argument("--head-baseline", default=None,
                    help="path to a copy of the baseline from the base ref, for growth checking")
    args = ap.parse_args()

    if args.growth_check:
        if not args.head_baseline:
            print("::error::--growth-check requires --head-baseline")
            return 1
        with open(args.head_baseline) as f:
            old_text = f.read()
        with open(args.baseline) as f:
            new_text = f.read()
        bad = check_growth(old_text, new_text)
        for i in bad:
            print(f"::error::Baseline grew with UNCOMMENTED entry {i} — additions need a justification comment (what/why/when revisited)")
        if bad:
            return 1

    return run_gate(args.audit, args.baseline)


if __name__ == "__main__":
    sys.exit(main())
