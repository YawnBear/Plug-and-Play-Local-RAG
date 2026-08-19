@echo off
setlocal
net session >nul 2>&1
if errorlevel 1 (
  set "TEAM_CONNECTOR_LAUNCHER=%~f0"
  powershell.exe -NoProfile -Command "Start-Process -FilePath $env:TEAM_CONNECTOR_LAUNCHER -Verb RunAs -Wait"
  exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-RagTeamConnector.ps1"
