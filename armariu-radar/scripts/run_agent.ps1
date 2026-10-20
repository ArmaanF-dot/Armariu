<#
  Runs one Armariu agent headlessly with Claude Code, on this machine's Claude login.
  Exits immediately (no Claude usage) when the agent has nothing to do.

    powershell -ExecutionPolicy Bypass -File scripts\run_agent.ps1 -Agent classifier
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("classifier", "reviewer", "newsletter")]
    [string]$Agent,
    [int]$MaxTurns = 40,
    [switch]$NoPush
)

# "Continue", not "Stop": in Windows PowerShell 5.1, git and claude writing to stderr would
# otherwise abort the script. Exit codes are checked explicitly instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$Log = Join-Path "logs" "$Agent-$Stamp.log"

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format s) $msg"
    Add-Content -Path $Log -Value $line
    Write-Host $line
}

$Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

# Only one copy of each agent at a time. A lock older than 2 hours is treated as stale.
$Lock = ".agent-$Agent.lock"
if ((Test-Path $Lock) -and ((Get-Item $Lock).LastWriteTime -gt (Get-Date).AddHours(-2))) {
    exit 0
}
Set-Content -Path $Lock -Value $PID

# What each agent may write (Edit rules cover the Write tool too) and which web tools it gets.
$Allowed = @{
    "classifier" = @("Read", "Glob", "Grep", "Edit(./classify/results/**)", "Edit(./inbox/**)", "WebSearch")
    "reviewer"   = @("Read", "Glob", "Grep", "Edit(./reviews/**)", "Edit(./inbox/**)", "WebSearch",
                     "WebFetch(domain:find-and-update.company-information.service.gov.uk)")
    "newsletter" = @("Read", "Glob", "Grep", "Edit(./drafts/**)", "Edit(./inbox/**)")
}
$OwnedPaths = @{
    "classifier" = @("classify/results", "inbox")
    "reviewer"   = @("reviews", "inbox")
    "newsletter" = @("drafts", "inbox")
}
$Denied = @("Read(./.env)", "Read(./data/radar.db)", "Bash", "PowerShell")

# Git is optional: commits give you history, and a remote (e.g. a private GitHub repo) gives a backup.
git rev-parse --is-inside-work-tree 2>&1 | Out-Null
$InRepo = ($LASTEXITCODE -eq 0)
$HasRemote = $InRepo -and [bool](git remote 2>$null)

try {
    if ($HasRemote) { git pull --rebase --autostash --quiet 2>&1 | Out-Null }

    & $Python -m radar.agentcheck prepare $Agent
    if ($LASTEXITCODE -eq 3) { exit 0 }          # nothing to do
    if ($LASTEXITCODE -ne 0) { throw "agentcheck failed ($LASTEXITCODE)" }

    Write-Log "Starting $Agent"
    $Prompt = "Your task for this run is in the file .agent-$Agent-task.md. Read it, then do it, following your role instructions."
    $claudeArgs = @(
        "-p", $Prompt,
        "--append-system-prompt-file", "agents/$Agent.md",
        "--permission-mode", "dontAsk",
        "--max-turns", "$MaxTurns",
        "--output-format", "text",
        "--allowedTools") + $Allowed[$Agent] + @("--disallowedTools") + $Denied

    & claude @claudeArgs *>> $Log
    if ($LASTEXITCODE -ne 0) { throw "claude exited with code $LASTEXITCODE (see $Log)" }

    & $Python -m radar.agentcheck finish $Agent *>> $Log

    if (-not $InRepo) { Write-Log "Finished $Agent (folder is not a git repo, nothing committed)"; exit 0 }

    # Commit only the folders this agent owns. Anything else changed is left alone and reported.
    $unexpected = git status --porcelain | ForEach-Object { $_.Substring(3) } | Where-Object {
        $p = $_ -replace '\\', '/'
        -not ($OwnedPaths[$Agent] | Where-Object { $p.StartsWith($_) })
    } | Where-Object { $_ -notmatch '^(logs/|\.agent-|classify/pending\.jsonl)' }
    if ($unexpected) { Write-Log "Note: other uncommitted changes left untouched: $($unexpected -join ', ')" }

    git add -- $OwnedPaths[$Agent]
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit --quiet -m "agent($Agent): run $Stamp"
        if ($HasRemote -and -not $NoPush) {
            git push --quiet 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                git pull --rebase --quiet
                git push --quiet
            }
        }
        Write-Log "Committed $Agent output"
    } else {
        Write-Log "No output to commit"
    }
}
catch {
    Write-Log "ERROR: $_"
    exit 1
}
finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $Lock
}
