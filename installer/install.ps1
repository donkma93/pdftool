param(
    [string]$InstallDir = "$env:LOCALAPPDATA\PDFTOOL"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exeSource = Join-Path $scriptDir "PDFTextEditor.exe"
if (-not (Test-Path -LiteralPath $exeSource)) {
    throw "Không tìm thấy PDFTextEditor.exe trong gói cài đặt."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -LiteralPath $exeSource -Destination (Join-Path $InstallDir "PDFTextEditor.exe") -Force

$shortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcutPath = Join-Path $shortcutDir "PDFTOOL.lnk"
$exePath = Join-Path $InstallDir "PDFTextEditor.exe"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = $InstallDir
$shortcut.Description = "PDFTOOL"
# Prefer embedded EXE icon; fallback to packaged ico if present next to installer.
$iconCandidate = Join-Path $scriptDir "pdftool.ico"
if (Test-Path -LiteralPath $iconCandidate) {
    $shortcut.IconLocation = "$iconCandidate,0"
} else {
    $shortcut.IconLocation = "$exePath,0"
}
$shortcut.Save()

Write-Host "Đã cài đặt PDFTOOL vào: $InstallDir"
Write-Host "Bạn có thể mở từ Start Menu với tên PDFTOOL."

