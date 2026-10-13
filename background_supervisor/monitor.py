"""Fail-closed ядро наблюдения за заданием и квотой Codex.

Модуль не вызывает Codex, не отправляет turn, не открывает сеть и не пишет Git.
В M0 реальный источник событий отсутствует: только тестовые/фиктивные события.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Mode(str, Enum):
    WORKING = "работает"
    WAITING_QUOTA = "ожидает квоту"
    READY = "готов к проверяемому продолжению (только макет)"
    WAITING_PERSON = "нужно решение человека"
    NEEDS_REVIEW = "неизвестный исход, нужна проверка"
    COMPLETED = "задача завершена"
    STOPPED = "остановлено"


@dataclass(frozen=True)
class Binding:
    project_id: str
    branch: str
    thread_id: str
    account_id: str
    limit_id: str
    task_id: str

    def __post_init__(self):
        values = (self.project_id, self.branch, self.thread_id,
                  self.account_id, self.limit_id, self.task_id)
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("Для Supervisor обязательна точная привязка задачи и thread.")


@dataclass(frozen=True)
class QuotaSnapshot:
    account_id: Optional[str]
    limit_id: Optional[str]
    ordinary_usage_allowed: Optional[bool]
    # Абсолютные секунды Unix; сброс всех блокирующих окон, не только первого.
    blocking_resets_at: tuple[float, ...]
    observed_at: float
    complete: bool

    def __post_init__(self):
        if isinstance(self.blocking_resets_at, list):
            raise ValueError("blocking_resets_at должен быть неизменяемым tuple.")


class BackgroundMonitor:
    """Не выполняет ни одного действия с моделью: выдаёт только готовность."""

    def __init__(self, binding: Binding, *, guard_seconds=30.0, max_snapshot_age=60.0):
        if guard_seconds < 0 or max_snapshot_age <= 0:
            raise ValueError("Неверная конфигурация защитных интервалов.")
        self.binding = binding
        self.guard_seconds = guard_seconds
        self.max_snapshot_age = max_snapshot_age
        self.mode = Mode.WORKING
        self.last_reason = "наблюдатель запущен (макет)"
        self.quota_snapshot: Optional[QuotaSnapshot] = None
        self.blocked_turn_id: Optional[str] = None
        self.resume_intent_issued = False

    def quota_error(self, *, code: str, account_id: str,
                    thread_id: str, turn_id: str,
                    server_will_retry: bool, failed_confirmed: bool):
        """Принимается только явная подписочная ошибка от нужной сессии."""
        if self.mode != Mode.WORKING:
            return False
        if account_id != self.binding.account_id or thread_id != self.binding.thread_id:
            self.mode = Mode.NEEDS_REVIEW
            self.last_reason = "событие относится к другой сессии или аккаунту"
            return False
        if code != "usageLimitExceeded":
            self.mode = Mode.WAITING_PERSON
            self.last_reason = "ошибка не подтверждает восстановимую подписочную квоту"
            return False
        if server_will_retry:
            self.mode = Mode.WAITING_PERSON
            self.last_reason = "Codex сам планирует повтор; второй запрещён"
            return False
        if not failed_confirmed or not isinstance(turn_id, str) or not turn_id:
            self.mode = Mode.NEEDS_REVIEW
            self.last_reason = "исход последнего хода неизвестен"
            return False
        self.blocked_turn_id = turn_id
        self.mode = Mode.WAITING_QUOTA
        self.last_reason = "квота подтверждена; ждём свежий полный snapshot"
        return True

    def observe(self, snapshot: QuotaSnapshot, *, now: float):
        """Результат пассивного чтения квоты: НЕ вызывает turn/start."""
        if self.mode != Mode.WAITING_QUOTA:
            return False
        if snapshot.account_id != self.binding.account_id or snapshot.limit_id != self.binding.limit_id:
            self.mode = Mode.NEEDS_REVIEW
            self.last_reason = "неверный аккаунт или limit bucket"
            return False
        if not snapshot.complete or not snapshot.blocking_resets_at:
            self.mode = Mode.WAITING_PERSON
            self.last_reason = "недостаточно полного серверного снимка лимитов"
            return False
        if (not all(isinstance(t, (int, float)) and not isinstance(t, bool)
                    and t > 0 for t in snapshot.blocking_resets_at)):
            self.mode = Mode.WAITING_PERSON
            self.last_reason = "недостоверное абсолютное время reset"
            return False
        reset = max(snapshot.blocking_resets_at)
        if reset > now:
            self.quota_snapshot = snapshot
            self.last_reason = "ожидаем завершения всех блокирующих окон"
            return False
        if snapshot.ordinary_usage_allowed is not True:
            self.quota_snapshot = snapshot
            self.last_reason = "сервер пока не разрешил использование"
            return False
        if now < reset + self.guard_seconds:
            self.quota_snapshot = snapshot
            self.last_reason = "защитная пауза после reset"
            return False
        if snapshot.observed_at < reset or not 0 <= now - snapshot.observed_at <= self.max_snapshot_age:
            self.quota_snapshot = snapshot
            self.last_reason = "нужен свежий серверный snapshot после reset"
            return False
        self.quota_snapshot = snapshot
        self.mode = Mode.READY
        self.last_reason = "условия квоты выполнены; НИКАКОЙ новый ход не отправлен"
        return True

    def resume_intent(self):
        """Одноразовый сигнал для будущего адаптера, в M0 только демонстрация."""
        if self.mode != Mode.READY or self.resume_intent_issued:
            return False
        self.resume_intent_issued = True
        return True

    def pause(self):
        if self.mode not in (Mode.STOPPED, Mode.COMPLETED):
            self.mode = Mode.WAITING_PERSON
            self.last_reason = "пользователь поставил паузу; автоход запрещён"

    def stop(self):
        self.mode = Mode.STOPPED
        self.resume_intent_issued = True
        self.last_reason = "пользователь остановил сессию"

    def complete(self):
        if self.mode != Mode.STOPPED:
            self.mode = Mode.COMPLETED
            self.resume_intent_issued = True
            self.last_reason = "задача выполнена; новых ходов быть не должно"

    def unknown_outcome(self):
        if self.mode not in (Mode.COMPLETED, Mode.STOPPED):
            self.mode = Mode.NEEDS_REVIEW
            self.resume_intent_issued = True
            self.last_reason = "действие могло уже выполниться; запрет повторов"
