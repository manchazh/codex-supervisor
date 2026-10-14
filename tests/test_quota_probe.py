"""Offline tests: fake RPC process, NO Codex, NO models, NO network."""

import sys
import unittest

from quota_probe.rpc import ProbeError, _request, read_quota_once
from quota_probe.summary import summary_lines


FAKE_RPC = r"""
import json, sys
seen = []
for line in sys.stdin:
    m = json.loads(line)
    seen.append(m["method"])
    if m["method"] == "initialize":
        print(json.dumps({"id": m["id"], "result": {"userAgent": "offline-fake"}}), flush=True)
    elif m["method"] == "initialized":
        print(json.dumps({"method": "account/updated", "params": {"email": "SECRET@EXAMPLE.COM"}}), flush=True)
    elif m["method"] == "account/rateLimits/read":
        print(json.dumps({
            "id": m["id"], "result": {
                "seen": seen,
                "rateLimits": {
                    "primary": {"usedPercent": 63, "windowDurationMins": 300,
                                "resetsAt": 1780000000},
                    "secondary": {"usedPercent": 34, "windowDurationMins": 10080,
                                  "resetsAt": 1780100000}
                },
                "accountEmail": "SECRET@EXAMPLE.COM",
                "threadId": "SECRET-SESSION-UUID",
            }
        }), flush=True)
        break
"""


def fake_command(script=FAKE_RPC):
    return [sys.executable, "-u", "-c", script]


class RpcTests(unittest.TestCase):
    def test_single_read_only_request_and_handshake(self):
        result = read_quota_once(command=fake_command(), timeout=5)
        self.assertEqual(
            result["seen"],
            ["initialize", "initialized", "account/rateLimits/read"],
        )
        text = "\n".join(summary_lines(result))
        self.assertIn("37%", text)
        self.assertIn("66%", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("EXAMPLE", text)

    def test_rejects_any_method_outside_allowlist(self):
        for method in ("turn/start", "thread/resume", "account/login/start",
                       "account/rateLimitResetCredit/consume",
                       "thread/interrupt", "account/logout"):
            with self.subTest(method=method):
                with self.assertRaises(ProbeError):
                    _request(method)

    def test_child_disconnect_becomes_error(self):
        with self.assertRaises(ProbeError):
            read_quota_once(command=fake_command("import sys; sys.exit(0)"),
                            timeout=2)

    def test_protocol_error_is_redacted(self):
        script = r"""
import sys, json
for line in sys.stdin:
    obj=json.loads(line)
    if obj.get("method") == "initialize":
        print(json.dumps({"id":obj["id"],"error":{"message":"SECRET token"}}),flush=True)
        break
"""
        with self.assertRaises(ProbeError) as cm:
            read_quota_once(command=fake_command(script), timeout=2)
        self.assertNotIn("SECRET", str(cm.exception))

    def test_invalid_timeout(self):
        for value in (0, -10, 1000, "foo", None):
            with self.assertRaises(ValueError):
                read_quota_once(command=fake_command(), timeout=value)


class SummaryTests(unittest.TestCase):
    def test_missing_rate_limits_does_not_dump_response(self):
        lines = summary_lines({
            "account": {"email": "private@example.com"},
            "raw_token": "SECRET",
        })
        result = " ".join(lines)
        self.assertNotIn("private@example.com", result)
        self.assertNotIn("SECRET", result)

    def test_bad_percent_and_timestamp_not_printed(self):
        lines = summary_lines({"rateLimits": {
            "primary": {"usedPercent": float("nan"),
                        "resetsAt": "UNTRUSTED SECRET"},
            "secondary": {"usedPercent": 900,
                          "resetsAt": 10**40},
        }})
        result = " ".join(lines)
        self.assertIn("неизвестно", result)
        self.assertNotIn("UNTRUSTED SECRET", result)
        self.assertNotIn("900%", result)

    def test_null_window(self):
        lines = summary_lines({"rateLimits": {"primary": None, "secondary": None}})
        self.assertEqual(len(lines), 3)
        self.assertIn("нет данных", lines[0])


if __name__ == "__main__":
    unittest.main()
