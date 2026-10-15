#!/usr/bin/env bash
# Install a user-level systemd service after the browser-terminal bootstrap.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$ROOT/remote_runtime_config.json" ]] || { echo 'missing remote_runtime_config.json' >&2; exit 1; }
mkdir -p "$ROOT/logs"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/solarbank-dry-run.service" <<EOF
[Unit]
Description=Solarbank read-only MILP dry-run
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=$ROOT/remote_dry_run_ctl.sh foreground
ExecStopPost=/usr/bin/rm -f $ROOT/policy.pid
StandardOutput=append:$ROOT/logs/live.log
StandardError=append:$ROOT/logs/live.log
Restart=on-failure
RestartSec=10
TimeoutStopSec=20

[Install]
WantedBy=default.target
EOF
chmod 700 "$ROOT/remote_dry_run_ctl.sh"
systemctl --user daemon-reload
systemctl --user enable --now solarbank-dry-run.service
systemctl --user --no-pager status solarbank-dry-run.service || true
echo "For reboot recovery, enable user lingering once: sudo loginctl enable-linger $USER"
