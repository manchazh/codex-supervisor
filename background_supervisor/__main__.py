"""Выполнение: python -m background_supervisor doctor|demo.

M0: не запускает модель, TUI, app-server, сеть или процессы, кроме codex --help.
"""

from __future__ import annotations

import argparse
import time

from .doctor import inspect_cli
from .monitor import BackgroundMonitor, Binding, Mode, QuotaSnapshot


def _doctor():
    result = inspect_cli()
    print("Codex Supervisor · диагностика M0 (без модели)")
    for key, value in result.items():
        print(f"{key}: {value}")
    print("Автоматическое продолжение в M0 НЕ РЕАЛИЗОВАНО.")


def _demo(ticks: int, interval: float):
    monitor = BackgroundMonitor(Binding(
        project_id="demo-only", branch="demo",
        thread_id="mock-thread-001", account_id="mock-account",
        limit_id="codex", task_id="demo-task",
    ), guard_seconds=2.0, max_snapshot_age=10.0)
    start = 2000000000.0  # Виртуальные Unix-секунды.
    n = 0
    previous = None
    print("M0: только виртуальные события. Не подключаемся к Codex и не отправляем turn.")
    print("Ctrl+C — остановить. ticks=0 означает бесконечный MOCK-наблюдатель.")
    try:
        while ticks == 0 or n < ticks:
            now = start + n
            if n == 1:
                monitor.quota_error(
                    code="usageLimitExceeded", account_id="mock-account",
                    thread_id="mock-thread-001", turn_id="mock-turn-1",
                    server_will_retry=False, failed_confirmed=True,
                )
            if n >= 2 and monitor.mode == Mode.WAITING_QUOTA:
                # Имитация того, что квота сбрасывается в момент start+4.
                monitor.observe(QuotaSnapshot(
                    account_id="mock-account", limit_id="codex",
                    ordinary_usage_allowed=(n >= 5),
                    blocking_resets_at=(start + 4,),
                    observed_at=now,
                    complete=True,
                ), now=now)
            if monitor.mode != previous or n % 10 == 0:
                print(f"Шаг {n:>3} · {monitor.mode.value} · {monitor.last_reason}", flush=True)
                previous = monitor.mode
            if monitor.mode == Mode.READY and monitor.resume_intent():
                print("DEMO: выдано одно разрешение на будущую проверку продолжения; "
                      "фактического turn/start НЕТ.", flush=True)
            n += 1
            if interval:
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\nДемо остановлено пользователем.")
    finally:
        monitor.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Фоновый Supervisor M0: только read-only doctor и имитация."
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="только codex --help / --version")
    demo = sub.add_parser("demo", help="mock-наблюдатель без настоящего Codex")
    demo.add_argument("--ticks", type=int, default=8,
                      help="число виртуальных шагов; 0 = до Ctrl+C")
    demo.add_argument("--interval", type=float, default=0.3,
                      help="задержка между mock-шагами в секундах")
    args = parser.parse_args()
    if args.action == "doctor":
        _doctor()
    else:
        if args.ticks < 0 or not 0 <= args.interval <= 60:
            parser.error("ticks >= 0 и 0 <= interval <= 60")
        _demo(args.ticks, args.interval)


if __name__ == "__main__":
    main()
