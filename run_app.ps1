Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $python) {
    & $python -m gpr_lab_pro.app
}
else {
    python -m gpr_lab_pro.app
}
