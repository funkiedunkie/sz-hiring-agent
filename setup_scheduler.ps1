# Registers the SZ Hiring Agent as a Windows scheduled task.
# Run once: powershell -ExecutionPolicy Bypass -File setup_scheduler.ps1

$taskName = "SZ-HiringAgent"
$batFile  = "C:\Users\Buste\sz-hiring-agent\run_agent.bat"
$logDir   = "C:\Users\Buste\sz-hiring-agent\logs"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batFile`""
$trigger  = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 10) -Once -At (Get-Date)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "Task registered: runs every 10 minutes." -ForegroundColor Green
Write-Host "Log file: $logDir\agent.log" -ForegroundColor Cyan
