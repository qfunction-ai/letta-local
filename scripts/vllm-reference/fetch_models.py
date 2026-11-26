#!/usr/bin/env python3
"""Fetch model weights at pinned revisions and verify every file's SHA-256 (ER-8.3). DRAFT, 2026-11-26.

Two modes:

  record  First time a model enters the set. Downloads the pinned revision and WRITES the hashes into the lock
          file. The output is a proposal: a person reviews the lock-file diff (trainer, source, revision, file
          list, license) before it's committed. Never run `record` as part of a release run.

  verify  Every release run. Downloads the pinned revision, then requires that the set of files on disk equals
          the set in the lock file exactly (nothing missing, nothing extra) and that every hash matches.
          Any difference exits non-zero and the run stops.

Lock file (models.lock.json):
{
  "models": [
    {
      "name": "gemma-4-26b",                 # directory name under the weights dir; used as VLLM_MODEL_DIR
      "trainer": "Google",
      "repo": "google/<repo>",               # the publisher's own Hugging Face organization (ER-8.3)
      "revision": "<40-char commit hash>",   # never a branch or tag
      "license": "<name and URL>",
      "files": {"config.json": "<sha256>", "...": "..."}
    }
  ]
}

Requires `huggingface_hub` on the machine that downloads (the GPU instance), not inside the vLLM container.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
CHUNK = 8 * 1024 * 1024


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def files_on_disk(root: Path) -> dict[str, Path]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise SystemExit(f"FAIL: symlink in model dir (not allowed): {p}")
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if rel.startswith(".cache/") or rel == ".gitattributes":
                continue
            out[rel] = p
    return out


def download(model: dict, dest: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit("FAIL: huggingface_hub is not installed on this machine")
    if not REVISION_RE.match(model.get("revision", "")):
        raise SystemExit(f"FAIL: {model['name']}: revision must be a 40-char commit hash, got {model.get('revision')!r}")
    snapshot_download(repo_id=model["repo"], revision=model["revision"], local_dir=str(dest))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["record", "verify"])
    ap.add_argument("--lock", default="models.lock.json", type=Path)
    ap.add_argument("--weights-dir", required=True, type=Path)
    ap.add_argument("--model", help="only this model name")
    ap.add_argument("--no-download", action="store_true", help="verify files already on disk")
    args = ap.parse_args()

    lock = json.loads(args.lock.read_text())
    models = [m for m in lock["models"] if not args.model or m["name"] == args.model]
    if not models:
        raise SystemExit("FAIL: no matching model in lock file")

    failures = 0
    for m in models:
        dest = args.weights_dir / m["name"]
        if not args.no_download:
            download(m, dest)
        on_disk = files_on_disk(dest)

        if args.mode == "record":
            m["files"] = {rel: sha256_of(p) for rel, p in on_disk.items()}
            print(f"RECORDED {m['name']}: {len(m['files'])} files at {m['revision']} (review before committing)")
            continue

        expected = m.get("files") or {}
        if not expected:
            print(f"FAIL {m['name']}: lock file has no hashes (run `record` and review first)")
            failures += 1
            continue
        missing = sorted(set(expected) - set(on_disk))
        extra = sorted(set(on_disk) - set(expected))
        bad = sorted(rel for rel in set(expected) & set(on_disk) if sha256_of(on_disk[rel]) != expected[rel])
        for label, items in (("missing", missing), ("unexpected", extra), ("hash mismatch", bad)):
            for rel in items:
                print(f"FAIL {m['name']}: {label}: {rel}")
        if missing or extra or bad:
            failures += 1
        else:
            print(f"PASS {m['name']}: {len(expected)} files match at {m['revision']}")

    if args.mode == "record":
        args.lock.write_text(json.dumps(lock, indent=2, sort_keys=False) + "\n")
        return 0
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
