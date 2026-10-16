# Compatibility entry point; plan-only unless -Execute is explicitly passed.
try {
    & (Join-Path $PSScriptRoot 'scripts/analysis/run_d2_expansion.ps1') @args
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
