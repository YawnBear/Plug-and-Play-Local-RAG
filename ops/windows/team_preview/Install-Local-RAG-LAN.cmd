@echo off
setlocal
net session >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\windows\team_preview\Install-RagTeamLanPreview.ps1" -PayloadRoot "%~dp0" -Confirm
if errorlevel 1 pause
