"""Однократный read-only RPC через WebSocket поверх stdio-proxy Codex.

Codex app-server control socket требует RFC 6455 (WebSocket), даже когда
используется Unix domain socket. codex app-server proxy переносит сырые
байты stdio, он НЕ превращает JSONL в WebSocket-кадры.

Разрешены только initialize, initialized, account/rateLimits/read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import queue
import struct
import subprocess
import threading
import time


class ProbeError(Exception):
    """Безопасная ошибка: приватный ответ сервера никогда не выводится."""


MAX_PAYLOAD = 262_144
MAX_NOTIFICATIONS = 100
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
METHODS = frozenset(("initialize", "initialized", "account/rateLimits/read"))
UPGRADED = object()


def _request(method: str, *, id=None, params=None):
    if method not in METHODS:
        raise ProbeError("Метод RPC не входит в белый список.")
    data = {"method": method}
    if id is not None:
        data["id"] = id
    if params is not None:
        data["params"] = params
    return data


def _read_exact(pipe, n):
    if n > MAX_PAYLOAD or n < 0:
        raise ProbeError("Размер входящего сообщения превышает предел.")
    chunks = []
    while n > 0:
        chunk = pipe.read(n)
        if not chunk:
            raise ProbeError("Сервер закрыл соединение.")
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


def _handshake(pipe, nonce):
    header = bytearray()
    while not header.endswith(b"\r\n\r\n"):
        if len(header) >= 8192:
            raise ProbeError("HTTP/WebSocket заголовок слишком длинный.")
        header.extend(_read_exact(pipe, 1))
    try:
        lines = header.decode("iso-8859-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101 "):
            raise ProbeError("Сервер не принял WebSocket upgrade.")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        accepted = base64.b64encode(hashlib.sha1(
            (nonce + WS_GUID).encode("ascii")
        ).digest()).decode("ascii")
        if (headers.get("sec-websocket-accept") != accepted or
            headers.get("upgrade", "").lower() != "websocket" or
            "upgrade" not in headers.get("connection", "").lower()):
            raise ProbeError("Проверка подлинности WebSocket handshake не пройдена.")
    except UnicodeError as exc:
        raise ProbeError("Неправильные HTTP заголовки handshake.") from exc


def _send_frame(pipe, opcode, payload, write_lock):
    if len(payload) > MAX_PAYLOAD:
        raise ProbeError("Слишком большой исходящий RPC.")
    mask = os.urandom(4)
    size = len(payload)
    if size < 126:
        prefix = bytes((0x80 | opcode, 0x80 | size))
    else:
        prefix = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", size)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    with write_lock:
        pipe.write(prefix + mask + masked)
        pipe.flush()


def _read_frame(pipe):
    first, second = _read_exact(pipe, 2)
    if (first & 0x70) or not (first & 0x80):
        raise ProbeError("Неподдерживаемый формат WebSocket фрейма.")
    opcode = first & 0x0F
    if second & 0x80:
        # Сервер, в отличие от клиента, НЕ должен маскировать кадры.
        raise ProbeError("Некорректно замаскированный серверный фрейм.")
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(pipe, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(pipe, 8))[0]
    if length > MAX_PAYLOAD:
        raise ProbeError("Серверный WebSocket фрейм слишком большой.")
    return opcode, _read_exact(pipe, length)


def _reader(pipe, to_server, inbox, nonce, write_lock):
    try:
        _handshake(pipe, nonce)
        inbox.put_nowait(UPGRADED)
        while True:
            opcode, payload = _read_frame(pipe)
            if opcode == 0x08:
                raise ProbeError("Сервер закрыл WebSocket соединение.")
            if opcode == 0x09:  # ping
                _send_frame(to_server, 0x0A, payload, write_lock)
                continue
            if opcode == 0x0A:  # pong
                continue
            if opcode != 0x01:
                raise ProbeError("Неожиданный тип серверного кадра.")
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise ProbeError("Не удалось разобрать JSON сервера.") from exc
            if isinstance(message, dict):
                inbox.put_nowait(message)
    except (OSError, ProbeError, queue.Full) as exc:
        try:
            inbox.put_nowait(
                exc if isinstance(exc, ProbeError)
                else ProbeError("Сбой соединения с WebSocket proxy.")
            )
        except queue.Full:
            pass
    finally:
        try:
            inbox.put_nowait(None)
        except queue.Full:
            pass


def _next(inbox, deadline, *, stage):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProbeError(f"Таймаут этапа: {stage}.")
    try:
        message = inbox.get(timeout=remaining)
    except queue.Empty as exc:
        raise ProbeError(f"Таймаут этапа: {stage}.") from exc
    if isinstance(message, ProbeError):
        raise message
    if message is None:
        raise ProbeError(f"Соединение закрыто на этапе: {stage}.")
    return message


def _await_response(inbox, expected, deadline):
    for _ in range(MAX_NOTIFICATIONS):
        msg = _next(inbox, deadline, stage=f"RPC {expected}")
        if not isinstance(msg, dict):
            raise ProbeError("Неожиданный ответ сервера.")
        if msg.get("id") == expected:
            if "error" in msg:
                raise ProbeError("Сервер отклонил RPC; подробности скрыты.")
            result = msg.get("result")
            if not isinstance(result, dict):
                raise ProbeError("Ответ RPC без ожидаемого result.")
            return result
        # Чужие уведомления и server requests только игнорируем.
    raise ProbeError("Слишком много уведомлений от сервера.")


def read_quota_once(*, timeout=12.0, command=None):
    """Read-only quota, ни одного turn/start. command — только для offline CI.

    Использует собственного дочернего proxy, основной daemon не останавливает.
    """
    if (isinstance(timeout, bool) or
        not isinstance(timeout, (int, float)) or
        not math.isfinite(timeout) or not 1 <= timeout <= 60):
        raise ValueError("timeout должен быть от 1 до 60 секунд.")
    argv = command if command is not None else ["codex", "app-server", "proxy"]
    process = None
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, shell=False,
        )
        deadline = time.monotonic() + timeout
        inbox = queue.Queue(maxsize=MAX_NOTIFICATIONS + 5)
        write_lock = threading.Lock()
        nonce = base64.b64encode(os.urandom(16)).decode("ascii")
        reader = threading.Thread(
            target=_reader,
            args=(process.stdout, process.stdin, inbox, nonce, write_lock),
            daemon=True, name="quota-readonly-websocket-reader",
        )
        reader.start()
        # Transport handshake, НЕ модельный запрос.
        request = (
            "GET /rpc HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {nonce}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        with write_lock:
            process.stdin.write(request.encode("ascii"))
            process.stdin.flush()
        if _next(inbox, deadline, stage="WebSocket handshake") is not UPGRADED:
            raise ProbeError("От сервера не получено подтверждение WebSocket.")

        def send(method, **kwargs):
            data = _request(method, **kwargs)
            payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
            _send_frame(process.stdin, 0x01, payload, write_lock)

        send("initialize", id=1, params={
            "clientInfo": {
                "name": "codex_supervisor_readonly_probe",
                "title": "Codex Supervisor Quota Check",
                "version": "0.1.0",
            },
        })
        _await_response(inbox, 1, deadline)
        send("initialized")
        send("account/rateLimits/read", id=2)
        return _await_response(inbox, 2, deadline)
    except (FileNotFoundError, PermissionError) as exc:
        raise ProbeError("Не удалось найти или запустить codex app-server proxy.") from exc
    except (BrokenPipeError, OSError) as exc:
        raise ProbeError("Связь с дочерним proxy завершилась с ошибкой.") from exc
    finally:
        if process is not None:
            # Закрывает именно наш дочерний proxy, не server daemon.
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            try:
                process.stdout.close()
            except OSError:
                pass
