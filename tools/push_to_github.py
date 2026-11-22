#!/usr/bin/env python3
"""Snapshot-push the repository working tree to GitHub via the REST API.

Why this exists
---------------
On networks where the Git smart-HTTP protocol to github.com is blocked
(TLS resets) but api.github.com is reachable, `git push` cannot work.
This script commits the current working tree with the GitHub Git Data API
instead, so the repository stays the single source of truth.

Semantics
---------
* Every run snapshots the *entire* working tree (minus ignored paths) into
  one commit on the target branch. Files deleted locally are deleted on the
  remote. The commit's parent is the current branch head, so history is
  linear and never force-pushed.
* Empty repositories are bootstrapped with a Contents API PUT, then the
  snapshot commit follows.

Usage
-----
    set GITHUB_TOKEN=github_pat_...
    python tools/push_to_github.py [--branch main] [--message "..."] [--dry-run]

Token permissions: fine-grained PAT needs "Contents: Read and write" on the
repository.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"

SKIP_DIRS = {
    ".git", ".install", "__pycache__", ".venv", "venv", "node_modules",
    ".output", "output", "tmp", ".pytest_cache", ".idea", ".vscode",
    ".submission", "out", "fixtures",
}
SKIP_FILES = {".env", ".git-credentials", "*.tmp"}
SKIP_SUFFIX = (".pyc", ".pyo", ".log", ".tmp")


def api(method: str, path: str, token: str, payload=None):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "dsh-push-script",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return e.code, body


def repo_slug(repo_dir: str) -> tuple[str, str]:
    """Read owner/name from the origin remote, falling back to GITHUB_URL.txt."""
    try:
        import subprocess

        url = subprocess.check_output(
            ["git", "-C", repo_dir, "remote", "get-url", "origin"], text=True
        ).strip()
    except Exception:
        url = ""
    if not url:
        link = os.path.join(repo_dir, "GITHUB_URL.txt")
        if os.path.exists(link):
            url = open(link, encoding="utf-8").readline().strip()
    for marker in ("github.com/", "github.com:"):
        if marker in url:
            slug = url.split(marker, 1)[1]
            slug = slug.removesuffix(".git").strip("/")
            owner, _, name = slug.partition("/")
            if owner and name:
                return owner, name
    raise SystemExit("cannot determine GitHub owner/repo from origin or GITHUB_URL.txt")


def collect(repo_dir: str) -> list[tuple[str, str]]:
    files = []
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".idea"))
        for name in sorted(names):
            if name in SKIP_FILES:
                continue
            if name.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, repo_dir).replace(os.sep, "/")
            files.append((rel, full))
    return files


def blob_payload(full: str) -> dict:
    raw = open(full, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"content": base64.b64encode(raw).decode(), "encoding": "base64"}
    return {"content": text, "encoding": "utf-8"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set")

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    owner, name = repo_slug(repo_dir)
    files = collect(repo_dir)
    print(f"repo: {owner}/{name}  branch: {args.branch}  files: {len(files)}")
    for rel, _ in files:
        print("  +", rel)

    if args.dry_run:
        return 0

    # Current head, if any
    head_sha, head_tree = None, None
    status, ref = api("GET", f"/repos/{owner}/{name}/git/ref/heads/{args.branch}", token)
    if status == 200:
        head_sha = ref["object"]["sha"]
        status, commit = api("GET", f"/repos/{owner}/{name}/git/commits/{head_sha}", token)
        if status == 200:
            head_tree = commit["tree"]["sha"]

    # Bootstrap an empty repository through the Contents API
    if head_sha is None:
        first_rel, first_full = files[0]
        payload = {
            "message": args.message or "chore: initialize repository",
            "content": base64.b64encode(open(first_full, "rb").read()).decode(),
            "branch": args.branch,
        }
        status, out = api(
            "PUT", f"/repos/{owner}/{name}/contents/{urllib.parse.quote(first_rel)}", token, payload
        )
        if status not in (200, 201):
            print(f"[error] bootstrap failed: {status} {out}")
            return 1
        head_sha = out["commit"]["sha"]
        status, commit = api("GET", f"/repos/{owner}/{name}/git/commits/{head_sha}", token)
        head_tree = commit["tree"]["sha"]
        print(f"bootstrapped with {first_rel} -> {head_sha[:8]}")

    # Build a complete tree snapshot
    tree_entries = []
    for rel, full in files:
        status, blob = api("POST", f"/repos/{owner}/{name}/git/blobs", token, blob_payload(full))
        if status != 201:
            print(f"[error] blob {rel}: {status} {blob}")
            return 1
        tree_entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    status, tree = api(
        "POST", f"/repos/{owner}/{name}/git/trees", token, {"tree": tree_entries}
    )
    if status != 201:
        print(f"[error] tree: {status} {tree}")
        return 1
    if tree["sha"] == head_tree:
        print("up to date — tree unchanged, nothing to commit")
        return 0

    message = args.message or f"chore: sync snapshot ({len(files)} files)"
    status, new_commit = api(
        "POST",
        f"/repos/{owner}/{name}/git/commits",
        token,
        {"message": message, "tree": tree["sha"], "parents": [head_sha]},
    )
    if status != 201:
        print(f"[error] commit: {status} {new_commit}")
        return 1

    status, out = api(
        "PATCH",
        f"/repos/{owner}/{name}/git/refs/heads/{args.branch}",
        token,
        {"sha": new_commit["sha"], "force": False},
    )
    if status != 200:
        print(f"[error] ref update: {status} {out}")
        return 1

    print(json.dumps(
        {
            "status": "pushed",
            "repo": f"{owner}/{name}",
            "branch": args.branch,
            "commit": new_commit["sha"],
            "files": len(files),
            "url": f"https://github.com/{owner}/{name}/commit/{new_commit['sha']}",
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
