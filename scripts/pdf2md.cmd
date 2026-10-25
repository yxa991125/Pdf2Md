@echo off
setlocal DisableDelayedExpansion
rem Limit the execution-policy override to this child process; never change system policy.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0pdf2md.ps1" %*
exit /b %errorlevel%
