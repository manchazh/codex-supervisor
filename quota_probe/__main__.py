"""python -m quota_probe: одноразовое read-only RPC получение процентов квот."""

import argparse
import sys

from .rpc import ProbeError, read_quota_once
from .summary import summary_lines


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Один read-only account/rateLimits/read через существующий Codex daemon."
    )
    parser.add_argument("--timeout", type=float, default=12.0,
                        help="секунд на handshake и read, максимум 60")
    args = parser.parse_args()
    print("Codex Supervisor: однократное чтение квот (без модели и turn/start).",
          flush=True)
    try:
        result = read_quota_once(timeout=args.timeout)
        for line in summary_lines(result):
            print(line)
    except (ProbeError, ValueError) as exc:
        print(f"Проверка не завершена: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
