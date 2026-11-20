$bash = "C:\Program Files\Git\bin\bash.exe"
$script = Join-Path $PSScriptRoot "pull_backup.sh"
$action = New-ScheduledTaskAction -Execute $bash -Argument "-lc `"'$($script -replace '\','/')'`""
$trigger = New-ScheduledTaskTrigger -Daily -At 4:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "ofmhelpers-pull-backup" -Action $action -Trigger $trigger -Settings $settings -Force
