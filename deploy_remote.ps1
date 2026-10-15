param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [string]$RuntimeConfigPath = "",
    [switch]$UploadRuntimeConfig
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$config = Get-Content -Raw -Encoding utf8 $ConfigPath | ConvertFrom-Json
$connection = $config.connection
$machine = $config.remote_machine
if (-not $connection.private_key_path -or $connection.private_key_path.StartsWith("<")) {
    throw "connection.private_key_path is not filled in"
}
if (-not (Test-Path -LiteralPath $connection.private_key_path -PathType Leaf)) {
    throw "SSH private key does not exist: $($connection.private_key_path)"
}
$remote = "$($connection.username)@$($connection.host)"
$strictHostKey = if ($connection.known_hosts_policy -eq "strict") { "yes" } else { "accept-new" }
$sshArgs = @("-i", $connection.private_key_path, "-p", [string]$connection.port, "-o", "ConnectTimeout=$($connection.connect_timeout_seconds)", "-o", "StrictHostKeyChecking=$strictHostKey", $remote)
$work = [string]$machine.working_directory

Write-Host "Checking SSH connectivity to $remote ..."
& ssh @sshArgs "mkdir -p '$work' '$work/logs' '$work/models'"
if ($LASTEXITCODE -ne 0) { throw "SSH connectivity check failed" }

$items = @("src", "run_live.py", "run_online_dry_run.py", "requirements.txt", "models",
    "remote_dry_run_ctl.sh", "install_remote_dry_run_recovery.sh")
foreach ($item in $items) {
    $local = Join-Path $root $item
    if (-not (Test-Path -LiteralPath $local)) { throw "Missing deployment item: $local" }
    Write-Host "Uploading $item ..."
    $scpArgs = @("-i", $connection.private_key_path, "-P", [string]$connection.port, "-o", "ConnectTimeout=$($connection.connect_timeout_seconds)", "-o", "StrictHostKeyChecking=$strictHostKey")
    if ((Get-Item -LiteralPath $local).PSIsContainer) { $scpArgs += "-r" }
    $scpArgs += @($local, "$remote`:$work/")
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) { throw "Upload failed: $item" }
}

& ssh @sshArgs "cd '$work' && $($machine.python_command) -m venv '$($machine.venv_directory)' && '$($machine.venv_directory)/bin/pip' install -r requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Remote dependency installation failed" }

if ($UploadRuntimeConfig) {
    if ([string]::IsNullOrWhiteSpace($RuntimeConfigPath)) { throw "-RuntimeConfigPath is required with -UploadRuntimeConfig" }
    if (-not (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf)) { throw "Runtime config does not exist: $RuntimeConfigPath" }
    Write-Warning "Uploading runtime config containing credentials to the remote host"
    $scpArgs = @("-i", $connection.private_key_path, "-P", [string]$connection.port, "-o", "ConnectTimeout=$($connection.connect_timeout_seconds)", "-o", "StrictHostKeyChecking=$strictHostKey", $RuntimeConfigPath, "$remote`:$work/remote_runtime_config.json")
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) { throw "Runtime config upload failed" }
}

Write-Host "Remote deployment complete: $remote`:$work"
if (-not $UploadRuntimeConfig) {
    Write-Host "Copy remote_runtime_config.example.json to remote_runtime_config.json on the host before starting."
}
