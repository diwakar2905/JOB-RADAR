# PowerShell script to register (or remove with -Uninstall) Job Radar in
# Windows Task Scheduler.
# Install:   powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1
# Uninstall: powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1 -Uninstall

param(
    [switch]$Uninstall
)

$TaskName = "JobRadar_Pipeline"
$ScriptDir = Split-Path -Parent $PSScriptRoot

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[SUCCESS] Job Radar scheduled task '$TaskName' removed." -ForegroundColor Green
    exit 0
}

$PythonPath = (Get-Command python).Source

Write-Host "Configuring Job Radar Scheduler..." -ForegroundColor Cyan
Write-Host "Python Path: $PythonPath"
Write-Host "Working directory: $ScriptDir"

# Action to execute: python -m radar run
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "-m radar run" -WorkingDirectory $ScriptDir

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
