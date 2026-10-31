#!/usr/bin/env python3
"""Push this repo to GitHub over the REST API.

Why not `git push`: some networks (corporate proxies) allow api.github.com
but drop the CONNECT tunnel to github.com, which kills the git smart-HTTP
protocol. The Git Data API lives on api.github.com, so it works anyway.

Usage:
    export GITHUB_TOKEN=ghp_xxx        # needs `repo` scope
    python tools/push_to_github.py --repo WraithN/dtc-ads-video-ai-generator

    # or pass it explicitly / put it in .env as GITHUB_TOKEN=...
    python tools/push_to_github.py --repo owner/name --token ghp_xxx

The script pushes the currently committed HEAD as a single commit on the
target branch, creating the branch if the repo is empty.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# minimal HTTP
# --------------------------------------------------------------------------


def _request(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"message": body[:500]}
        return e.code, parsed


# --------------------------------------------------------------------------
# git plumbing (local only -- no network)
# --------------------------------------------------------------------------


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def head_files() -> list[tuple[str, str]]:
    """Return [(path, blob_sha)] for every file in HEAD."""
    lines = git("ls-tree", "-r", "HEAD").splitlines()
    out = []
    for line in lines:
        # 100644 blob <sha>\t<path>
        meta, path = line.split("\t", 1)
        _mode, _type, sha = meta.split()
        out.append((path, sha))
    return out


def head_message() -> str:
    return git("log", "-1", "--format=%B")


def head_author() -> tuple[str, str]:
    name = git("log", "-1", "--format=%an")
    email = git("log", "-1", "--format=%ae")
    date = git("log", "-1", "--format=%aI")
    return name, email, date


# --------------------------------------------------------------------------
# push
# --------------------------------------------------------------------------


def push(repo: str, token: str, branch: str = "main") -> str:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise SystemExit(f"--repo must be owner/name, got {repo!r}")

    # 1. resolve the base commit (empty repo -> no base)
    status, repo_info = _request("GET", f"{API}/repos/{owner}/{name}", token)
    if status == 404:
        raise SystemExit(
            f"Repo {repo} not found or token lacks access.\n"
            f"  -> create it on github.com first (public, no README),\n"
            f"     and use a token with `repo` scope."
        )
    if status >= 400:
        raise SystemExit(f"GitHub said {status}: {repo_info}")

    default_branch = repo_info.get("default_branch") or branch

    status, ref = _request(
        "GET", f"{API}/repos/{owner}/{name}/git/ref/heads/{branch}", token
    )
    base_sha = None
    if status == 200:
        base_sha = ref["object"]["sha"]
    elif status != 404:
        raise SystemExit(f"ref lookup failed {status}: {ref}")

    # 2. upload every blob from HEAD
    files = head_files()
    print(f"uploading {len(files)} blobs ...")
    tree_entries = []
    for i, (path, local_sha) in enumerate(files, 1):
        raw_bytes = subprocess.run(
            ["git", "cat-file", "blob", local_sha],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        # base64 works for both text and binary; the API decodes it either way
        payload = {
            "encoding": "base64",
            "content": base64.b64encode(raw_bytes).decode(),
        }
        status, blob = _request(
            "POST", f"{API}/repos/{owner}/{name}/git/blobs", token, payload
        )
        if status >= 400:
            raise SystemExit(f"blob {path} failed {status}: {blob}")
        tree_entries.append({"path": path, "mode": "100644", "type": "blob",
                             "sha": blob["sha"]})
        if i % 10 == 0 or i == len(files):
            print(f"  {i}/{len(files)}")

    # 3. tree
    tree_payload = {"tree": tree_entries}
    if base_sha:
        tree_payload["base_tree"] = base_sha
    status, tree = _request(
        "POST", f"{API}/repos/{owner}/{name}/git/trees", token, tree_payload
    )
    if status >= 400:
        raise SystemExit(f"tree failed {status}: {tree}")
    print(f"tree: {tree['sha']}")

    # 4. commit
    name_a, email_a, date_a = head_author()
    commit_payload = {
        "message": head_message() or "push via API",
        "tree": tree["sha"],
        "author": {"name": name_a, "email": email_a, "date": date_a},
        "committer": {"name": name_a, "email": email_a, "date": date_a},
    }
    if base_sha:
        commit_payload["parents"] = [base_sha]
    status, commit = _request(
        "POST", f"{API}/repos/{owner}/{name}/git/commits", token, commit_payload
    )
    if status >= 400:
        raise SystemExit(f"commit failed {status}: {commit}")
    print(f"commit: {commit['sha']}")

    # 5. ref
    if base_sha:
        status, resp = _request(
            "PATCH",
            f"{API}/repos/{owner}/{name}/git/refs/heads/{branch}",
            token,
            {"sha": commit["sha"], "force": False},
        )
    else:
        status, resp = _request(
            "POST",
            f"{API}/repos/{owner}/{name}/git/refs",
            token,
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    if status >= 400:
        raise SystemExit(f"ref update failed {status}: {resp}")

    url = f"https://github.com/{owner}/{name}"
    print(f"\npushed -> {url}/tree/{branch}")
    return url


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="WraithN/dtc-ads-video-ai-generator",
                    help="owner/name (default: %(default)s)")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--token", default=None,
                    help="GitHub PAT with repo scope (or set GITHUB_TOKEN)")
    args = ap.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        # last-ditch: look in .env without importing the whole config stack
        env_file = ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("GITHUB_TOKEN="):
                    token = s.split("=", 1)[1].strip().strip("'\"")
                    break
    if not token:
        raise SystemExit(
            "No GitHub token found.\n"
            "  export GITHUB_TOKEN=ghp_xxx   (needs `repo` scope)\n"
            "  or: python tools/push_to_github.py --token ghp_xxx"
        )

    # sanity: never push secrets
    tracked = [p for p, _ in head_files()]
    leaked = [p for p in tracked if p == ".env" or p.endswith((".key", ".pem"))]
    if leaked:
        raise SystemExit(f"refusing to push secret-looking files: {leaked}")

    push(args.repo, token, args.branch)


if __name__ == "__main__":
    main()
