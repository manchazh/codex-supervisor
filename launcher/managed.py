"""Один управляемый Codex TUI + независимое read-only наблюдение за квотами.

Объединяет проверенный внешний Launcher 0.1.0 и quota_probe M1. Не
выполняет model prompts или auto-resume: лишь запускает стандартный TUI.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import threading

from quota_probe.watch import QuotaWatcher, WatchLock, WatchState, WatchError, local_directory


class LaunchError(Exception):
    """Ошибка подготовки безопасного запуска."""


def project_directory(value):
    """Версия project_directory из архивного Launcher 0.1.0."""
    value = str(value).strip().strip('"')
    if not value:
        raise LaunchError("Папка проекта не выбрана.")
    try:
        directory = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LaunchError("Папка проекта не найдена или недоступна.") from exc
    if not directory.is_dir():
        raise LaunchError("Выберите папку, а не файл.")
    return directory


def codex_argv(executable, *, directory, resume=False):
    """Явный проект для remote TUI: cwd дочернего процесса недостаточен.

    В remote-режиме Codex может брать cwd app-server daemon. Начиная с
    Codex CLI 0.118+, --cd перенаправляется серверу как cwd сессии.
    """
    if not executable:
        raise LaunchError("Codex CLI не найден. Установите CLI отдельно.")
    command = [str(executable)]
    if resume:
        command += ["resume", "--last"]
    command += ["--remote", "unix://", "--cd", str(directory), "--no-alt-screen"]
    return command


def _log_writer(log_file):
    lock = threading.Lock()
    def log(message):
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        with lock:
            log_file.write(f"{timestamp} {message}\n")
            log_file.flush()
    return log


def run_managed(directory, *, resume=False, interval=300.0,
                codex_path=None, process_factory=subprocess.Popen,
                watcher_factory=QuotaWatcher, state_dir=None, stdout=print):
    """Запустить ровно одну управляемую TUI с watcher в том же процессе.

    После выхода из TUI watcher прекращает периодические запросы.
    Не подключается к существующим чужим сессиям и не посылает модели ход.
    """
    directory = project_directory(directory)
    executable = codex_path if codex_path is not None else shutil.which("codex")
    command = codex_argv(executable, directory=directory, resume=resume)
    home = Path(state_dir) if state_dir is not None else local_directory()

    try:
        with ExitStack() as stack:
            # Внимание: пользователь может оставить старый самостоятельный
            # watcher запущенным. НИКОГДА не завершаем его ради нового.
            stack.enter_context(WatchLock(home))
            logfile = stack.enter_context(
                (home / "managed-watch.log").open("a", encoding="utf-8")
            )
            stop = threading.Event()
            watcher = watcher_factory(
                interval=interval,
                store=WatchState(home),
                write=_log_writer(logfile),
            )
            thread = threading.Thread(
                target=lambda: watcher.run(stop_event=stop), daemon=True,
                name="codex-supervisor-managed-quota-watch",
            )
            thread.start()
            if not thread.is_alive():
                raise LaunchError("Не удалось запустить монитор квот.")

            stdout("Монитор квот запущен вместе с Codex; информация записывается "
                   "локально. Автопродолжение пока НЕ реализовано.")
            stdout("Для выхода из Codex используй /quit. Монитор завершится автоматически.")
            try:
                # Наследуем stdin/stdout/stderr: НЕ подменяем штатный TUI,
                # не перехватываем клавиатуру, не читаем переписку.
                child = process_factory(command, cwd=str(directory), shell=False)
                try:
                    return child.wait()
                except KeyboardInterrupt:
                    # Не убиваем работающий Codex в ответ на Ctrl+C.
                    stdout("Ctrl+C обработан; ожидаем завершения Codex через /quit.")
                    return child.wait()
            finally:
                stop.set()
                thread.join(timeout=20.0)
                if thread.is_alive():
                    stdout("Предупреждение: запрос квот ещё завершается; "
                           "дочерний Codex уже закрыт.")
    except WatchError as exc:
        raise LaunchError(
            f"Монитор уже работает или не может использовать хранилище: {exc} "
            "Не завершаем существующий монитор и не запускаем новый Codex."
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchError("Не удалось запустить Codex или открыть локальный лог.") from exc
