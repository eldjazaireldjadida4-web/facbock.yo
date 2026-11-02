$ErrorActionPreference = 'Stop'
$taskName = 'FB-YouTube-Uploader'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -InputObject $task -Confirm:$false
    Write-Output 'The FB-YouTube-Uploader logon task was removed.'
} else {
    Write-Output 'The FB-YouTube-Uploader logon task was not installed.'
}
