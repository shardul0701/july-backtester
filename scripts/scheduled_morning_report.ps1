# scheduled_morning_report.ps1
# Runs daily_positions_report.py at 9:45 AM ET (Mon-Fri)
# Saves output to a timestamped log file.

$ROOT   = Split-Path -Parent $PSScriptRoot
$LOG    = Join-Path $ROOT "output\logs\morning_report_$(Get-Date -Format 'yyyy-MM-dd').txt"
$PYTHON = (Get-Command python).Source

New-Item -ItemType Directory -Force -Path (Split-Path $LOG) | Out-Null

"=== Morning Positions Report: $(Get-Date) ===" | Out-File -FilePath $LOG -Encoding utf8
& $PYTHON (Join-Path $ROOT "scripts\daily_positions_report.py") 2>&1 | Tee-Object -FilePath $LOG -Append
