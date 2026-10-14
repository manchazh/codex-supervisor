"""Один безопасный read-only запрос квоты через codex app-server proxy.

Whitelist RPC: initialize, initialized, account/rateLimits/read.
Никаких thread/*, turn/*, токенов, истории и внешних слушателей.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time


class ProbeError(Exception):
    """Проверка не удалась; не печатаем исходные данные daemon."""


MAX_LINE = 262_144
MAX_NOTIFICATIONS = 100
REQUESTED_METHODS = frozenset({
    "initialize", "initialized", "account/rateLimits/read",
})


def _request(method: str, *, id=None, params=None):
    if method not in REQUESTED_METHODS:
        raise ProbeError("Метод не входит в безопасный белый список.")
    data = {"method": method}
    if id is not None:
        data["id"] = id
    if params is not None:
        data["params"] = params
    return data


def _reader(pipe, messages):
    try:
        while True:
            line = pipe.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                messages.put_nowait(ProbeError("Ответ RPC превышает допустимый размер."))
                return
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    messages.put_nowait(message)
            except (ValueError, TypeError):
                messages.put_nowait(ProbeError("Неправильный формат JSON ответа RPC."))
                return
            except queue.Full:
                return
    finally:
        try:
            messages.put_nowait(None)
        except queue.Full:
            pass


def _await_response(messages, req_id, *, deadline):
    skipped = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("Ответ от app-server не получен до таймаута.")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise ProbeError("Истёк таймаут ответа app-server.") from exc
        if isinstance(message, ProbeError):
            raise message
        if message is None:
            raise ProbeError("Соединение с proxy закрыто до получения ответа.")
        if message.get("id") == req_id:
            if "error" in message:
                # Не публикуем сырой текст серверной ошибки: там возможны приватные данные.
                raise ProbeError("app-server отклонил read-only RPC (подробности скрыты).")
            if not isinstance(message.get("result"), dict):
                raise ProbeError("Ответ RPC не содержит ожидаемый result.")
            return message["result"]
        # Игнорируем любые уведомления, не обрабатываем серверные approval requests.
        skipped += 1
        if skipped >= MAX_NOTIFICATIONS:
            raise ProbeError("Слишком много посторонних уведомлений от app-server.")


def read_quota_once(*, timeout=12.0, command=None):
    """Возвращает только read-only snapshot; никаких данных не пишет.

    command допускается ТОЛЬКО для офлайн unit tests, публичная CLI не даёт
    указать произвольную команду или socket path.
    """
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
        raise ValueError("timeout должен быть от 1 до 60 секунд.")
    argv = command if command is not None else ["codex", "app-server", "proxy"]
    proc = None
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", bufsize=1, shell=False,
        )
        incoming = queue.Queue(maxsize=MAX_NOTIFICATIONS + 4)
        reader = threading.Thread(
            target=_reader, args=(proc.stdout, incoming),
            daemon=True, name="read-only-rpc-reader",
        )
        reader.start()
        deadline = time.monotonic() + timeout

        def send(method, **kwargs):
            data = _request(method, **kwargs)
            proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        send("initialize", id=1, params={
            "clientInfo": {
                "name": "codex_supervisor_readonly_probe",
                "title": "Codex Supervisor Quota Check",
                "version": "0.1.0",
            },
        })
        _await_response(incoming, 1, deadline=deadline)
        send("initialized", params={})
        send("account/rateLimits/read", id=2)
        return _await_response(incoming, 2, deadline=deadline)
    except (FileNotFoundError, PermissionError) as exc:
        raise ProbeError("codex CLI недоступен для запуска proxy.") from exc
    except (BrokenPipeError, OSError) as exc:
        raise ProbeError("Связь с proxy завершилась с ошибкой.") from exc
    finally:
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # Завершаем ТОЛЬКО дочерний proxy, НИКОГДА не основной daemon.
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
            try:
                proc.stdout.close()
            except OSError:
                pass
