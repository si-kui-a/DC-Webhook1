[CmdletBinding()]
param([int]$MaxAgeMinutes = 60)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$result = [ordered]@{ checked_at=(Get-Date).ToUniversalTime().ToString('o'); commit=(git -C $repo rev-parse HEAD).Trim(); checks=@{} }
$result.checks.git_clean = [string]::IsNullOrWhiteSpace((git -C $repo status --porcelain))
$result.checks.python = Test-Path (Join-Path $repo 'venv\Scripts\python.exe')
$runDir = Join-Path $repo 'logs\runs'
$result.checks.run_log_dir = Test-Path $runDir
$latest = if (Test-Path $runDir) { Get-ChildItem $runDir -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1 }
$result.checks.latest_log = if ($latest) { $latest.Name } else { $null }
$result.checks.latest_log_fresh = if ($latest) { ((Get-Date)-$latest.LastWriteTime).TotalMinutes -le $MaxAgeMinutes } else { $false }
$result.ok = ($result.checks.git_clean -and $result.checks.python -and $result.checks.run_log_dir)
$result | ConvertTo-Json -Depth 5
if (-not $result.ok) { exit 1 }
