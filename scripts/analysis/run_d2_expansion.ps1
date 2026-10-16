param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateRange(1,20)][int]$Batches = 20,
    [ValidateRange(1,24)][int]$Iterations = 24,
    [ValidateRange(1,30)][int]$IntervalSeconds = 20,
    [switch]$Execute
)

# D2 paired API/HA read-only sampling. No HA service calls. By default only
# prints the plan; -Execute is required to create data or contact APIs.
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$outputsRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'outputs'))
$target = if ([IO.Path]::IsPathRooted($OutputDir)) {
    [IO.Path]::GetFullPath($OutputDir)
} else {
    [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDir))
}
$prefix = $outputsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDir must be a new child directory under workspace outputs'
}
if (Test-Path -LiteralPath $target) { throw 'OutputDir already exists; refusing overwrite' }
$runner = Join-Path $repoRoot 'run_irradiance_shadow.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw 'read-only pilot runner missing' }
$plan = [ordered]@{mode='plan_only'; runner=$runner; output=$target; batches=$Batches;
                   iterations_per_batch=$Iterations; interval_seconds=$IntervalSeconds;
                   estimated_wall_minutes=[math]::Round($Batches * $Iterations * $IntervalSeconds / 60, 1);
                   ha_writes_allowed=$false}
if (-not $Execute) { $plan | ConvertTo-Json -Compress; return }

$plan.mode = 'execute_read_only'
New-Item -ItemType Directory -Path $target | Out-Null
$plan | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $target 'plan.json') -Encoding UTF8
$chainLog = Join-Path $target 'chain_log.jsonl'
$consecutivePartial = 0
for ($k = 1; $k -le $Batches; $k++) {
    $name = 'batch_{0:d2}' -f $k
    $batch = Join-Path $target $name
    $argv = @($runner, '--output', $batch, '--iterations', [string]$Iterations,
              '--interval-seconds', [string]$IntervalSeconds)
    if ($k -gt 1) {
        $previous = Join-Path (Join-Path $target ('batch_{0:d2}' -f ($k - 1))) 'result.json'
        if (-not (Test-Path -LiteralPath $previous -PathType Leaf)) { throw 'previous batch result missing; refusing continuation' }
        $argv += @('--history-result', $previous)
    }
    $started = [DateTimeOffset]::UtcNow.ToString('o')
    & python @argv 2>&1 | Out-File -LiteralPath (Join-Path $target "$name.driver.log") -Encoding UTF8
    $code = $LASTEXITCODE
    $resultPath = Join-Path $batch 'result.json'
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw "batch $name failed or result missing" }
    $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($result.mode -ne 'read_only_pilot' -or $result.writes_performed -ne 0) {
        throw "batch $name violated read-only contract"
    }
    $entry = [ordered]@{batch=$name; started_utc=$started; exit_code=$code;
                        iterations=@($result.cycles).Count; pairs=@($result.pairs).Count;
                        result_sha256=(Get-FileHash -LiteralPath $resultPath -Algorithm SHA256).Hash;
                        writes_performed=0}
    ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $chainLog -Encoding UTF8
    if ($entry.iterations -lt 12) { $consecutivePartial++ } else { $consecutivePartial = 0 }
    if ($consecutivePartial -ge 3) { break }
}
