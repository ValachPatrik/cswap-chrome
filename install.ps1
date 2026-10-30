# Install cswap-chrome for the current Windows user. Run from the repo clone; the clone stays the install.
$ErrorActionPreference = 'Stop'

$Repo = $PSScriptRoot
$Task = 'cswap-chrome-watch'
$Python = (Get-Command python.exe).Source
$PythonW = Join-Path (Split-Path $Python) 'pythonw.exe'
$Bin = Join-Path $HOME '.local\bin'
$ConfigDir = Join-Path $HOME '.config\cswap-chrome'

New-Item -ItemType Directory -Force $Bin, $ConfigDir, (Join-Path $HOME '.local\state\cswap-chrome') | Out-Null

# Claude Code runs hook commands in Git Bash, so the command in ~/.local/bin is a sh wrapper.
$Wrapper = "#!/bin/sh`nexec `"$($Python -replace '\\', '/')`" -I `"$($Repo -replace '\\', '/')/cswap_chrome.py`" `"`$@`"`n"
[IO.File]::WriteAllText((Join-Path $Bin 'cswap-chrome'), $Wrapper)

$Config = Join-Path $ConfigDir 'config.json'
if (-not (Test-Path $Config)) {
    Copy-Item (Join-Path $Repo 'config.example.json') $Config
    Write-Output "Edit ${Config}: set the Chrome profile folder for each account."
}

$Action = New-ScheduledTaskAction -Execute $PythonW -Argument "-I `"$Repo\cswap_chrome.py`" watch"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $Task -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $Task
Get-ScheduledTask -TaskName $Task | Select-Object TaskName, State
