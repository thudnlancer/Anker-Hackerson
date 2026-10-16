# Compatibility entry point. Read-only preflight is the default; pass -Restart explicitly.
try {
    & (Join-Path $PSScriptRoot 'scripts/maintenance/restart_w3_collector.ps1') @args
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
