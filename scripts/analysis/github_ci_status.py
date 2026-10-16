"""Print recent GitHub Actions runs without ever prompting for credentials.

Token resolution order (first hit wins):
1. GITHUB_TOKEN environment variable
2. config/local/github_token.txt (git-ignored; one personal access token per line)

Never calls git-credential/gh, so it can never pop up an interactive dialog.
Exit code is 0 when the newest run succeeded, 2 on any API/permission error,
1 otherwise (in progress or failure) — safe for shell gating.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

REPO = "thudnlancer/Anker-Hackerson"
TOKEN_FILE = Path(__file__).resolve().parents[2] / "config" / "local" / "github_token.txt"


def load_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text(encoding="utf-8").splitlines()[0].strip() or None
    return None


def main() -> int:
    token = load_token()
    if not token:
        print(f"no token: set GITHUB_TOKEN or write one PAT line to {TOKEN_FILE}")
        return 2
    url = f"https://api.github.com/repos/{REPO}/actions/runs?per_page=5"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "User-Agent": "ci-status",
        "Accept": "application/vnd.github+json"})
    try:
        runs = json.load(urllib.request.urlopen(request, timeout=30))["workflow_runs"]
    except urllib.error.HTTPError as exc:
        print(f"API error {exc.code}: token missing repo access? "
              f"(fine-grained PAT needs {REPO}: Actions=Read)")
        return 2
    except Exception as exc:
        print(f"API error: {type(exc).__name__}: {exc}")
        return 2
    for run in runs:
        print(run["id"], run["head_branch"], run["status"], run["conclusion"],
              run["head_sha"][:8])
    newest = runs[0] if runs else None
    return 0 if newest and newest["conclusion"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
