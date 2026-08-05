@echo off
setlocal
set "RAG_SOURCE_ARG="
findstr /c:"\"payload_state\": \"development_template\"" "%~dp0personal-release.json" >nul 2>&1
if not errorlevel 1 set "RAG_SOURCE_ARG=-DevelopmentSource"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-RagPersonal.ps1" %RAG_SOURCE_ARG% %*
exit /b %ERRORLEVEL%
