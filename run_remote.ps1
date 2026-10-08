param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [ValidateSet("start", "dry-run", "live", "status", "logs", "stop")]
    [string]$Action = "status",
    [int]$LogLines = 80
)

$ErrorActionPreference = "Stop"
$config = Get-Content -Raw -Encoding utf8 $ConfigPath | ConvertFrom-Json
$connection = $config.connection
$machine = $config.remote_machine
if (-not $connection.private_key_path -or $connection.private_key_path.StartsWith("<")) { throw "connection.private_key_path is not filled in" }
if (-not (Test-Path -LiteralPath $connection.private_key_path -PathType Leaf)) { throw "SSH private key does not exist" }
$remote = "$($connection.username)@$($connection.host)"
$work = [string]$machine.working_directory
$strictHostKey = if ($connection.known_hosts_policy -eq "strict") { "yes" } else { "accept-new" }
$sshArgs = @("-i", $connection.private_key_path, "-p", [string]$connection.port, "-o", "ConnectTimeout=$($connection.connect_timeout_seconds)", "-o", "StrictHostKeyChecking=$strictHostKey", $remote)

switch ($Action) {
    { $_ -in @("start", "dry-run", "live") } {
        $runMode = if ($Action -eq "live") { "live" } else { "dry-run" }
        if ($runMode -eq "live") { Write-Warning "Requesting remote HA write mode; the remote configuration must pass all safety gates" }
        $command = "cd '$work' && mkdir -p logs && if test -f policy.pid && kill -0 `$(cat policy.pid) 2>/dev/null; then echo ALREADY_RUNNING; else nohup '$($machine.venv_directory)/bin/python' run_live.py --config remote_runtime_config.json --mode $runMode >> logs/live.log 2>&1 </dev/null & echo `$! > policy.pid; echo STARTED; fi"
        & ssh @sshArgs $command
    }
    "status" {
        & ssh @sshArgs "cd '$work' && if test -f policy.pid && kill -0 `$(cat policy.pid) 2>/dev/null; then echo RUNNING; else echo STOPPED; fi"
    }
    "logs" {
        & ssh @sshArgs "cd '$work' && tail -n $LogLines logs/live.log"
    }
    "stop" {
        & ssh @sshArgs "cd '$work' && if test -f policy.pid; then kill `$(cat policy.pid) 2>/dev/null || true; rm -f policy.pid; fi"
    }
}
if ($LASTEXITCODE -ne 0) { throw "Remote action failed: $Action" }
