@echo off
setlocal
net session >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
set /p RAG_PREVIEW_IPV4=Enter the new private IPv4 address:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\windows\team_preview\Repair-RagTeamLanPreview.ps1" -LocalAddress "%RAG_PREVIEW_IPV4%" -Confirm
if errorlevel 1 pause
