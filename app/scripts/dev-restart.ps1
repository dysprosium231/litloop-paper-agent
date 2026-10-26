# Restart the LitLoop desktop app for development, then return.
#
# Stops a running dev window, its Python backend and the Vite server on port
# 1420, starts "npm run tauri dev" as a separate hidden process (the app
# window itself still opens), and waits until the window is running.
# Output goes to app/.dev.log.
#
# Uses the current environment: cargo must be on PATH (or set CARGO_HOME /
# RUSTUP_HOME), LITLOOP_PYTHON may point at the Python that has LitLoop.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File app/scripts/dev-restart.ps1

$ErrorActionPreference = "Stop"
$App = Split-Path $PSScriptRoot -Parent
$Log = Join-Path $App ".dev.log"

Get-Process litloop-app -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "litloop\.(desktop\.server|app_server)" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# This app's Vite, found by command line: netstat's
# state names are localized, so they are not matched.
$vite = [regex]::Escape((Join-Path $App "node_modules"))
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -match $vite } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# A stopped server can hold the port for a moment; starting too early fails.
$free = (Get-Date).AddSeconds(15)
while ((Get-NetTCPConnection -LocalPort 1420 -ErrorAction SilentlyContinue | Where-Object { $_.State -ne "TimeWait" }) -and (Get-Date) -lt $free) {
    Start-Sleep -Milliseconds 500
}

if ($env:CARGO_HOME) { $env:PATH = (Join-Path $env:CARGO_HOME "bin") + ";" + $env:PATH }
# Keep the previous run's log (a download test's timings were lost twice when a
# restart overwrote it): .dev.log.bak-<time>, the newest 5 kept (gitignored).
if (Test-Path $Log) {
    Move-Item $Log (Join-Path $App (".dev.log.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))) -Force
    Get-ChildItem $App -Filter ".dev.log.bak-*" | Sort-Object Name -Descending |
        Select-Object -Skip 5 | Remove-Item -Force -ErrorAction SilentlyContinue
}
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run tauri dev > .dev.log 2>&1" `
    -WorkingDirectory $App -WindowStyle Hidden

$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $text = if (Test-Path $Log) { Get-Content $Log -Raw -Encoding UTF8 } else { "" }
    if ($text -match "error\[|Error ") {
        Get-Process litloop-app -ErrorAction SilentlyContinue | Stop-Process -Force  # a window without its page
        Write-Output "FAILED, see $Log"
        Write-Output ($text -split "`n" | Select-Object -Last 15)
        exit 1
    }
    if (Get-Process litloop-app -ErrorAction SilentlyContinue) {
        Start-Sleep -Seconds 3  # Vite may still fail just after the window starts
        if ((Get-Content $Log -Raw -Encoding UTF8) -match "error\[|Error ") { continue }
        Write-Output "app running (log: $Log)"
        exit 0
    }
}
Write-Output "timed out waiting for the window, see $Log"
exit 1
