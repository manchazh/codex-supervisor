"""Единый интерфейс PowerShell: python -m launcher --project <PATH>."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

from .managed import LaunchError, codex_argv, project_directory, run_managed


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Codex TUI и read-only Supervisor — единый безопасный запуск."
    )
    parser.add_argument("--project", default=".", help="папка проекта")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="последняя сессия папки")
    mode.add_argument("--new", action="store_true", help="новая сессия, без меню")
    parser.add_argument("--interval", type=int, default=300,
                        help="пауза между служебными проверками квот в секундах, от 60 до 3600")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать параметры, НЕ запускать Codex или watcher")
    args = parser.parse_args(argv)
    try:
        directory = project_directory(args.project)
        if args.interval < 60 or args.interval > 3600:
            raise LaunchError("Интервал проверок должен быть 60–3600 секунд.")
        if args.resume or args.new:
            resume = args.resume
        else:
            print("1 — Новая сессия (Enter)")
            print("2 — Продолжить последнюю сессию текущего проекта")
            print("0 — Отмена")
            choice = input("Выбери режим: ").strip()
            if choice == "0":
                print("Запуск отменён.")
                return 0
            if choice not in ("", "1", "2"):
                raise LaunchError("Выбери 1, 2 или 0.")
            resume = choice == "2"
        if args.dry_run:
            command = codex_argv("codex", resume=resume)
            print("Проверка без запуска Codex, модели и фонового монитора.")
            print(f"Проект: {directory}")
            print(f"Режим: {'последняя сессия проекта' if resume else 'новая сессия'}")
            print("Команда: " + " ".join(command))
            print(f"Интервал read-only проверки: {args.interval} секунд.")
            return 0
        return run_managed(directory, resume=resume, interval=args.interval)
    except LaunchError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("Запуск отменён: нет ввода.")
        return 0
    except KeyboardInterrupt:
        print("\nЗапуск прерван пользователем.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
