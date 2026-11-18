$ErrorActionPreference = 'Stop'
$taskName = 'FB-YouTube-Uploader'
$projectDirectory = $PSScriptRoot
$runner = Join-Path $projectDirectory 'run_automation.bat'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "run_automation.bat was not found in $projectDirectory"
}
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '/d /c ""{0}""' -f $runner
$action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument $arguments -WorkingDirectory $projectDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Run FB-YouTube-Uploader automatically after this Windows user signs in.' -Force | Out-Null
Write-Output 'Automation will start when this Windows account signs in. Run disable_auto_start.bat to remove the scheduled task.'
