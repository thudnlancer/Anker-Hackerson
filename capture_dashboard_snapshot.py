"""Capture a token-free dashboard snapshot for a local contest run.

This is strictly read-only: it logs in to obtain a short-lived token, GETs
``/api/dashboard``, and never starts/resets a contest or calls HA. The raw
response is retained for byte-level hashing; credentials are not retained.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import requests


SECRET_KEYS = {"password", "sim_password", "ha_long_lived_token", "ha_token", "token", "access_token"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_config(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: ("<redacted>" if name.lower() in SECRET_KEYS or "password" in name.lower()
                       else _safe_config(item, name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_safe_config(item, key) for item in value]
    return value


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _result_digest(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode())


def capture(config_path: Path, output_dir: Path, local_run_id: str, phase: str,
            selected_run_index: int | None = None) -> dict[str, Any]:
    if phase not in {"prestart", "end"}:
        raise ValueError("phase must be prestart or end")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", local_run_id):
        raise ValueError("local_run_id must be 1-80 safe ASCII characters")
    stem = f"{local_run_id}.{phase}"
    raw_path = output_dir / f"{stem}.dashboard.json"
    manifest_path = output_dir / f"{stem}.manifest.json"
    if raw_path.exists() or manifest_path.exists():
        raise FileExistsError("snapshot already exists; refusing overwrite")
    pre_manifest = None
    pre_results: set[str] = set()
    if phase == "end":
        pre_path = output_dir / f"{local_run_id}.prestart.manifest.json"
        pre_raw = output_dir / f"{local_run_id}.prestart.dashboard.json"
        pre_manifest = json.loads(pre_path.read_text(encoding="utf-8"))
        if pre_manifest.get("local_run_id") != local_run_id or _sha256_bytes(pre_raw.read_bytes()) != pre_manifest.get("dashboard_sha256"):
            raise ValueError("prestart snapshot identity or SHA-256 mismatch")
        pre_dashboard = json.loads(pre_raw.read_text(encoding="utf-8"))
        pre_results = {_result_digest(item) for item in ((pre_dashboard.get("results") or {}).get("runs") or [])}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    machine = config.get("environment_machine", config)
    base = str(machine["sim_base"]).rstrip("/")
    login = requests.post(base + "/api/auth/login",
                           json={"username": machine["sim_username"], "password": machine["sim_password"]},
                           timeout=20)
    login.raise_for_status()
    token = login.json().get("token")
    if not token:
        raise RuntimeError("dashboard login returned no token")
    response = requests.get(base + "/api/dashboard",
                            headers={"Authorization": f"Bearer {token}"}, timeout=20)
    response.raise_for_status()
    raw = response.content
    dashboard = response.json()
    digest = _sha256_bytes(raw)
    contest = dashboard.get("contest") if isinstance(dashboard, dict) else None
    results = ((dashboard.get("results") or {}).get("runs") or []) if isinstance(dashboard, dict) else []
    if phase == "end" and pre_manifest.get("dashboard_endpoint") != base + "/api/dashboard":
        raise ValueError("dashboard endpoint changed since prestart")
    if phase == "prestart" and isinstance(contest, dict) and contest.get("mode") == "running":
        raise ValueError("contest already running; cannot label snapshot prestart")
    selected = None
    result_digests = [_result_digest(item) for item in results]
    new_indices = [index for index, item_digest in enumerate(result_digests) if item_digest not in pre_results]
    if phase == "end":
        if not new_indices:
            raise ValueError("no new settled run since prestart; refusing end settlement snapshot")
        if selected_run_index is not None:
            if selected_run_index < 0 or selected_run_index >= len(results) or selected_run_index not in new_indices:
                raise ValueError("selected result is not a new post-prestart run")
            selected = results[selected_run_index]
        elif len(new_indices) == 1:
            selected_run_index = new_indices[0]
            selected = results[selected_run_index]
    selected_meta = None
    if isinstance(selected, dict):
        selected_meta = {key: selected.get(key) for key in
                         ("ended_at", "track", "days", "date_range", "speed", "data_seed")}
        if selected_meta["track"] not in {"mock", "official"} or selected_meta["days"] != 7:
            raise ValueError("selected result has unexpected track or window length")
    config_safe = _safe_config(config)
    config_bytes = json.dumps(config_safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest = {
        "contract": "dashboard_snapshot_v1",
        "read_only": True,
        "local_run_id": local_run_id,
        "phase": phase,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dashboard_endpoint": base + "/api/dashboard",
        "dashboard_raw_file": raw_path.name,
        "dashboard_sha256": digest,
        "dashboard_content_type": response.headers.get("content-type"),
        "dashboard_top_keys": sorted(dashboard) if isinstance(dashboard, dict) else [],
        "contest": {key: contest.get(key) for key in
                    ("mode", "track", "start_at", "end_at", "now", "days", "date_range", "speed")}
                    if isinstance(contest, dict) else None,
        "results_count": len(results),
        "results_sha256": result_digests,
        "new_result_indices_since_prestart": new_indices if phase == "end" else None,
        "selected_result": selected_meta,
        "selected_result_index": selected_run_index if selected is not None else None,
        "selected_result_sha256": _result_digest(selected) if selected is not None else None,
        "selected_result_attribution": "provisional_new_since_prestart" if selected is not None else None,
        "prestart_dashboard_sha256": pre_manifest.get("dashboard_sha256") if pre_manifest else None,
        "config_sanitized_sha256": _sha256_bytes(config_bytes),
        "git_head": _git_head(Path(__file__).resolve().parent),
        "token_saved": False,
        "ha_writes_performed": 0,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with raw_path.open("xb") as stream:
        stream.write(raw)
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--local-run-id", required=True)
    parser.add_argument("--phase", choices=("prestart", "end"), required=True)
    parser.add_argument("--selected-run-index", type=int)
    args = parser.parse_args()
    report = capture(args.config, args.output_dir, args.local_run_id, args.phase, args.selected_run_index)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
