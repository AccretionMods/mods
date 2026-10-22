# Makes "Mod Studio" shortcuts on the desktop and in the Start menu (scripts\studio_launch.ps1). Right-click one
# and choose Pin to taskbar or Pin to Start: Windows lets only the user pin.
#   powershell -ExecutionPolicy Bypass -File scripts\studio_shortcut.ps1
$ErrorActionPreference = 'Stop'
$launch = Join-Path $PSScriptRoot 'studio_launch.ps1'
$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Accretion')
)
foreach ($place in $places) {
    New-Item -ItemType Directory -Force -Path $place | Out-Null
    $link = $shell.CreateShortcut((Join-Path $place 'Mod Studio.lnk'))
    $link.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $link.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launch`""
    $link.WorkingDirectory = Split-Path -Parent $PSScriptRoot
    $link.IconLocation = "$env:SystemRoot\System32\imageres.dll,186"
    $link.Description = 'Start Mod Studio (if needed) and open it'
    $link.WindowStyle = 7  # minimized: no console flash
    $link.Save()
    Write-Host "made $($link.FullName)"
}
