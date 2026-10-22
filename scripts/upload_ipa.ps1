<#
Manual upload: takes an unsigned mod .ipa and publishes it to Accretion's private storage.

  scripts/upload_ipa.ps1                      newest *_accretion.ipa in C:\AccretionMods\builds
  scripts/upload_ipa.ps1 -Mod wanderer-dev    newest build of that mod
  scripts/upload_ipa.ps1 -Pick                list the builds and type the number to upload
  scripts/upload_ipa.ps1 -Number 3            the third one of that list, no question
  scripts/upload_ipa.ps1 -Folder D:\out       look in another folder
  scripts/upload_ipa.ps1 -File <path.ipa>     that file
  scripts/upload_ipa.ps1 -MinOS 15.0          minimum iOS to record (optional)
  scripts/upload_ipa.ps1 -AllowPersonal       also publish a mod marked "personal": true (asked for by name)
  scripts/upload_ipa.ps1 -List                only list the candidates, upload nothing
  scripts/upload_ipa.ps1 -DryRun              check everything and print the command, upload nothing

Candidates are *_accretion.ipa files (what scripts/local_ipa.sh makes); signed copies and other files are
skipped. The mod name comes from the file name (<mod>_<version>_accretion.ipa) and must have mods/<mod>/mod.json.
A personal mod ("personal": true) is refused unless -AllowPersonal is given; then publish.py records it with
"personalPublished" and mods.json lists it. Its mods/<mod>/mod.json must be in git, or the next catalogue
rebuild on main cannot see it: the script warns when it is not.
Hands the file to scripts/publish.py, which uploads it with the git-ignored
.upload-key (or ACCRETION_UPLOAD_KEY) and records it in releases.json and mods.json. It does not commit or
push: the store sees the build once those two files reach main (scripts/checked_push.py or a pull request).
Runs publish.py with uv (python on this PC's PATH is only the Store stub). Exit code 0 only after publish.py
succeeded (or after -List / -DryRun).
#>
param(
    [string]$Folder = "C:\AccretionMods\builds",
    [string]$File = "",
    [string]$Mod = "",
    [switch]$Pick,
    [int]$Number = 1,
    [string]$MinOS = "",
    [switch]$AllowPersonal,
    [switch]$List,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if ($File -eq "") {
    if (-not (Test-Path $Folder)) { Write-Host "No folder $Folder"; exit 1 }
    $filter = if ($Mod) { "${Mod}_*_accretion.ipa" } else { "*_accretion.ipa" }
    $ipas = @(Get-ChildItem -Path $Folder -Filter $filter -File | Sort-Object LastWriteTime -Descending)
    if ($ipas.Count -eq 0) { Write-Host "No $filter in $Folder"; exit 1 }
    Write-Host "Unsigned mod builds in $Folder (newest first):"
    $i = 1
    foreach ($ipa in $ipas) {
        $mb = [math]::Round($ipa.Length / 1MB, 1)
        $tag = ""
        if ($ipa.Name -match '^(?<m>[a-z0-9-]+)_') {
            $mj = Join-Path $repo "mods\$($Matches.m)\mod.json"
            if ((Test-Path $mj) -and (Get-Content $mj -Raw | ConvertFrom-Json).personal) { $tag = "  [personal]" }
        }
        Write-Host ("  {0,2}. {1}  ({2} MB, {3:yyyy-MM-dd HH:mm}){4}" -f $i, $ipa.Name, $mb, $ipa.LastWriteTime, $tag)
        $i++
    }
    if ($List) { exit 0 }
    $n = $Number
    if ($Pick) {
        $answer = Read-Host "Number to upload (Enter = $Number)"
        if ($answer -ne "") { $n = [int]$answer }
    }
    if ($n -lt 1 -or $n -gt $ipas.Count) { Write-Host "No build number $n (1 to $($ipas.Count))"; exit 1 }
    $File = $ipas[$n - 1].FullName
    Write-Host "Taking $n. $($ipas[$n - 1].Name)"
}

if (-not (Test-Path $File)) { Write-Host "No file at $File"; exit 1 }
$name = [IO.Path]::GetFileName($File)
if ($name -notmatch '^(?<mod>[a-z0-9-]+)_(?<version>[^_]+)_accretion\.ipa$') {
    Write-Host "Expected <mod>_<version>_accretion.ipa (what scripts/local_ipa.sh makes), got $name"; exit 1
}
$mod = $Matches.mod
$version = $Matches.version
$meta = Join-Path $repo "mods\$mod\mod.json"
if (-not (Test-Path $meta)) { Write-Host "No mods/$mod/mod.json in $repo"; exit 1 }
$personal = [bool](Get-Content $meta -Raw | ConvertFrom-Json).personal
if ($personal -and -not $AllowPersonal) {
    Write-Host "mods/$mod is a personal mod (""personal"": true). To publish it anyway, run again with -AllowPersonal."
    exit 1
}
if (-not (Test-Path (Join-Path $repo ".upload-key")) -and -not $env:ACCRETION_UPLOAD_KEY) {
    Write-Host "No .upload-key in $repo and no ACCRETION_UPLOAD_KEY"; exit 1
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) {
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"  # uv's usual home, before a fresh shell picks it up
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Host "uv is not on PATH"; exit 1 }

$uvArgs = @("run", "--no-project", "--python", "3.12", "python", (Join-Path $repo "scripts\publish.py"), "--mod", $mod, "--ipa", $File)
if ($MinOS) { $uvArgs += @("--min-os", $MinOS) }
if ($personal) { $uvArgs += "--allow-personal" }

Write-Host ""
Write-Host "Upload $name"
Write-Host "  mod      $mod$(if ($personal) { ' (personal, published because of -AllowPersonal)' })"
Write-Host "  version  $version"
if ($MinOS) { Write-Host "  min iOS  $MinOS" }
$tracked = git -C $repo ls-files -- "mods/$mod/mod.json"
if (-not $tracked) {
    Write-Host "  warning  mods/$mod/mod.json is not in git: commit the mod folder, or the catalogue on main drops it"
}
if ($DryRun) {
    Write-Host "  dry run  uv $($uvArgs -join ' ')"
    exit 0
}

$env:PYTHONUTF8 = "1"
Push-Location $repo
try {
    & uv @uvArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) { Write-Host "publish.py failed (exit $code)"; exit $code }
Write-Host ""
Write-Host "Recorded in releases.json and mods.json. Not committed: push them to main (scripts/checked_push.py or a PR)"
Write-Host "and the store picks the build up on its next build."
