# Libre_Core AutoLoader - Windows Installer Script
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\LibreCoreAutoLoader"
)

Write-Host "=== Installing Libre_Core AutoLoader ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$distPath = Join-Path $PSScriptRoot "dist\autoloader"
if (Test-Path $distPath) {
    Copy-Item -Recurse -Force "$distPath\*" $InstallDir
    Write-Host "Copied binary files to: $InstallDir" -ForegroundColor Green
} else {
    Write-Host "Warning: dist\autoloader not found. Run pyinstaller build first." -ForegroundColor Yellow
}

# Add to user PATH if not present
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    Write-Host "Added $InstallDir to User PATH." -ForegroundColor Green
}

Write-Host "Installation complete! Run 'autoloader' from your terminal." -ForegroundColor Green
