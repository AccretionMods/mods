# Registers a Windows scheduled task that runs scripts\intake_auto.py --apply every 15 minutes while
# you are signed in. Run it once, yourself:  powershell -ExecutionPolicy Bypass -File scripts\register_intake_auto.ps1
# Remove it with:  Unregister-ScheduledTask -TaskName "Accretion inbox auto-pipeline" -Confirm:$false
# The task needs git and gh signed in (push to main, start Mods: build a mod) and the repo's .upload-key.
param([int]$EveryMinutes = 15)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv -ErrorAction Stop).Source
$name = "Accretion inbox auto-pipeline"

$cmd = "`$env:PYTHONUTF8='1'; & '$uv' run --no-project --python 3.12 python scripts\intake_auto.py --apply"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Builds a mod when a new base .ipa lands in C:\AccretionMods\intake" -Force | Out-Null
Write-Host "Registered '$name' (every $EveryMinutes min). Log: C:\AccretionMods\auto.log"
