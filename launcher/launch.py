"""Переносимый запуск интерактивного Codex; без Supervisor и автопродолжения."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


class LaunchError(Exception):
    """Понятная пользователю ошибка перед запуском."""


def project_directory(value):
    # Данные никогда не вставляются в shell-команду.
    value = value.strip().strip('"')
    if not value:
        raise LaunchError("Папка проекта не выбрана.")
    try:
        directory = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LaunchError("Папка проекта не найдена или недоступна.") from exc
    if not directory.is_dir():
        raise LaunchError("Выберите папку, а не файл.")
    return directory


def launch(directory, resume=False):
    executable = shutil.which("codex")
    if not executable:
        raise LaunchError(
            "Codex CLI не найден в PATH. Установите его по официальной инструкции "
            "и откройте новое окно терминала."
        )
    # Только известные аргументы. Путь проекта передаётся как cwd, включая
    # пробелы и shell-метасимволы; stdin/stdout/stderr наследуются для TUI.
    command = [executable]
    if resume:
        command.extend(["resume", "--last"])
    try:
        return subprocess.call(command, cwd=str(directory), shell=False)
    except OSError as exc:
        raise LaunchError("Не удалось запустить Codex CLI. Проверьте установку.") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Запуск Codex в любом локальном проекте. Настройки Codex не меняются."
    )
    parser.add_argument("project", nargs="?", help="Папка проекта (в кавычках при наличии пробелов)")
    parser.add_argument("--resume", action="store_true", help="Продолжить последнюю сессию этой папки")
    args = parser.parse_args(argv)
    try:
        if args.project is None:
            print("Codex · переносимая запускалка")
            print("Настройки и авторизация используются с этого ПК.")
            print("Не запускайте два агента для правок одного проекта одновременно.")
            directory = project_directory(input("Папка проекта (можно вставить путь): "))
            mode = "2" if args.resume else input("1 — новый диалог; 2 — последняя сессия проекта; Enter — отмена: ").strip()
            if not mode:
                print("Запуск отменён.")
                return 0
            if mode not in {"1", "2"}:
                raise LaunchError("Выберите 1 или 2.")
            resume = mode == "2"
        else:
            directory = project_directory(args.project)
            resume = args.resume
        print("Открывается интерактивный Codex. Для выхода используйте /quit или Ctrl+C.", flush=True)
        return launch(directory, resume)
    except LaunchError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("Запуск отменён: ввод закрыт.")
        return 0
    except KeyboardInterrupt:
        print("\nЗапуск прерван пользователем.")
        return 130


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        print("Нужен Python 3.11 или новее.", file=sys.stderr)
        raise SystemExit(1)
    if os.name == "nt":
        # UTF-8 для консольных сообщений; окружение Codex не переопределяется.
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
