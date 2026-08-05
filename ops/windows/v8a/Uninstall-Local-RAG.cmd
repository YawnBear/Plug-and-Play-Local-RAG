@echo off
setlocal
echo Local RAG uninstall
echo.
echo 1. Keep my data (recommended)
echo 2. I already created a restore-verified export; keep my data
echo 3. Permanently delete all Local RAG data
choice /c 123 /n /m "Choose 1, 2, or 3: "
if errorlevel 3 goto delete
if errorlevel 2 goto export
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-RagPersonal.ps1" -DataAction Preserve
goto done
:export
set /p RAG_BACKUP="Paste the restore-verified backup folder path: "
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-RagPersonal.ps1" -DataAction Export -VerifiedBackupBundle "%RAG_BACKUP%"
goto done
:delete
echo.
echo This permanently deletes documents, database records, settings, and local indexes.
set /p RAG_DELETE="Type DELETE LOCAL RAG DATA to continue: "
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-RagPersonal.ps1" -DataAction Delete -DeleteConfirmation "%RAG_DELETE%"
:done
if errorlevel 1 (
  echo.
  echo Uninstall stopped safely. Review the message above.
  pause
  exit /b 1
)
echo.
echo Local RAG was uninstalled using the selected data choice.
pause
exit /b 0
