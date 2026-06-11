@echo off
REM Daily after-close manager for the 5 live paper strategies (T+1).
REM Registered as Windows Scheduled Task "LiveStrategyManager" at 5:30 PM ET, Mon-Fri.
REM Runs LIVE (submits orders that fill at next open). Per-strategy logs in paper_state\logs\.
cd /d "C:\Users\shard\Light Water Internship\july-backtester"
"C:\Users\shard\AppData\Local\Programs\Python\Python312\python.exe" scripts\manage_live_portfolio.py >> "paper_state\logs\task.log" 2>&1
