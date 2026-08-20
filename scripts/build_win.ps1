# Win10 桌面端打包脚本
# 用法（在项目根目录）:
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_win.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "==> Working dir: $(Get-Location)"

if (Test-Path ".\venv\Scripts\Activate.ps1") {
    Write-Host "==> Activating venv"
    & ".\venv\Scripts\Activate.ps1"
} else {
    Write-Host "==> venv not found, using current Python"
}

Write-Host "==> Installing requirements"
python -m pip install -r requirements.txt

Write-Host "==> Building with PyInstaller"
python -m PyInstaller --noconfirm --clean build.spec

$exe = ".\dist\TranslateTool\TranslateTool.exe"
if (Test-Path $exe) {
    Write-Host "==> Build OK: $exe"
} else {
    Write-Error "Build failed: $exe not found"
}
