@echo off
setlocal
chcp 65001 >nul
rem ONE-CLICK M2 TEST BUILD: needs Python 3.11+ and installed Codex CLI.
rem The full package (launcher/ and quota_probe/) lives next to this CMD.
rem This keeps archive 0.1.0 untouched; all user paths are environment DATA.
set "SUPERVISOR_BUNDLE=%~dp0."
set "SUPERVISOR_PROJECT=%~dp0."
set "SUPERVISOR_RESUME="
if "%~1"=="" goto launch
if /i "%~1"=="--resume" (
    set "SUPERVISOR_RESUME=1"
) else (
    set "SUPERVISOR_PROJECT=%~1"
)
if /i "%~2"=="--resume" set "SUPERVISOR_RESUME=1"
:launch
start "Codex Supervisor | Windows PowerShell" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoExit -Command "$bundle = $env:SUPERVISOR_BUNDLE; $project = $env:SUPERVISOR_PROJECT; $resume = $env:SUPERVISOR_RESUME; Remove-Item Env:SUPERVISOR_BUNDLE,Env:SUPERVISOR_PROJECT,Env:SUPERVISOR_RESUME -ErrorAction SilentlyContinue; $Host.UI.RawUI.WindowTitle = 'Codex Supervisor | Windows PowerShell'; Clear-Host; try { Set-Location -LiteralPath $bundle -ErrorAction Stop; $py = Get-Command python -CommandType Application -ErrorAction Stop; $launcherArgs = @('-m','launcher','--project',$project); if ($resume) { $launcherArgs += '--resume' }; & $py.Source @launcherArgs; if ($LASTEXITCODE -ne 0) { Write-Host ('Supervisor завершился с кодом ' + $LASTEXITCODE) -ForegroundColor Yellow } } catch { Write-Host ('Ошибка запуска Supervisor: ' + $_.Exception.Message) -ForegroundColor Red }"
exit /b %errorlevel%
