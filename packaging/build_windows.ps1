param(
    [string]$Python = "python",
    [string]$Version = "0.0.0-dev",
    [switch]$SkipDataRefresh
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Push-Location $Root
try {
    if (-not $SkipDataRefresh) {
        & $Python buildgen\party.py buildgen\party.allflame.json `
            --out-dir builds\allflame
        if ($LASTEXITCODE -ne 0) { throw "Prepared-build generation failed." }

        & $Python tools\fetch_layouts.py
        if ($LASTEXITCODE -ne 0) { throw "Layout download failed." }
    }

    & $Python -m PyInstaller packaging\poe_league_tools.spec `
        --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $Exe = Join-Path $Root "dist\PoE League Tools\PoE League Tools.exe"
    & $Exe --self-test
    if ($LASTEXITCODE -ne 0) { throw "Packaged-app self-test failed." }

    $IsccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $IsccPath = if ($IsccCommand) { $IsccCommand.Source } else { $null }
    if (-not $IsccPath) {
        $Candidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        if (Test-Path $Candidate) {
            $IsccPath = $Candidate
        }
    }
    if (-not $IsccPath) {
        throw "Inno Setup 6 was not found. Install it from jrsoftware.org."
    }

    & $IsccPath "/DAppVersion=$Version" `
        packaging\poe_league_tools.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

    $Installer = Join-Path $Root `
        "dist\installer\PoE-League-Tools-Setup.exe"
    Write-Host ""
    Write-Host "Built: $Installer"
}
finally {
    Pop-Location
}
