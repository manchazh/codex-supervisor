"""Постоянный read-only монитор лимитов Codex: M1, без модельных ходов.

Каждый цикл запрашивает только account/rateLimits/read через отдельный
короткоживущий proxy. Ни turn/start, ни thread/resume отсутствуют.
Сигнал о времени сброса НЕ равен разрешению продолжить работу.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time

from .rpc import ProbeError, read_quota_once
from .summary import summary_lines


class WatchError(Exception):
    """Не удалось безопасно запустить или сохранить монитор."""


@dataclass(frozen=True)
class Window:
    used_percent: float | None
    reset_at: float | None
    duration_mins: float | None


def _number(value, *, max_value):
    if (isinstance(value, bool) or
        not isinstance(value, (int, float)) or
        not math.isfinite(value) or not 0 <= value <= max_value):
        return None
    return float(value)


def _window(data):
    if not isinstance(data, dict):
        return Window(None, None, None)
    return Window(
        used_percent=_number(data.get("usedPercent"), max_value=100),
        reset_at=_number(data.get("resetsAt"), max_value=4_102_444_800),
        duration_mins=_number(data.get("windowDurationMins"), max_value=525_600),
    )


def extract(result):
    """Из ответа оставляет только безопасные числовые метаданные."""
    if not isinstance(result, dict) or not isinstance(result.get("rateLimits"), dict):
        raise WatchError("Сервер ответил, но структура квот не распознана.")
    limits = result["rateLimits"]
    windows = {"primary": _window(limits.get("primary")),
               "secondary": _window(limits.get("secondary"))}
    if all(w == Window(None, None, None) for w in windows.values()):
        raise WatchError("Сервер не вернул распознаваемые данные ни об одном окне.")
    return windows


def _as_dict(windows):
    return {k: {
        "used_percent": v.used_percent,
        "reset_at": v.reset_at,
        "duration_mins": v.duration_mins,
    } for k, v in windows.items()}


def local_directory():
    override = os.getenv("CODEX_SUPERVISOR_WATCH_HOME")
    if override:
        return Path(override).expanduser()
    base = os.getenv("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "CodexSupervisor" / "quota"


class WatchState:
    """Только числовая сводка; одна запись с атомарной заменой."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.path = self.dir / "last-seen.json"

    def read(self):
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or not isinstance(value.get("windows"), dict):
                raise ValueError("bad schema")
            result = {}
            for key in ("primary", "secondary"):
                win = value["windows"].get(key)
                if not isinstance(win, dict):
                    raise ValueError("missing window")
                result[key] = Window(
                    _number(win.get("used_percent"), max_value=100),
                    _number(win.get("reset_at"), max_value=4_102_444_800),
                    _number(win.get("duration_mins"), max_value=525_600),
                )
            return result
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise WatchError(
                "Файл числового состояния повреждён; сохранён без перезаписи. "
                "Исправьте или переместите его вручную."
            ) from exc

    def save(self, windows):
        self.dir.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.dir,
                prefix=".quota-", suffix=".tmp", delete=False
            ) as stream:
                temp = Path(stream.name)
                json.dump({"version": 1, "windows": _as_dict(windows)},
                          stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            raise WatchError("Невозможно сохранить сводку квот.") from exc
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass


class WatchLock:
    """Эксклюзивная OS-блокировка в этой папке, с освобождением при crash."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.handle = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        h = (self.directory / "watch.lock").open("a+b")
        try:
            h.seek(0, os.SEEK_END)
            if h.tell() == 0:
                h.write(b"0")
                h.flush()
            h.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle = h
            return self
        except OSError as exc:
            h.close()
            raise WatchError("Другой монитор уже работает с этим каталогом.") from exc

    def __exit__(self, exc_type, exc, tb):
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class QuotaWatcher:
    def __init__(self, *, interval=300.0, fetch=read_quota_once,
                 store=None, write=print, sleeper=None, clock=time.time):
        if (isinstance(interval, bool) or not isinstance(interval, (float, int))
            or not math.isfinite(interval) or not 60 <= interval <= 3600):
            raise ValueError("Интервал проверки должен быть 60–3600 секунд.")
        self.interval = float(interval)
        self.fetch = fetch
        self.store = store or WatchState(local_directory())
        self.write = write
        self.sleeper = sleeper or threading.Event().wait
        self.clock = clock
        self.previous = None
        self.failures = 0
        self._observed_reset = set()

    def check(self):
        """Один цикл, не вызывает модель и не принимает решение о resume."""
        try:
            response = self.fetch()
            current = extract(response)
            old = self.previous
            if old is None:
                old = self.store.read()
            if old != current:
                self.write("Состояние квот изменилось / первый снимок:")
                for line in summary_lines(response):
                    self.write(line)
                self.store.save(current)
            if old is not None:
                now = self.clock()
                for key, name in (("primary", "основного"), ("secondary", "дополнительного")):
                    w = current[key]
                    before = old[key]
                    if (w.reset_at is not None and before.reset_at == w.reset_at and
                        w.reset_at <= now and (key, w.reset_at) not in self._observed_reset):
                        self._observed_reset.add((key, w.reset_at))
                        self.write(
                            f"Время сброса {name} окна наступило. "
                            "Это НЕ подтверждение доступности модели и НЕ команда продолжить."
                        )
            self.previous = current
            self.failures = 0
            return self.interval
        except (ProbeError, WatchError, OSError) as exc:
            # Ошибку от сервера уже скрывает ProbeError, не печатаем содержимое RPC.
            self.failures += 1
            self.write(f"Проверка лимитов не удалась: {exc}")
            return min(900.0, max(60.0, self.interval) * 2 ** min(self.failures - 1, 4))

    def run(self, *, max_checks=None, stop_event=None):
        if max_checks is not None and max_checks < 1:
            raise ValueError("max_checks должен быть >= 1")
        count = 0
        self.write(
            "Фоновый Supervisor: только чтение квот. "
            "Ctrl+C — остановить. Автопродолжение НЕ РЕАЛИЗОВАНО."
        )
        while stop_event is None or not stop_event.is_set():
            delay = self.check()
            count += 1
            if max_checks is not None and count >= max_checks:
                return
            if stop_event is not None:
                if stop_event.wait(delay):
                    return
            else:
                self.sleeper(delay)
