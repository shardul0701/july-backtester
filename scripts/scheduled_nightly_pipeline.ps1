# scheduled_nightly_pipeline.ps1
# Runs daily_pipeline.py at 5:00 PM ET (Mon-Fri)
# Fetches prices, updates positions, submits orders to all 3 Alpaca accounts.
# Saves output to a timestamped log file.

$ROOT   = Split-Path -Parent $PSScriptRoot
$LOG    = Join-Path $ROOT "output\logs\nightly_pipeline_$(Get-Date -Format 'yyyy-MM-dd').txt"
$PYTHON = (Get-Command python).Source

New-Item -ItemType Directory -Force -Path (Split-Path $LOG) | Out-Null

"=== Nightly Pipeline: $(Get-Date) ===" | Out-File -FilePath $LOG -Encoding utf8
& $PYTHON (Join-Path $ROOT "scripts\daily_pipeline.py") 2>&1 | Tee-Object -FilePath $LOG -Append

# After pipeline, run positions report to log current state
"`n=== Post-Submit Positions Report ===" | Out-File -FilePath $LOG -Append -Encoding utf8
& $PYTHON (Join-Path $ROOT "scripts\daily_positions_report.py") 2>&1 | Tee-Object -FilePath $LOG -Append
