@echo off
setlocal
chcp 65001 >nul
rem Запуск Codex. По умолчанию проект — папка самого файла. Python не требуется.
rem Пути передаются как данные окружения, а не как исполняемый PowerShell-код.
set "CODEX_LAUNCHER_PROJECT=%~dp0."
set "CODEX_LAUNCHER_RESUME="
if "%~1"=="" goto launch
if /i "%~1"=="--resume" (
    set "CODEX_LAUNCHER_RESUME=1"
) else (
    set "CODEX_LAUNCHER_PROJECT=%~1"
)
if /i "%~2"=="--resume" set "CODEX_LAUNCHER_RESUME=1"
:launch
start "Codex | Windows PowerShell" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoExit -Command "$launcherProject = $env:CODEX_LAUNCHER_PROJECT; $launcherResume = $env:CODEX_LAUNCHER_RESUME; Remove-Item Env:CODEX_LAUNCHER_PROJECT,Env:CODEX_LAUNCHER_RESUME -ErrorAction SilentlyContinue; $Host.UI.RawUI.WindowTitle = 'Codex | Windows PowerShell'; Clear-Host; try { Set-Location -LiteralPath $launcherProject -ErrorAction Stop; if ($launcherResume) { $launcherMode = '2' } else { Write-Host '1 - Новая сессия'; Write-Host '2 - Продолжить последнюю сессию этого проекта'; Write-Host '0 - Отмена'; $launcherMode = ([string](Read-Host 'Выберите 1 или 2 (Enter = 1)')).Trim() }; if ($launcherMode -eq '0') { Write-Host 'Запуск отменён.' } else { if ($launcherMode -notin @('', '1', '2')) { throw 'Выберите 1, 2 или 0.' }; $launcherCodex = Get-Command codex -CommandType Application -ErrorAction Stop; if ($launcherMode -eq '2') { & $launcherCodex.Source resume --last } else { & $launcherCodex.Source } } } catch { Write-Host ('Ошибка запуска Codex: ' + $_.Exception.Message) -ForegroundColor Red }"
exit /b %errorlevel%
