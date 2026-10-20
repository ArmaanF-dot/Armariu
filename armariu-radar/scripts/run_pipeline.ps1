<#
  One scheduled pass of the data pipeline (no AI). Commits the queue and digests if this folder
  is a git repo, and pushes them if it has a remote (optional backup).

    powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1
#>
param([int]$Minutes = 10, [switch]$NoPush)

# "Continue", not "Stop": in Windows PowerShell 5.1, git and claude writing to stderr would
# otherwise abort the script. Exit codes are checked explicitly instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$Lock = ".agent-pipeline.lock"
if ((Test-Path $Lock) -and ((Get-Item $Lock).LastWriteTime -gt (Get-Date).AddHours(-1))) { exit 0 }
Set-Content -Path $Lock -Value $PID
$Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

git rev-parse --is-inside-work-tree 2>&1 | Out-Null
$InRepo = ($LASTEXITCODE -eq 0)
$HasRemote = $InRepo -and [bool](git remote 2>$null)

try {
    if ($HasRemote) { git pull --rebase --autostash --quiet 2>&1 | Out-Null }
    # Pipe through Add-Content (UTF-8): *>> in Windows PowerShell 5.1 writes UTF-16.
    & $Python -m radar.pipeline run --minutes $Minutes 2>&1 |
        ForEach-Object { Add-Content -Path "logs\pipeline-console.log" -Value "$_" -Encoding UTF8 }
    if ($LASTEXITCODE -ne 0) { throw "pipeline failed ($LASTEXITCODE)" }

    if ($InRepo) {
        git add -- classify/queue.jsonl output/weekly
        git diff --cached --quiet
        if ($LASTEXITCODE -ne 0) {
            git commit --quiet -m "pipeline: update queue and digests"
            if ($LASTEXITCODE -ne 0) { throw "git commit failed: set git user.name and user.email (see run_agent.ps1)" }
            if ($HasRemote -and -not $NoPush) {
                git push --quiet 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { git pull --rebase --quiet; git push --quiet }
            }
        }
    }
}
catch {
    Add-Content "logs\pipeline-console.log" "$(Get-Date -Format s) ERROR: $_" -Encoding UTF8
    Write-Host "ERROR: $_"
    exit 1
}
finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $Lock
}
