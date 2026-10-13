"""Неизменяющая диагностика установленного CLI: только --help и --version."""

from __future__ import annotations

import shutil
import subprocess


def _read(argv: list[str]):
    try:
        proc = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            timeout=8, check=False, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode:
        return None
    return proc.stdout + "\n" + proc.stderr


def inspect_cli(*, which=shutil.which, read=_read):
    """Отдаём только флаги возможностей, не скрытые файлы/чат/пути."""
    exe = which("codex")
    if not exe:
        return {"installed": False, "reason": "Команда codex не найдена в PATH."}
    version = read([exe, "--version"])
    main_help = read([exe, "--help"])
    resume_help = read([exe, "resume", "--help"])
    server_help = read([exe, "app-server", "--help"])
    return {
        "installed": version is not None,
        "version": (version or "не удалось прочитать").strip()[:120],
        "help_available": main_help is not None,
        "supports_app_server": bool(main_help and "app-server" in main_help and server_help),
        "supports_remote_tui": bool(main_help and "--remote" in main_help),
        "supports_remote_resume": bool(resume_help and "--remote" in resume_help),
        "supports_no_alt_screen": bool(main_help and "--no-alt-screen" in main_help),
        "note": "Это только синтаксис CLI, не доказательство рабочего remote TUI или квоты.",
    }
