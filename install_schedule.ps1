param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$jobs = @(
    @{
        Name = "ffbot-snapshot"
        Command = "snapshot"
        Day = [System.DayOfWeek]::Thursday
        At = [datetime]::Today.AddHours(17)
    },
    @{
        Name = "ffbot-weekly"
        Command = "weekly"
        Day = [System.DayOfWeek]::Sunday
        At = [datetime]::Today.AddHours(11).AddMinutes(30)
    },
    @{
        Name = "ffbot-settle"
        Command = "settle"
        Day = [System.DayOfWeek]::Tuesday
        At = [datetime]::Today.AddHours(6)
    }
)

if ($Uninstall) {
    foreach ($job in $jobs) {
        $task = Get-ScheduledTask -TaskName $job.Name -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Unregister-ScheduledTask -TaskName $job.Name -Confirm:$false
            Write-Output "Removed task $($job.Name)."
        }
        else {
            Write-Output "Task $($job.Name) is not registered."
        }
    }
    exit 0
}

$pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction Stop
$pythonPath = $pythonCommand.Source
$workingDirectory = $PSScriptRoot
$runnerPath = Join-Path $workingDirectory "run_job.py"
$configPath = Join-Path $workingDirectory "config.yaml"
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

foreach ($job in $jobs) {
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek $job.Day `
        -At $job.At
    $arguments = '"{0}" {1} --config "{2}"' -f $runnerPath, $job.Command, $configPath
    $action = New-ScheduledTaskAction `
        -Execute $pythonPath `
        -Argument $arguments `
        -WorkingDirectory $workingDirectory
    Register-ScheduledTask `
        -TaskName $job.Name `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Run the ffbot $($job.Command) job." `
        -Force | Out-Null
    Write-Output "Registered $($job.Name) for $($job.Day) at $($job.At.ToString('HH:mm'))."
}
