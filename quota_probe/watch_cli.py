"""Запуск: python -m quota_probe.watch_cli [--interval 300]."""

import argparse
import sys

from .watch import QuotaWatcher, WatchError, WatchLock, WatchState, local_directory


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Постоянный безопасный монитор квот без создания модельных ходов."
    )
    parser.add_argument("--interval", type=float, default=300.0,
                        help="секунд между проверками, от 60 до 3600 (по умолчанию 300)")
    args = parser.parse_args()
    try:
        directory = local_directory()
        with WatchLock(directory):
            watcher = QuotaWatcher(
                interval=args.interval, store=WatchState(directory)
            )
            watcher.run()
    except KeyboardInterrupt:
        print("\nНаблюдатель остановлен пользователем. Codex daemon не затронут.")
        return 0
    except (WatchError, ValueError, OSError) as exc:
        print(f"Наблюдатель не запущен: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
