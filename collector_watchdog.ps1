# B4.4 collector watchdog (local node): restart the read-only collector when
# the process is gone or its heartbeat is DEAD, with crash-loop circuit
# breaking per B4_COLLECTION_REDUNDANCY_DESIGN 5.2.
#
#   - process absent            -> start immediately
#   - heartbeat DEAD (>60s)     -> kill and start
#   - >=3 restarts within 5 min -> backoff 5/15/60 min, then give up and write
#                                  outputs/collector_health/nodeA.disabled
#   - disabled marker present   -> do nothing (manual check required)
#
# Detection fallback: if collector_monitor.py has not produced
# monitor_status.json yet, the heartbeat file age is used directly.
# This script never touches HA or the runtime config; the collector it
# starts is strictly read-only (writes_performed == 0).
#
# Install (current user, every 2 minutes):
#   schtasks /Create /TN SolixCollectorWatchdog /SC MINUTE /MO 2 /F `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\Anker黑客松\实验环境\collector_watchdog.ps1"
param(
    [string]$NodeId = "nodeA",
    [int]$DeadAfterSeconds = 60,
    [int]$CrashLoopWindowSeconds = 300,
    [int]$CrashLoopMaxRestarts = 3,
    [string]$OfflineFixture = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$fixture = $null
if ($OfflineFixture) {
    $fixture = Get-Content -Raw -Encoding UTF8 -LiteralPath $OfflineFixture | ConvertFrom-Json
    $root = Split-Path -Parent (Resolve-Path -LiteralPath $OfflineFixture)
}
$healthDir = Join-Path $root "outputs/collector_health"
$logsDir = Join-Path $root "logs"
$statePath = Join-Path $healthDir "watchdog_state_$NodeId.json"
$disabledPath = Join-Path $healthDir "$NodeId.disabled"
New-Item -ItemType Directory -Force -Path $healthDir, $logsDir | Out-Null

function Write-Action($action, $detail) {
    $line = @{ ts = (Get-Date).ToUniversalTime().ToString("o"); node = $NodeId; action = $action; detail = $detail }
    Write-Output ($line | ConvertTo-Json -Compress)
    ($line | ConvertTo-Json -Compress) | Add-Content -LiteralPath (Join-Path $healthDir "watchdog.jsonl") -Encoding utf8
}

if (Test-Path -LiteralPath $disabledPath) {
    Write-Action "disabled_skip" "manual check required; delete $disabledPath to re-enable"
    exit 3
}

$state = @{ restarts = @(); backoff_until = $null; backoff_stage = 0 }
if (Test-Path -LiteralPath $statePath) {
    try { $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json } catch { }
    if (-not $state.restarts) { $state | Add-Member -NotePropertyName restarts -NotePropertyValue @() -Force }
    if (-not $state.PSObject.Properties["backoff_stage"]) { $state | Add-Member -NotePropertyName backoff_stage -NotePropertyValue 0 -Force }
    if (-not $state.PSObject.Properties["backoff_until"]) { $state | Add-Member -NotePropertyName backoff_until -NotePropertyValue $null -Force }
}

$now = Get-Date
if ($fixture) { $now = [datetime]$fixture.now }
if ($state.backoff_until -and ([datetime]$state.backoff_until) -gt $now) {
    Write-Action "backoff_skip" ("until " + ([datetime]$state.backoff_until).ToUniversalTime().ToString("o"))
    exit 0
}

function Get-CollectorProcesses {
    if ($fixture) {
        if ($fixture.process_exists) { [pscustomobject]@{ ProcessId = -1 } }
        return
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "collect_online_history" -and
            $_.CommandLine -match ("--node-id\s+" + [regex]::Escape($NodeId)) }
}

function Start-Collector {
    if ($fixture) { Write-Action "started" "offline simulated process; no network or process launch"; return }
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $outLog = Join-Path $logsDir "collector_$stamp.out.log"
    $errLog = Join-Path $logsDir "collector_$stamp.err.log"
    $py = (Get-Command python -ErrorAction Stop).Source
    Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Hidden `
        -ArgumentList @("collect_online_history.py", "--config", "local_online_config.json", "--iterations", "0", "--node-id", $NodeId,
                        "--health-dir", $healthDir) `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Write-Action "started" "logs: $outLog"
}

# Is a restart needed? (process missing, or heartbeat DEAD / monitor says DEAD)
$processes = @(Get-CollectorProcesses)
$needsRestart = $false
$reason = ""
if ($processes.Count -eq 0) {
    $needsRestart = $true; $reason = "process_absent"
} else {
    $monitorStatus = Join-Path $healthDir "monitor_status.json"
    $dead = $false
    if (Test-Path -LiteralPath $monitorStatus) {
        try {
            $status = Get-Content -Raw -Encoding UTF8 -LiteralPath $monitorStatus | ConvertFrom-Json
            $monitorAge = ($now - [datetime]$status.checked_at).TotalSeconds
            if ($monitorAge -ge 0 -and $monitorAge -le $DeadAfterSeconds -and
                $status.nodes.$NodeId.state -eq "DEAD") {
                $dead = $true; $reason = "monitor_state_DEAD"
            }
        } catch { }
    }
    if (-not $dead) {
        $heartbeatPath = Join-Path $healthDir "heartbeat_$NodeId.json"
        if (Test-Path -LiteralPath $heartbeatPath) {
            try {
                $heartbeat = Get-Content -Raw -Encoding UTF8 -LiteralPath $heartbeatPath | ConvertFrom-Json
                $age = ($now - [datetime]$heartbeat.last_cycle_at).TotalSeconds
                if ($age -gt $DeadAfterSeconds) { $dead = $true; $reason = "heartbeat_age_" + [int]$age + "s" }
            } catch { }
        }
    }
    if ($dead) { $needsRestart = $true }
}

if (-not $needsRestart) {
    Write-Action "healthy_skip" ("processes: " + ($processes.ProcessId -join ","))
    exit 0
}

# Crash-loop accounting: prune the window, then decide backoff or disable.
$windowStart = $now.AddSeconds(-$CrashLoopWindowSeconds)
$recent = @($state.restarts | Where-Object { ([datetime]$_) -gt $windowStart })
if ($recent.Count -ge $CrashLoopMaxRestarts) {
    $backoffs = @(5, 15, 60)
    $stage = [int]$state.backoff_stage
    if ($stage -ge $backoffs.Count) {
        @{ disabled_at = $now.ToUniversalTime().ToString("o"); node = $NodeId;
           reason = "crash_loop_breaker_tripped_manual_check_required" } |
            ConvertTo-Json | Set-Content -LiteralPath $disabledPath -Encoding utf8
        Write-Action "disabled" "crash-loop breaker tripped after backoff ladder"
        exit 3
    }
    $until = $now.AddMinutes($backoffs[$stage])
    $state.backoff_stage = $stage + 1
    $state.backoff_until = $until.ToUniversalTime().ToString("o")
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Action "backoff_armed" ("stage $($stage + 1), until " + $state.backoff_until)
    exit 0
}

foreach ($proc in $processes) {
    Write-Action "stopping" ("pid " + $proc.ProcessId + " (" + $reason + ")")
    if (-not $fixture) { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Collector
$state.restarts = @($recent + $now.ToUniversalTime().ToString("o"))
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
Write-Action "restarted" $reason
exit 0
