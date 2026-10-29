# Run from a native Windows checkout; writes only desktop build/cache/output folders.
[CmdletBinding()]
param([switch]$SkipDependencyInstall)
$ErrorActionPreference = 'Stop'
$DesktopRoot = Split-Path -Parent $PSScriptRoot
$PreviousLocation = Get-Location
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
try {
    Set-Location $DesktopRoot
    if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
        throw 'Build the Windows Runtime bundle on Windows.'
    }
    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { throw 'Use Node.js 24 or later, then run this script again.' }
    Invoke-Checked 'node.exe' @('-e', 'if(Number(process.versions.node.split(".")[0])<24) process.exit(1)')
    if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11 or later, then run this script again.' }
    Invoke-Checked 'python.exe' @('-c', 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"')
    $env:npm_config_cache = Join-Path $DesktopRoot '.build-cache/npm'
    $env:ELECTRON_CACHE = Join-Path $DesktopRoot '.build-cache/electron'
    $env:ELECTRON_BUILDER_CACHE = Join-Path $DesktopRoot '.build-cache/electron-builder'
    if (-not $SkipDependencyInstall) { Invoke-Checked 'npm.cmd' @('ci') }
    if (-not (Test-Path '.runtime-venv/Scripts/python.exe')) { Invoke-Checked 'python.exe' @('-m', 'venv', '.runtime-venv') }
    $BuildPython = Join-Path $DesktopRoot '.runtime-venv/Scripts/python.exe'
    if (-not $SkipDependencyInstall) { Invoke-Checked $BuildPython @('-m', 'pip', 'install', '-r', 'requirements-build.txt') }
    Invoke-Checked $BuildPython @('tools/bundle_runtime.py')
    Invoke-Checked $BuildPython @('tools/bundle_agents.py', '--target', 'win32-x64')
    $env:XIAOYU_TEST_RUNTIME = Join-Path $DesktopRoot 'runtime-dist/xiaoyu-runtime/xiaoyu-runtime.exe'
    Invoke-Checked 'npm.cmd' @('test')
    Invoke-Checked 'npm.cmd' @('run', 'package:win')
    Get-ChildItem 'release/*.exe' | Get-FileHash -Algorithm SHA256 | Format-Table -AutoSize
    Write-Host 'Installer built. Native install, provider login, background and cross-network acceptance remain separate checks.'
} finally {
    Set-Location $PreviousLocation
}
