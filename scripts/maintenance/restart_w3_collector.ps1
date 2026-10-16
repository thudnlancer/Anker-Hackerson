param(
    [Parameter(Mandatory=$true)][ValidateRange(1,[int]::MaxValue)][int]$ExpectedPid,
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$CacheDir,
    [Parameter(Mandatory=$true)][string]$HealthDir,
    [Parameter(Mandatory=$true)][string]$EvidenceDir,
    [Parameter(Mandatory=$true)][string]$NodeId,
    [ValidateRange(1,3600)][int]$IntervalSeconds = 5,
    [string]$DeviceTimezone,
    [switch]$Restart
)

# Read-only process ownership preflight by default. -Restart is the only path
# that calls Stop-Process, and it requires explicit paths and PID ownership.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
function Resolve-WorkspacePath([string]$value) {
    if ([IO.Path]::IsPathRooted($value)) { return [IO.Path]::GetFullPath($value) }
    return [IO.Path]::GetFullPath((Join-Path $root $value))
}
$configPath = Resolve-WorkspacePath $Config
$cachePath = Resolve-WorkspacePath $CacheDir
$healthPath = Resolve-WorkspacePath $HealthDir
$evidencePath = Resolve-WorkspacePath $EvidenceDir
$outputs = [IO.Path]::GetFullPath((Join-Path $root 'outputs')) + [IO.Path]::DirectorySeparatorChar
foreach ($candidate in @($cachePath, $healthPath, $evidencePath)) {
    if (-not $candidate.StartsWith($outputs, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'cache, health, and evidence paths must be under workspace outputs'
    }
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'collector config missing' }
if (-not (Test-Path -LiteralPath $cachePath -PathType Container)) { throw 'cache directory missing' }
if (-not (Test-Path -LiteralPath $healthPath -PathType Container)) { throw 'health directory missing' }
if (Test-Path -LiteralPath $evidencePath) { throw 'evidence directory exists; choose a new directory' }
if (-not ($NodeId -match '^[A-Za-z0-9_-]+$')) { throw 'node id is not filename-safe' }
$heartbeatPath = Join-Path $healthPath "heartbeat_$NodeId.json"
if (-not (Test-Path -LiteralPath $heartbeatPath -PathType Leaf)) { throw 'heartbeat missing' }
$heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$heartbeat.writes_performed -ne 0) {
    throw 'heartbeat violates read-only contract; no process control performed'
}
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$ExpectedPid"
if (-not $process -or [int]$heartbeat.pid -ne $ExpectedPid -or
    $process.CommandLine -notlike '*collect_online_history.py*' -or
    $process.CommandLine -notlike "*--node-id $NodeId*" -or
    -not $process.CommandLine.Contains($configPath) -or
    -not $process.CommandLine.Contains($cachePath) -or
    -not $process.CommandLine.Contains($healthPath)) {
    throw 'process ownership check failed; no process control performed'
}
$preflight = [ordered]@{mode=if ($Restart) {'restart'} else {'preflight_only'};
    pid=$ExpectedPid; node_id=$NodeId; process_created=$process.CreationDate;
    heartbeat_cycle=$heartbeat.cycle; consecutive_errors=$heartbeat.consecutive_errors;
    writes_performed=0; config=$configPath; cache=$cachePath; health=$healthPath; evidence=$evidencePath}
if (-not $Restart) { $preflight | ConvertTo-Json -Compress; return }

New-Item -ItemType Directory -Path $evidencePath | Out-Null
$process | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidencePath 'process_before.json') -Encoding UTF8
Copy-Item -LiteralPath $heartbeatPath -Destination (Join-Path $evidencePath 'heartbeat_before.json')
Stop-Process -Id $ExpectedPid
for ($i=0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 250
    if (-not (Get-Process -Id $ExpectedPid -ErrorAction SilentlyContinue)) { break }
}
if (Get-Process -Id $ExpectedPid -ErrorAction SilentlyContinue) { throw 'owned collector did not stop; refusing replacement process' }
$replacementEvidence = Join-Path $evidencePath 'replacement'
$launchArgs = @((Join-Path $root 'launch_w3_collector.py'), '--config', $configPath,
    '--cache-dir', $cachePath, '--health-dir', $healthPath, '--evidence-dir', $replacementEvidence,
    '--node-id', $NodeId, '--iterations', '0', '--interval-seconds', [string]$IntervalSeconds)
if ($DeviceTimezone) { $launchArgs += @('--device-timezone', $DeviceTimezone) }
$pythonExe = $process.ExecutablePath
$newPid = [int]((& $pythonExe @launchArgs | Select-Object -Last 1))
if ($newPid -le 0) { throw 'replacement launcher did not return a PID' }
for ($i=0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 2
    $current = Get-Content -LiteralPath $heartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$current.pid -eq $newPid -and [int]$current.cycle -ge 2) { break }
}
if ([int]$current.pid -ne $newPid -or [int]$current.cycle -lt 2 -or [int]$current.writes_performed -ne 0) {
    throw 'replacement heartbeat did not verify; inspect evidence before further action'
}
Copy-Item -LiteralPath $heartbeatPath -Destination (Join-Path $evidencePath 'heartbeat_after.json')
[ordered]@{old_pid=$ExpectedPid; new_pid=$newPid; restarted_at=[DateTimeOffset]::UtcNow.ToString('o'); writes_performed=0} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidencePath 'restart.json') -Encoding UTF8
[ordered]@{new_pid=$newPid; old_pid=$ExpectedPid; evidence=$evidencePath; writes_performed=0} | ConvertTo-Json -Compress
