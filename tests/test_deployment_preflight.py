import json

from scripts.maintenance.deployment_preflight import build_report


def test_preflight_is_read_only_and_reports_expected_commit(monkeypatch, tmp_path):
    import scripts.maintenance.deployment_preflight as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    required = ("src", "scripts/online/run_live.py", "config/requirements.txt", "scripts/maintenance/remote_dry_run_ctl.sh",
                "scripts/maintenance/install_remote_dry_run_recovery.sh", "config/templates/remote_runtime_config.example.json")
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts/online").mkdir(parents=True)
    (tmp_path / "scripts/maintenance").mkdir(parents=True)
    (tmp_path / "config/templates").mkdir(parents=True)
    (tmp_path / "src" / "module.py").write_text("x", encoding="utf-8")
    for name in required[1:5]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "config/templates/remote_runtime_config.example.json").write_text(json.dumps({
        "mode": "dry-run", "live_enabled": False, "allow_ha_write": False,
        "allow_formal_contest": False}), encoding="utf-8")
    monkeypatch.setattr(module, "git", lambda *args: (
        "abc123" if args[0] == "rev-parse" else
        "\n".join((*required[1:5], "src/module.py", required[5])) if args[0] == "ls-files" else ""))
    (tmp_path / "local_online_config.json").write_text("private", encoding="utf-8")
    report = build_report("abc123")
    assert report["ready_for_browser_terminal_dry_run"] is True
    assert report["remote_actions_performed"] == 0
    assert report["ha_writes_performed"] == 0
    assert report["credential_files_tracked"] == []
    assert report["required_tracked"]["src"] is True
    assert report["required_sha256"]["src"]


def test_preflight_rejects_tracked_credentials_and_untracked_required_file(monkeypatch, tmp_path):
    import scripts.maintenance.deployment_preflight as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    for name in module.REQUIRED[1:]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "git", lambda *args: (
        "abc123" if args[0] == "rev-parse" else
        "src/module.py\nremote_runtime_config.json" if args[0] == "ls-files" else ""))
    report = build_report("abc123")
    assert report["ready_for_browser_terminal_dry_run"] is False
    assert report["credential_files_tracked"] == ["remote_runtime_config.json"]
    assert report["required_tracked"]["scripts/online/run_live.py"] is False


def test_preflight_rejects_dirty_tree_or_live_template(monkeypatch, tmp_path):
    import scripts.maintenance.deployment_preflight as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "config/templates/remote_runtime_config.example.json").parent.mkdir(parents=True)
    (tmp_path / "config/templates/remote_runtime_config.example.json").write_text(json.dumps({
        "mode": "live", "live_enabled": True, "allow_ha_write": True,
        "allow_formal_contest": True}), encoding="utf-8")
    monkeypatch.setattr(module, "git", lambda *args: "abc123" if args[0] == "rev-parse" else "modified")
    report = build_report("abc123")
    assert report["ready_for_browser_terminal_dry_run"] is False
    assert report["worktree_clean"] is False
    assert report["template_is_dry_run"] is False
