$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$releaseName = "GPR_Lab_Pro_V3"
$distRoot = Join-Path $env:TEMP "gpr_dist_v3"
$workRoot = Join-Path $env:TEMP "gpr_build_v3"
$releaseRoot = ".\release"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Missing virtual environment: .venv"
}

if (-not (Test-Path ".\.venv\Scripts\pyinstaller.exe")) {
    & .\.venv\Scripts\python.exe -m pip install pyinstaller
}
if (Test-Path $distRoot) {
    Remove-Item -LiteralPath $distRoot -Recurse -Force -ErrorAction SilentlyContinue
}
if (Test-Path $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
}

& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --distpath $distRoot --workpath $workRoot .\GPR_V11_Pyside.spec

if (-not (Test-Path $releaseRoot)) {
    New-Item -ItemType Directory -Path $releaseRoot | Out-Null
}
$releasePath = Join-Path $releaseRoot $releaseName
if (Test-Path $releasePath) {
    Remove-Item -LiteralPath $releasePath -Recurse -Force
}
Copy-Item -Path (Join-Path $distRoot $releaseName) -Destination $releasePath -Recurse -Force
Copy-Item -Path ".\qt.conf" -Destination (Join-Path $releasePath "qt.conf") -Force
Copy-Item -Path ".\release_launcher.bat" -Destination (Join-Path $releasePath "release_launcher.bat") -Force
Copy-Item -Path ".\RELEASE_INSTRUCTIONS.txt" -Destination (Join-Path $releasePath "RELEASE_INSTRUCTIONS.txt") -Force

$zipPath = Join-Path $releaseRoot "$releaseName.zip"
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path $releasePath -DestinationPath $zipPath

Write-Host "Release package ready:"
Write-Host "  $PSScriptRoot\release\$releaseName"
Write-Host "  $PSScriptRoot\release\$releaseName.zip"
