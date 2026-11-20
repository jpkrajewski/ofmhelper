$bash = "C:\Program Files\Git\bin\bash.exe"
$script = (Join-Path $PSScriptRoot "pull_backup.sh") -replace '\\', '/'
$run = "mkdir -p ~/ofmhelpers-backups && { date -u; '$script'; echo exit=`$?; } >> ~/ofmhelpers-backups/pull.log 2>&1"
$action = New-ScheduledTaskAction -Execute $bash -Argument "-lc `"$run`""
$trigger = New-ScheduledTaskTrigger -Daily -At 4:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "ofmhelpers-pull-backup" -Action $action -Trigger $trigger -Settings $settings -Force
