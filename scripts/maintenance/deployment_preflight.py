"""Generate a local, read-only deployment readiness report.

This never connects to SSH/EC2, reads credentials, starts a runner, or writes
outside the requested report. It proves only that the checked-out tree contains
the files and dry-run controls required by the browser-terminal runbook.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "src", "scripts/online/run_live.py", "config/requirements.txt", "scripts/maintenance/remote_dry_run_ctl.sh",
    "scripts/maintenance/install_remote_dry_run_recovery.sh", "config/templates/remote_runtime_config.example.json",
)
SECRET_NAMES = {"remote_runtime_config.json", "local_online_config.json", "config.json"}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_report(expected_commit: str | None = None) -> dict:
    head = git("rev-parse", "HEAD")
    porcelain = git("status", "--porcelain")
    tracked = set(git("ls-files", "--cached", "--", *REQUIRED, *SECRET_NAMES).splitlines())
    required = {name: (ROOT / name).exists() for name in REQUIRED}
    required_tracked = {
        name: (name in tracked if (ROOT / name).is_file()
               else any(p.startswith(name + "/") for p in tracked))
        for name in REQUIRED
    }
    template = ROOT / "config/templates/remote_runtime_config.example.json"
    raw = json.loads(template.read_text(encoding="utf-8")) if template.is_file() else {}
    safety = {key: raw.get(key) for key in ("mode", "live_enabled", "allow_ha_write", "allow_formal_contest")}
    forbidden_tracked = sorted(SECRET_NAMES & tracked)
    hashes = {}
    for name, exists in required.items():
        if not exists:
            continue
        if (ROOT / name).is_file():
            hashes[name] = sha256(ROOT / name)
        else:
            digest = hashlib.sha256()
            for relative in sorted(p for p in tracked if p.startswith(name + "/")):
                child = ROOT / relative
                if child.is_file():
                    digest.update(relative.encode("utf-8"))
                    digest.update(sha256(child).encode("ascii"))
            hashes[name] = digest.hexdigest()
    report = {
        "schema": "deployment_preflight/v1", "read_only": True,
        "git_head": head, "expected_commit": expected_commit,
        "commit_matches": expected_commit is None or head == expected_commit,
        "worktree_clean": not porcelain, "worktree_status": porcelain.splitlines(),
        "required_paths": required, "missing_paths": [name for name, ok in required.items() if not ok],
        "required_tracked": required_tracked,
        "required_sha256": hashes,
        "dry_run_safety": safety,
        "template_is_dry_run": safety == {"mode": "dry-run", "live_enabled": False,
                                           "allow_ha_write": False, "allow_formal_contest": False},
        "credential_files_tracked": forbidden_tracked,
        "remote_actions_performed": 0,
        "ha_writes_performed": 0,
    }
    report["ready_for_browser_terminal_dry_run"] = bool(
        report["commit_matches"] and report["worktree_clean"] and
        not report["missing_paths"] and all(required_tracked.values()) and
        report["template_is_dry_run"] and not forbidden_tracked)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.expected_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output),
                      "ready_for_browser_terminal_dry_run": report["ready_for_browser_terminal_dry_run"],
                      "git_head": report["git_head"], "remote_actions_performed": 0}, ensure_ascii=False))
    if not report["ready_for_browser_terminal_dry_run"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
