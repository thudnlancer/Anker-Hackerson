param(
    [int]$StartSite = 1,
    [int]$SiteCount = 10,
    [int]$Workers = 8,
    [switch]$Resume,
    [string]$WeatherMode = "persistence",
    [string]$OutputRoot = "outputs/milp_chunks"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($StartSite -lt 1 -or $SiteCount -lt 1) { throw "StartSite and SiteCount must be positive" }
$end = $StartSite + $SiteCount - 1
$offset = $StartSite - 1
$output = Join-Path $OutputRoot ("sites_{0:D3}_{1:D3}_{2}" -f $StartSite, $end, $WeatherMode)
$log = Join-Path $output "console.log"
if (Test-Path (Join-Path $output "strategy_comparison_summary.json")) { throw "Completed output exists; choose a new OutputRoot" }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$extra = @()
if ($Resume) { $extra += "--resume" }

Write-Host "Starting chunk $StartSite-$end with $Workers processes; output: $output"
python run_logged_experiment.py `
  --data forecast_data `
  --output $output `
  --site-start $offset `
  --max-sites $SiteCount `
  --train-all-sites `
  --weather-modes $WeatherMode `
  --soc-min 10 `
  --degradation-cost 0.02 `
  --terminal-price 0.1 `
  --solver milp --workers $Workers --worker-threads 1 @extra
if ($LASTEXITCODE -ne 0) { throw "MILP chunk failed with exit code $LASTEXITCODE" }
Write-Host "Chunk completed. Verify strategy_comparison_summary.json and console.log before treating it as valid."
