"""Состояния демо-чата; никакой связи с Codex CLI, сетью или Git."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .storage import StateError

STATUSES = {
    "idle": "Готов",
    "running": "Обрабатывается (демо)",
    "paused": "Пауза",
    "stopped": "Остановлено",
    "needs_review": "Нужна проверка",
    "completed": "Завершено",
    "await_user": "Ожидает решения",
    "await_limit": "Ожидает лимита (демо)",
    "error": "Ошибка",
}


class ActionError(Exception):
    """Запрошенное действие не допускается текущим состоянием."""


def _id():
    return str(uuid.uuid4())


def _time():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session(project_id):
    return {
        "id": _id(),
        "project_id": project_id,
        "status": "idle",
        "messages": [],
        "pending_turn": None,
        "pause_after": False,
    }


def _initial():
    project_id = _id()
    session = _session(project_id)
    return {
        "version": 1,
        "active_project_id": project_id,
        "active_session_id": session["id"],
        "projects": [{"id": project_id, "name": "Демо-проект"}],
        "sessions": [session],
    }


def _check(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise StateError("Неизвестная версия состояния: обновление вручную, без сброса.")
    projects = data.get("projects")
    sessions = data.get("sessions")
    if not isinstance(projects, list) or not isinstance(sessions, list):
        raise StateError("Неправильная структура проектов или сессий.")
    if not projects or not sessions:
        raise StateError("Отсутствует проект или сессия.")
    if any(not isinstance(p, dict) or
           not isinstance(p.get("id"), str) or
           not isinstance(p.get("name"), str) for p in projects):
        raise StateError("Некорректные метаданные проекта.")
    project_ids = [p["id"] for p in projects]
    if len(set(project_ids)) != len(project_ids):
        raise StateError("Обнаружены повторяющиеся ID проектов.")
    session_ids = []
    for session in sessions:
        if not isinstance(session, dict):
            raise StateError("Некорректная сессия.")
        session_ids.append(session.get("id"))
        if (not isinstance(session.get("id"), str) or
            session.get("project_id") not in project_ids or
            session.get("status") not in STATUSES or
            not isinstance(session.get("messages"), list) or
            not isinstance(session.get("pause_after"), bool)):
            raise StateError("Повреждены метаданные сессии.")
        turn = session.get("pending_turn")
        if turn is not None and (not isinstance(turn, dict) or
                                 not isinstance(turn.get("id"), str)):
            raise StateError("Некорректный ожидающий ход.")
        for msg in session["messages"]:
            if (not isinstance(msg, dict) or
                msg.get("role") not in ("user", "assistant", "system") or
                not isinstance(msg.get("text"), str) or
                not isinstance(msg.get("time"), str)):
                raise StateError("Некорректная история сообщений.")
    if len(set(session_ids)) != len(session_ids):
        raise StateError("Обнаружены повторяющиеся ID сессий.")
    if (data.get("active_project_id") not in project_ids or
        data.get("active_session_id") not in session_ids):
        raise StateError("Не найдена активная сессия или проект.")
    current = next(s for s in sessions if s["id"] == data["active_session_id"])
    if current["project_id"] != data["active_project_id"]:
        raise StateError("Активный проект не соответствует выбранной сессии.")


class Supervisor:
    """Фиктивное backend-состояние с записью каждого подтверждённого перехода."""

    def __init__(self, store):
        self.store = store
        self.data = store.load()
        if self.data is None:
            self.data = _initial()
            self._save()
        else:
            _check(self.data)
            # После сбоя нельзя считать неизвестный ход успешно завершённым.
            changed = False
            for s in self.data["sessions"]:
                if s["status"] == "running":
                    s["status"] = "needs_review"
                    s["pause_after"] = False
                    changed = True
            if changed:
                self._save()

    def _save(self):
        _check(self.data)
        self.store.save(self.data)

    def active(self):
        return next(s for s in self.data["sessions"]
                    if s["id"] == self.data["active_session_id"])

    def projects(self):
        return list(self.data["projects"])

    def sessions(self, project_id=None):
        project_id = project_id or self.data["active_project_id"]
        return [s for s in self.data["sessions"] if s["project_id"] == project_id]

    def add_project(self, name):
        name = name.strip()
        if not name or len(name) > 90:
            raise ActionError("Название проекта должно содержать от 1 до 90 символов.")
        project_id = _id()
        s = _session(project_id)
        self.data["projects"].append({"id": project_id, "name": name})
        self.data["sessions"].append(s)
        self.data["active_project_id"] = project_id
        self.data["active_session_id"] = s["id"]
        self._save()
        return project_id

    def select_project(self, project_id):
        if project_id not in {p["id"] for p in self.projects()}:
            raise ActionError("Проект не найден.")
        self.data["active_project_id"] = project_id
        self.data["active_session_id"] = self.sessions(project_id)[-1]["id"]
        self._save()

    def new_session(self):
        s = _session(self.data["active_project_id"])
        self.data["sessions"].append(s)
        self.data["active_session_id"] = s["id"]
        self._save()
        return s["id"]

    def select_session(self, session_id):
        s = next((s for s in self.sessions() if s["id"] == session_id), None)
        if s is None:
            raise ActionError("Эта сессия не принадлежит текущему проекту.")
        self.data["active_session_id"] = session_id
        self._save()

    @staticmethod
    def _message(s, role, text):
        s["messages"].append({"role": role, "text": text, "time": _time()})

    def submit(self, text):
        s = self.active()
        if s["status"] != "idle":
            raise ActionError("Нельзя отправить сообщение: сессия не готова.")
        text = text.strip()
        if not text:
            raise ActionError("Введите текст тестового сообщения.")
        if len(text) > 20000:
            raise ActionError("В демо допускается не более 20 000 символов.")
        turn_id = _id()
        self._message(s, "user", text)
        s["pending_turn"] = {"id": turn_id, "text": text}
        s["status"] = "running"
        self._save()
        return s["id"], turn_id

    def finish_mock(self, session_id, turn_id, answer):
        s = next((s for s in self.data["sessions"] if s["id"] == session_id), None)
        if (s is None or s["status"] != "running" or
            not s["pending_turn"] or s["pending_turn"]["id"] != turn_id):
            return False
        self._message(s, "assistant", answer)
        s["pending_turn"] = None
        s["status"] = "paused" if s["pause_after"] else "idle"
        s["pause_after"] = False
        self._save()
        return True

    def pause(self):
        s = self.active()
        if s["status"] == "running":
            s["pause_after"] = True
        elif s["status"] == "idle":
            s["status"] = "paused"
        else:
            raise ActionError("Пауза доступна только для активной сессии.")
        self._save()

    def resume(self):
        s = self.active()
        if s["status"] == "needs_review":
            # Ручная проверка означает отказ от автоматического повтора pending.
            s["pending_turn"] = None
            self._message(s, "system",
                          "Неизвестный исход прежнего демо-хода. "
                          "Повтор автоматически не выполнялся.")
        elif s["status"] not in ("paused", "await_user", "await_limit", "error"):
            raise ActionError("Сейчас нечего возобновлять.")
        s["status"] = "idle"
        s["pause_after"] = False
        self._save()

    def stop(self):
        s = self.active()
        if s["status"] in ("stopped", "completed"):
            return
        s["status"] = "stopped"
        s["pending_turn"] = None
        s["pause_after"] = False
        self._save()

    def complete(self):
        s = self.active()
        if s["status"] not in ("idle", "paused"):
            raise ActionError("Завершить можно только свободную/приостановленную сессию.")
        s["status"] = "completed"
        self._save()

    def simulate_wait(self):
        s = self.active()
        if s["status"] != "idle":
            raise ActionError("Имитация ожидания доступна только из состояния «Готов».")
        s["status"] = "await_limit"
        self._save()
