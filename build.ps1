# SPDX-License-Identifier: Apache-2.0
# build.ps1: build dist\OpenLIFUUpdater.exe and dist\openlifu-fw-updater.exe
# from OpenLIFUUpdater.spec, then start each once.
#
#   .\build.ps1                 uses the python on PATH (activate the env first)
#   .\build.ps1 -Python <path>  uses that interpreter
#
# The env needs this package with the build extra (pip install -e .[build])
# and a release embedded (python scripts/fetch_release.py <tag>).
param(
    [string]$Python = "python"
)

# Failures are checked by exit code: under Windows PowerShell 5.1 a native
# command's stderr (PyInstaller logs there) is an error record when redirected.
Set-Location -Path $PSScriptRoot -ErrorAction Stop

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $Python -m PyInstaller -y --clean OpenLIFUUpdater.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

# Command line: it must start and parse its arguments. Nothing that opens the unit.
& dist\openlifu-fw-updater.exe --help | Out-Host
if ($LASTEXITCODE -ne 0) { throw "dist\openlifu-fw-updater.exe --help failed (exit $LASTEXITCODE)." }

# Window: build the window, load and verify the embedded release, load libusb.
# It does not open the unit. A GUI exe does not hold the shell, so wait for it.
$gui = Start-Process -FilePath dist\OpenLIFUUpdater.exe -ArgumentList "--self-test" -Wait -PassThru
if ($gui.ExitCode -ne 0) {
    Get-Content dist\update-*.log -ErrorAction SilentlyContinue | Out-Host
    throw "dist\OpenLIFUUpdater.exe failed its self-test (exit $($gui.ExitCode))."
}
Get-Content dist\update-*.log -ErrorAction SilentlyContinue | Select-String "self-test" | Out-Host

Remove-Item dist\update-*.log -ErrorAction SilentlyContinue
Write-Host "Built dist\OpenLIFUUpdater.exe and dist\openlifu-fw-updater.exe" -ForegroundColor Green
exit 0
