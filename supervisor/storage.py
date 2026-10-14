"""Атомарное локальное состояние и одиночный экземпляр приложения."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


class StateError(Exception):
    """Состояние невозможно безопасно загрузить или сохранить."""


class AlreadyRunning(StateError):
    """Другой экземпляр использует ту же базу состояния."""


def data_directory() -> Path:
    override = os.environ.get("CODEX_SUPERVISOR_HOME")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".local" / "share") / "CodexSupervisor"


class InstanceLock:
    """Держим kernel lock на 1 байт до закрытия приложения."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.handle = None

    def acquire(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = (self.directory / "instance.lock").open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise AlreadyRunning("Supervisor уже запущен с этим каталогом данных.") from exc
        self.handle = handle
        return self

    def close(self):
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

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.close()


class StateStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "state.json"

    def load(self):
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, UnicodeError) as exc:
            raise StateError(
                "Файл состояния повреждён или недоступен. Не удаляйте его: "
                "проверьте каталог данных и восстановите копию."
            ) from exc
        if not isinstance(data, dict):
            raise StateError("Неверный формат состояния. Автоматического сброса нет.")
        return data

    def save(self, data):
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=".state-", suffix=".tmp",
                dir=self.directory, delete=False,
            ) as handle:
                temp = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except (OSError, ValueError, TypeError) as exc:
            raise StateError("Не удалось сохранить состояние. Старый файл не удалён.") from exc
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
