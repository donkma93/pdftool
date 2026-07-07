param(
    [string]$InstallDir = "$env:LOCALAPPDATA\PDFTOOL"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $scriptDir "install.ps1") -InstallDir $InstallDir

