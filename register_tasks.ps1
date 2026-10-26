# Registers the Windows scheduled tasks for Deye BatteryLife (run once; re-running replaces the tasks).
#   "Deye BatteryLife"        daily 18:00            full run
#   "Deye BatteryLife Retry"  daily 18:45 and 19:30  --retry (only stations not handled successfully today)
# The tasks run as the current user while logged on (secrets.json is readable by this user only).
# Remove with: Unregister-ScheduledTask -TaskName "Deye BatteryLife*" -Confirm:$false
$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
$env:PYTHONUTF8 = "1"

function New-Job($name, $argsLine, $times) {
    $action   = New-ScheduledTaskAction -Execute $python -Argument "batterylife.py $argsLine" -WorkingDirectory $root
    # StartBoundary without UTC offset = local time, so the job keeps its wall-clock time across daylight saving changes
    $day      = (Get-Date).ToString('yyyy-MM-dd')
    $triggers = $times | ForEach-Object { $t = New-ScheduledTaskTrigger -Daily -At $_; $t.StartBoundary = "${day}T${_}:00"; $t }
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
                  -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings `
        -Description "Deye BatteryLife (dynamic minimum SoC)" -Force | Out-Null
    Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
}

New-Job "Deye BatteryLife"       ""        @("18:00")
New-Job "Deye BatteryLife Retry" "--retry" @("18:45", "19:30")
