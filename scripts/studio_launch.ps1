# Mod Studio in one click: starts studio/server.py in the background when it is not running, then opens the page.
# The "Mod Studio" shortcuts (desktop, Start menu) run this; scripts\studio_shortcut.ps1 makes them.
#   powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File scripts\studio_launch.ps1 [-Stop]
param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$port = 8765
$stateRoot = if ($env:AM_ROOT) { $env:AM_ROOT } else { 'C:\AccretionMods' }
$registry = Join-Path $stateRoot 'studio\instance.json'
if (Test-Path -LiteralPath $registry) {
    try {
        $instance = Get-Content -LiteralPath $registry -Raw | ConvertFrom-Json
        $info = Invoke-RestMethod "http://127.0.0.1:$($instance.port)/api/studio" -TimeoutSec 2
        if ($info.protocol -eq 1 -and $info.root -eq $repo) { $port = [int]$instance.port }
    } catch { }
}
$url = "http://localhost:$port/"
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($Stop) {
    $info = Invoke-RestMethod "http://127.0.0.1:$port/api/studio" -TimeoutSec 2
    if ($info.protocol -ne 1 -or $info.root -ne $repo) { throw 'This listener is not the registered Studio instance.' }
    Invoke-RestMethod "http://127.0.0.1:$port/api/studio/stop" -Method Post -ContentType 'application/json' -Body '{}' | Out-Null
    exit 0
}
if ($listening) {
    try { $info = Invoke-RestMethod "http://127.0.0.1:$port/api/studio" -TimeoutSec 2 }
    catch { throw 'An older Studio or another application occupies this port. Close that window, then launch Studio again.' }
    if ($info.protocol -ne 1 -or $info.root -ne $repo) { throw 'This port belongs to another application.' }
}
if (-not $listening) {
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
    if (-not (Test-Path $uv)) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show("uv is not installed (https://docs.astral.sh/uv/), so Mod Studio can't start.", "Mod Studio") | Out-Null
        exit 1
    }
    $env:PYTHONUTF8 = '1'
    Start-Process -FilePath $uv -ArgumentList 'run', '--no-project', '--python', '3.12', 'studio/server.py', '--port', "$port", '--no-open' `
        -WorkingDirectory $repo -WindowStyle Hidden
    for ($i = 0; $i -lt 40; $i++) {  # up to 20 s for the first start (uv may fetch Python once)
        Start-Sleep -Milliseconds 500
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { break }
    }
}
Start-Process $url
