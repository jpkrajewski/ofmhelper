param([string]$BackupDir = "~/ofmhelpers-backups")
$bash = "C:\Program Files\Git\bin\bash.exe"
$script = (Join-Path $PSScriptRoot "pull_backup.sh") -replace '\\', '/'
$run = "mkdir -p $BackupDir && { date -u; OFM_BACKUP_DIR=$BackupDir '$script'; echo exit=`$?; } >> $BackupDir/pull.log 2>&1"
$action = New-ScheduledTaskAction -Execute $bash -Argument "-lc `"$run`""
$trigger = New-ScheduledTaskTrigger -Daily -At 4:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "ofmhelpers-pull-backup" -Action $action -Trigger $trigger -Settings $settings -Force
