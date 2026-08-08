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
  if (-not $p.WaitForExit($TimeoutSeconds * 1000)) { $p.Kill(); throw "timeout after ${TimeoutSeconds}s" }
  $p.Refresh()
  $code = [int]$p.ExitCode
  $end = Get-Date
  "stdout=$stdout`nstderr=$stderr`nend=$end exit_code=$code duration_sec=$([int]($end-$start).TotalSeconds)" | Add-Content -LiteralPath $log
  $status = if ($code -eq 0) { "SUCCESS_CORE" } else { "FAILED_CORE" }
  $result = [ordered]@{
    task = $Task
    run_id = $runId
    commit = $sha
    status = $status
    started_at = $start.ToUniversalTime().ToString('o')
    finished_at = $end.ToUniversalTime().ToString('o')
    duration_ms = [int](($end - $start).TotalMilliseconds)
    message = if ($code -eq 0) { "process completed" } else { "process failed" }
    error_code = if ($code -eq 0) { $null } else { [string]$code }
  }
  $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $logDir "$Task-$runId.result.json") -Encoding utf8
  if ($code -ne 0) { exit $code }
} finally {
  Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
}
