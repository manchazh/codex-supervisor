@echo off
setlocal
chcp 65001 >nul
rem Пути передаются как данные окружения, а не как исполняемый PowerShell-код.
set "CODEX_LAUNCHER_SCRIPT=%~dp0launch.py"
set "CODEX_LAUNCHER_PROJECT=%~1"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoExit -Command "$launcherArgs = @($env:CODEX_LAUNCHER_SCRIPT); if ($env:CODEX_LAUNCHER_PROJECT) { $launcherArgs += $env:CODEX_LAUNCHER_PROJECT }; Remove-Item Env:CODEX_LAUNCHER_SCRIPT,Env:CODEX_LAUNCHER_PROJECT -ErrorAction SilentlyContinue; if (Get-Command py -CommandType Application -ErrorAction SilentlyContinue) { & py -3 @launcherArgs } elseif (Get-Command python -CommandType Application -ErrorAction SilentlyContinue) { & python @launcherArgs } else { Write-Host 'Python 3.11+ required. See README.md.' }"
exit /b %errorlevel%
