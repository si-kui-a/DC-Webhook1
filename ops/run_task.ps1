[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Task,
  [Parameter(Mandatory=$true)][string]$Script,
  [string[]]$Arguments = @(),
  [int]$TimeoutSeconds = 900
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runId = '{0:yyyyMMdd-HHmmss}-{1}' -f (Get-Date), ([guid]::NewGuid().ToString('N').Substring(0,8))
$logDir = Join-Path $repo 'logs\runs'
$null = New-Item -ItemType Directory -Force -Path $logDir
$log = Join-Path $logDir "$Task-$runId.log"
$stdout = "$log.out"
$stderr = "$log.err"
$lock = Join-Path $repo "logs\$Task.lock"
$start = Get-Date
if (Test-Path $lock) {
  $age = ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalSeconds
  if ($age -lt $TimeoutSeconds) { throw "overlap blocked: $Task ($([int]$age)s)" }
  Remove-Item -LiteralPath $lock -Force
}
Set-Content -LiteralPath $lock -Value $runId -Encoding utf8
try {
  $sha = (git -C $repo rev-parse HEAD 2>$null).Trim()
  $python = Join-Path $repo 'venv\Scripts\python.exe'
  if (-not (Test-Path $python)) { $python = 'python' }
  "run_id=$runId task=$Task commit=$sha start=$start" | Set-Content -LiteralPath $log -Encoding utf8
  $p = Start-Process -FilePath $python -ArgumentList (@($Script) + $Arguments) -WorkingDirectory $repo -NoNewWindow -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  # Touch Handle right away: without a cached handle, ExitCode reads back empty
  # after exit and [int] turns it into 0, so every failure was logged as success.
  $null = $p.Handle
  if (-not $p.WaitForExit($TimeoutSeconds * 1000)) { $p.Kill(); throw "timeout after ${TimeoutSeconds}s" }
  $p.Refresh()
  $code = [int]$p.ExitCode
  $end = Get-Date
  "stdout=$stdout`nstderr=$stderr`nend=$end exit_code=$code duration_sec=$([int]($end-$start).TotalSeconds)" | Add-Content -LiteralPath $log
  if ($code -ne 0) { exit $code }
} finally {
  Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
}
