@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\windows\v8a\Setup-RagFromSource.ps1" %*
if errorlevel 1 (
  echo.
  echo Local RAG setup stopped safely. Existing data was not deleted.
  echo Fix the item shown above, then double-click Setup-Local-RAG.cmd again.
  pause
  exit /b 1
)
echo.
echo Local RAG setup completed.
pause
exit /b 0
