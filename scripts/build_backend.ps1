[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
$entryPoint = Join-Path $repoRoot 'scripts\docprocessor_cli.py'
$distPath = Join-Path $repoRoot 'gui\build\backend'
$workPath = Join-Path $repoRoot 'gui\build\pyinstaller-work'
$specPath = Join-Path $repoRoot 'gui\build\pyinstaller-spec'

if (-not (Test-Path $pythonExe)) {
    throw "Python virtual environment not found at $pythonExe. Create .venv and install requirements first."
}

$pyInstallerPackage = Get-ChildItem -Path (Join-Path $repoRoot '.venv\Lib\site-packages') -Filter 'PyInstaller' -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pyInstallerPackage) {
    Write-Host 'Installing PyInstaller into the project virtual environment...'
    & $pythonExe -m pip install pyinstaller
}

Remove-Item $distPath -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $distPath -Force | Out-Null
New-Item -ItemType Directory -Path $workPath -Force | Out-Null
New-Item -ItemType Directory -Path $specPath -Force | Out-Null

& $pythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name docprocessor `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    --paths $repoRoot `
    --collect-submodules azure `
    --collect-submodules pydantic_settings `
    --collect-submodules docprocessor `
    $entryPoint

$backendExe = Join-Path $distPath 'docprocessor.exe'
if (-not (Test-Path $backendExe)) {
    throw "Backend build failed; expected $backendExe"
}

Write-Host "Bundled backend created: $backendExe"
