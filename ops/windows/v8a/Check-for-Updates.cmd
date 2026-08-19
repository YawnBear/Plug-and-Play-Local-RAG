@echo off
setlocal
findstr /c:"\"payload_state\": \"development_template\"" "%~dp0personal-release.json" >nul 2>&1
if not errorlevel 1 (
  echo Local RAG is running from a source clone.
  echo.
  echo One-click source updates are not available yet.
  echo Create a verified backup, close Local RAG, and follow the update notes
  echo for the version you are moving to. Do not replace the data folders.
  pause
  exit /b 0
)
findstr /c:"\"payload_state\": \"assembled_unsigned\"" "%~dp0personal-release.json" >nul 2>&1
if not errorlevel 1 (
  echo This Local RAG preview was installed without release signing.
  echo.
  echo Automatic updates are disabled for unsigned preview installations.
  echo Download a newer preview installer manually when one is available.
  pause
  exit /b 0
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-RagPersonal.ps1" -Mode Guided
if errorlevel 1 (
  echo.
  echo The update stopped safely. Your current release and data were preserved or restored.
  echo Run Check for updates again, or use the recovery shortcut if instructed.
  pause
  exit /b 1
)
echo.
echo Update check completed.
pause
exit /b 0
