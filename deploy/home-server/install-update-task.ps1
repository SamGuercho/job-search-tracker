# Registers a Windows scheduled task that runs update.ps1 at log-on and every 15 minutes,
# so the server installs each new version published by CI by itself (continuous deployment).
# Run once, in PowerShell, from this folder:   powershell -ExecutionPolicy Bypass -File .\install-update-task.ps1
$script = Join-Path $PSScriptRoot "update.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$atLogon = New-ScheduledTaskTrigger -AtLogOn
$every15 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "JobSearchTracker-Update" -Action $action -Trigger $atLogon, $every15 `
    -Settings $settings -Description "Keeps the Job Search Tracker up to date" -Force | Out-Null
Write-Host "Scheduled task 'JobSearchTracker-Update' installed: checks for a new version every 15 minutes."
Write-Host "Remove it with: Unregister-ScheduledTask -TaskName JobSearchTracker-Update -Confirm:`$false"
