"""Launcher arguments are explicit and never encode an old mock window."""

from pathlib import Path

import pytest

from scripts.online.launch_w3_collector import ROOT, command


def test_command_uses_explicit_absolute_paths(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    cache = tmp_path / "cache"
    health = tmp_path / "health"
    args = command(config, cache, health, "nodeA", 4, 5.0, None)
    assert args[2] == str(ROOT / "collect_online_history.py")
    assert args[args.index("--config") + 1] == str(config.resolve())
    assert args[args.index("--cache-dir") + 1] == str(cache.resolve())
    assert args[args.index("--health-dir") + 1] == str(health.resolve())
    assert args[args.index("--iterations") + 1] == "4"
    assert "--device-timezone" not in args
    assert "mock2_running_20261014" not in " ".join(args)


def test_invalid_or_missing_inputs_refuse_to_launch(tmp_path):
    config = tmp_path / "config.json"
    with pytest.raises(ValueError, match="config must exist"):
        command(config, tmp_path / "cache", tmp_path / "health", "nodeA", 1, 5, None)
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="filename-safe"):
        command(config, tmp_path / "cache", tmp_path / "health", "../other", 1, 5, None)
    with pytest.raises(ValueError, match="nonnegative"):
        command(config, tmp_path / "cache", tmp_path / "health", "nodeA", -1, 5, None)
