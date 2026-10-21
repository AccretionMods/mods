# am: the mod dev loop (scripts/dev.py) from PowerShell, without typing the uv command each time.
#   scripts\am.ps1 new my-game-plus "$HOME\Downloads\MyGame-Decrypted.ipa"
#   scripts\am.ps1 pick my-game-plus Damage
#   scripts\am.ps1 build my-game-plus --ipa
# To call it as just `am` from anywhere, add this line to your PowerShell profile ($PROFILE):
#   function am { & "C:\Users\<you>\GitHub\mods\scripts\am.ps1" @args }
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not on PATH (https://docs.astral.sh/uv/). Or run: py -3 $PSScriptRoot\dev.py $args"
    exit 1
}
if ($args.Count -eq 0) {
    & uv run --no-project --python 3.12 "$PSScriptRoot\dev.py" --help
    exit $LASTEXITCODE
}
& uv run --no-project --python 3.12 "$PSScriptRoot\dev.py" @args
exit $LASTEXITCODE
