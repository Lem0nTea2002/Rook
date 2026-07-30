[CmdletBinding()]
param(
    [switch]$PrepareOnly,
    [string]$OutputRoot
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repoRoot ".rook\interview-demo"
}
$runId = "{0}-{1}" -f (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N").Substring(0, 6))
$runRoot = Join-Path $OutputRoot "run-$runId"
$workspace = Join-Path $runRoot "coding-task"

New-Item -ItemType Directory -Path (Join-Path $workspace "src") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $workspace "tests") -Force | Out-Null

@'
def final_price(subtotal: float, discount_percent: float) -> float:
    """Return the price after a percentage discount."""
    return subtotal - discount_percent
'@ | Set-Content -LiteralPath (Join-Path $workspace "src\pricing.py") -Encoding utf8

@'
from pricing import final_price


def test_twenty_percent_discount() -> None:
    assert final_price(100, 20) == 80


def test_fractional_subtotal() -> None:
    assert final_price(79.9, 25) == 59.925
'@ | Set-Content -LiteralPath (Join-Path $workspace "tests\test_pricing.py") -Encoding utf8

@'
[tool.pytest.ini_options]
pythonpath = ["src"]
'@ | Set-Content -LiteralPath (Join-Path $workspace "pyproject.toml") -Encoding utf8

@'
# Rook interview coding task

`final_price` subtracts the percentage as a fixed amount. Fix the implementation
without changing the tests, then run the focused test suite.
'@ | Set-Content -LiteralPath (Join-Path $workspace "README.md") -Encoding utf8

git -C $workspace init --quiet
git -C $workspace config user.name "Rook Demo"
git -C $workspace config user.email "rook-demo@local.invalid"
git -C $workspace add README.md pyproject.toml src tests
git -C $workspace commit --quiet -m "seed interview coding task"

$prompt = "读取 README.md，修复折扣计算，只修改 src/pricing.py，并运行 python -m pytest -q 验证。"
Write-Host ""
Write-Host "Rook 3-minute demo prepared" -ForegroundColor Cyan
Write-Host "Workspace: $workspace"
Write-Host "Prompt: $prompt" -ForegroundColor Green
Write-Host ""

if ($PrepareOnly) {
    Write-Output $runRoot
    exit 0
}

$localRook = if ($IsWindows) {
    Join-Path $repoRoot ".venv\Scripts\rook.exe"
} else {
    Join-Path $repoRoot ".venv/bin/rook"
}
if (Test-Path -LiteralPath $localRook) {
    $rookCommand = $localRook
} else {
    $rookCommand = (Get-Command rook -ErrorAction Stop).Source
}

Write-Host "Step 1/2: run the live Coding Agent." -ForegroundColor Cyan
Write-Host "Paste the prompt above. Tool output and the permission picker stay visible."
Write-Host "The live coding step uses your configured Provider and may incur a small model cost." -ForegroundColor Yellow
Push-Location $workspace
try {
    & $rookCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Rook TUI exited with code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Coding task Git state:" -ForegroundColor Cyan
git -C $workspace status --short
git -C $workspace diff -- src/pricing.py

Write-Host ""
Write-Host "Step 2/2: run the deterministic Forge lifecycle." -ForegroundColor Cyan
$forgeId = $runId.Substring($runId.Length - 6)
$forgeRoot = Join-Path $repoRoot ".rook\forge-$forgeId"
& $rookCommand eval demo --output $forgeRoot
if ($LASTEXITCODE -ne 0) {
    throw "Rook Forge demo exited with code $LASTEXITCODE"
}

$summary = Get-ChildItem -LiteralPath $forgeRoot -Filter demo-summary.md -Recurse -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if ($null -eq $summary) {
    throw "Rook Forge demo did not produce demo-summary.md"
}

Write-Host ""
Write-Host "Forge evidence:" -ForegroundColor Green
Get-Content -LiteralPath $summary.FullName
Write-Host ""
Write-Host "Demo artifacts: $runRoot" -ForegroundColor Cyan
