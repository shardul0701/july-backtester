# setup_task_scheduler.ps1
# Run ONCE as Administrator to register both scheduled tasks.
#
# Usage (from PowerShell as Admin):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\scripts\setup_task_scheduler.ps1

$ROOT       = Split-Path -Parent $PSScriptRoot
$MORNING_PS = Join-Path $ROOT "scripts\scheduled_morning_report.ps1"
$NIGHTLY_PS = Join-Path $ROOT "scripts\scheduled_nightly_pipeline.ps1"

# ET = UTC-4 (summer) / UTC-5 (winter)
# Task Scheduler uses LOCAL time — set these to your local equivalent of ET.
# If you are on ET: 09:45 and 17:00.
# If you are on IST (UTC+5:30): 19:15 and 02:30 (+1 day for nightly).
$MORNING_TIME = "09:45"   # adjust to your timezone
$NIGHTLY_TIME = "17:00"   # adjust to your timezone

$PYTHON = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PYTHON) {
    Write-Error "Python not found in PATH. Make sure Python is installed and accessible."
    exit 1
}

# ── Task 1: Morning positions report ─────────────────────────────────────────
$taskName1 = "JulyBacktester_MorningReport"
if (Get-ScheduledTask -TaskName $taskName1 -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName1 -Confirm:$false
    Write-Host "Removed existing task: $taskName1"
}

$action1  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$MORNING_PS`""
$trigger1 = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $MORNING_TIME
$settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit "00:05:00" `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $taskName1 `
    -Action $action1 `
    -Trigger $trigger1 `
    -Settings $settings1 `
    -Description "July-backtester morning positions report" `
    -RunLevel Highest | Out-Null

Write-Host "Registered: $taskName1 (Mon-Fri at $MORNING_TIME)"

# ── Task 2: Nightly pipeline ──────────────────────────────────────────────────
$taskName2 = "JulyBacktester_NightlyPipeline"
if (Get-ScheduledTask -TaskName $taskName2 -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName2 -Confirm:$false
    Write-Host "Removed existing task: $taskName2"
}

$action2  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$NIGHTLY_PS`""
$trigger2 = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $NIGHTLY_TIME
$settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit "00:30:00" `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $taskName2 `
    -Action $action2 `
    -Trigger $trigger2 `
    -Settings $settings2 `
    -Description "July-backtester nightly pipeline: fetch prices, update positions, submit orders" `
    -RunLevel Highest | Out-Null

Write-Host "Registered: $taskName2 (Mon-Fri at $NIGHTLY_TIME)"

Write-Host ""
Write-Host "Both tasks registered. Verify in Task Scheduler (taskschd.msc)."
Write-Host "Logs will be written to: $ROOT\output\logs\"
