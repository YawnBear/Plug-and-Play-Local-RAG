@echo off
setlocal
if exist "%~dp0ops\windows\v8a\personal-release.json" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\windows\v8a\Verify-and-Install-Local-RAG.ps1" -AssetRoot "%~dp0" -UnsignedPreview
  goto :install_complete
)
if exist "%~dp0personal-release.json" (
  findstr /c:"\"payload_state\": \"development_template\"" "%~dp0personal-release.json" >nul
  if not errorlevel 1 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-RagFromSource.ps1"
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Verify-and-Install-Local-RAG.ps1"
  )
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Verify-and-Install-Local-RAG.ps1"
)
:install_complete
if errorlevel 1 (
  echo.
  echo Local RAG setup stopped safely. No existing data was deleted.
  echo Review the message above, correct the prerequisite, and run this installer again.
  pause
  exit /b 1
)
echo.
echo Local RAG system preparation completed.
pause
exit /b 0
