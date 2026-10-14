"""Выводит ТОЛЬКО статистику квот, исключая email/accountID/threads."""

from __future__ import annotations

from datetime import datetime, timezone
import math


def _number(value, low=0, high=100):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not low <= value <= high:
        return None
    return float(value)


def _timestamp(value):
    number = _number(value, 0, 4_102_444_800)
    if number is None:
        return "неизвестно"
    try:
        local = datetime.fromtimestamp(number, tz=timezone.utc).astimezone()
        return local.strftime("%d.%m.%Y %H:%M %Z")
    except (OverflowError, ValueError, OSError):
        return "неизвестно"


def summary_lines(result):
    if not isinstance(result, dict):
        return ["Неверная структура ответа (данные скрыты)."]
    quota = result.get("rateLimits")
    if not isinstance(quota, dict):
        return [
            "Ответ получен, но ожидаемый объект rateLimits отсутствует.",
            "Версия app-server может использовать другую схему; raw JSON НЕ выводим.",
        ]
    lines = []
    for name, key in (("Основное окно", "primary"), ("Дополнительное окно", "secondary")):
        window = quota.get(key)
        if window is None:
            lines.append(f"{name}: нет данных")
            continue
        if not isinstance(window, dict):
            lines.append(f"{name}: неизвестный формат")
            continue
        used = _number(window.get("usedPercent"))
        remaining = None if used is None else 100.0 - used
        duration = _number(window.get("windowDurationMins"), 0, 525_600)
        resets = _timestamp(window.get("resetsAt"))
        def fmt(x):
            return "неизвестно" if x is None else f"{x:g}%"
        duration_str = ("неизвестно" if duration is None else f"{duration:g} мин.")
        lines.append(
            f"{name}: использовано {fmt(used)}, осталось {fmt(remaining)}, "
            f"окно {duration_str}, сброс {resets}"
        )
    if quota.get("rateLimitReachedType"):
        lines.append("Сервер сообщает об ограничении квоты (без сырых деталей).")
    lines.append("Проверка завершена. Модельные сообщения не отправлялись.")
    return lines
