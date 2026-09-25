# PowerShell script to register Job Radar in Windows Task Scheduler
# Run this once in PowerShell: powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1

$TaskName = "JobRadar_Pipeline"
$PythonPath = (Get-Command python).Source
$ScriptDir = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $ScriptDir "run.py"

Write-Host "Configuring Job Radar Scheduler..." -ForegroundColor Cyan
Write-Host "Python Path: $PythonPath"
Write-Host "Target Script: $RunScript"

# Action to execute
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "`"$RunScript`"" -WorkingDirectory $ScriptDir

# Trigger: Every 6 hours starting today
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 6)

# Settings: Tolerate laptop sleep, wake/run on battery or AC, don't stop if running
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Unregister old task if exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Register task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Job Radar background job discovery and fit scoring agent"

Write-Host "`n[SUCCESS] Job Radar scheduled task '$TaskName' registered successfully!" -ForegroundColor Green
Write-Host "It will run every 6 hours in the background whenever your laptop is on."
