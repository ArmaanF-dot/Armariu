<#
  Registers Windows Task Scheduler jobs for the roles this machine runs. Runs as you, only while
  you're logged in (that's where your Claude Code login lives).

  Everything on this PC (the default):
    powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
  Only some roles:
    powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1 -Roles pipeline,classifier

  Remove them again:
    powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1 -Uninstall
#>
param(
    [ValidateSet("pipeline", "classifier", "reviewer", "newsletter")]
    [string[]]$Roles = @("pipeline", "classifier", "reviewer", "newsletter"),
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Prefix = "Armariu Radar"

if ($Uninstall) {
    Get-ScheduledTask | Where-Object { $_.TaskName -like "$Prefix*" } |
        Unregister-ScheduledTask -Confirm:$false
    Write-Host "Removed all '$Prefix' tasks."
    exit 0
}
if (-not $Roles) { throw "Pass -Roles, e.g. -Roles pipeline,classifier" }

# How often each role wakes up. Agents exit at once when they have no work, so a wake-up with
# nothing to do costs no Claude usage. The classifier is the only frequent Claude user: with one
# Pro plan for everything, raise its interval (e.g. 180) if you hit usage limits.
$Every = @{ pipeline = 30; classifier = 120; reviewer = 240; newsletter = 240 }

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

foreach ($role in $Roles) {
    if ($role -eq "pipeline") {
        $script = Join-Path $Root "scripts\run_pipeline.ps1"; $extra = ""
    } else {
        $script = Join-Path $Root "scripts\run_agent.ps1"; $extra = " -Agent $role"
    }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"$extra"
    $start = (Get-Date).AddMinutes(2)
    $trigger = New-ScheduledTaskTrigger -Once -At $start `
        -RepetitionInterval (New-TimeSpan -Minutes $Every[$role])
    Register-ScheduledTask -TaskName "$Prefix - $role" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Armariu deal radar: $role" -Force | Out-Null
    Write-Host "Registered '$Prefix - $role' every $($Every[$role]) minutes, first run at $start"
}
