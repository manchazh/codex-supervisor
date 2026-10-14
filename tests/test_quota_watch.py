"""Тесты непрерывного quota watcher без запуска настоящего Codex."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quota_probe.rpc import ProbeError
from quota_probe.watch import (
    QuotaWatcher, WatchError, WatchLock, WatchState, extract,
)


def response(pct=20, weekly=40, reset=200, *, extra=None):
    data = {
        "rateLimits": {
            "primary": {
                "usedPercent": pct, "resetsAt": reset,
                "windowDurationMins": 300,
            },
            "secondary": {
                "usedPercent": weekly, "resetsAt": reset + 600,
                "windowDurationMins": 10080,
            },
        },
        "account": {
            "email": "SECRET@example.com", "accountId": "SECRET_ACCOUNT"
        },
    }
    if extra:
        data.update(extra)
    return data


class WatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.store = WatchState(self.directory)
        self.out = []

    def watcher(self, fetch, **opts):
        return QuotaWatcher(
            interval=60, fetch=fetch, store=self.store,
            write=self.out.append, clock=lambda: 100, **opts,
        )

    def test_first_observation_writes_redacted_metrics(self):
        calls = []
        def fetch():
            calls.append("account/rateLimits/read")
            return response()
        watcher = self.watcher(fetch)
        delay = watcher.check()
        self.assertEqual(delay, 60)
        self.assertEqual(calls, ["account/rateLimits/read"])
        self.assertEqual(self.store.read()["primary"].used_percent, 20)
        text = "\n".join(self.out)
        self.assertIn("80%", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("example.com", text)

    def test_unchanged_observations_do_not_spam_or_resave(self):
        watcher = self.watcher(lambda: response())
        watcher.check()
        self.out.clear()
        with patch.object(self.store, "save", side_effect=AssertionError("unneeded")):
            watcher.check()
        self.assertEqual(self.out, [])

    def test_only_changed_window_is_reported(self):
        samples = iter([response(20), response(21)])
        watcher = self.watcher(lambda: next(samples))
        watcher.check()
        self.out.clear()
        watcher.check()
        self.assertTrue(any("79%" in row for row in self.out))
        self.assertEqual(self.store.read()["primary"].used_percent, 21)

    def test_restart_uses_previous_state_without_forcing_second_log(self):
        self.watcher(lambda: response()).check()
        self.out.clear()
        restarted = self.watcher(lambda: response())
        restarted.check()
        self.assertEqual(self.out, [])

    def test_server_error_has_bounded_backoff_without_model_calls(self):
        count = [0]
        def fetch():
            count[0] += 1
            raise ProbeError("Нет связи с proxy.")
        watcher = self.watcher(fetch)
        intervals = [watcher.check() for _ in range(7)]
        self.assertEqual(intervals, [60, 120, 240, 480, 900, 900, 900])
        self.assertEqual(count[0], 7)
        self.assertEqual(watcher.previous, None)

    def test_recovery_resets_error_backoff(self):
        n = [0]
        def fetch():
            n[0] += 1
            if n[0] == 1:
                raise ProbeError("Нет связи.")
            return response()
        watcher = self.watcher(fetch)
        watcher.check()
        self.assertEqual(watcher.failures, 1)
        watcher.check()
        self.assertEqual(watcher.failures, 0)

    def test_after_reset_elapsed_not_false_permission(self):
        # Временная оценка НЕ даёт никакого turn/start или status=ready.
        old = response(reset=80)
        watcher = QuotaWatcher(
            interval=60, fetch=lambda: old, store=self.store,
            write=self.out.append, clock=lambda: 100,
        )
        watcher.check()  # first observation, no old snapshot
        self.out.clear()
        watcher.check()  # still same reset past
        self.assertEqual(
            sum("НЕ подтверждение доступности модели" in x for x in self.out), 1
        )
        watcher.check()
        self.assertEqual(
            sum("НЕ подтверждение доступности модели" in x for x in self.out), 1
        )

    def test_empty_quota_does_not_erase_persisted_state(self):
        good = self.watcher(lambda: response())
        good.check()
        before = self.store.path.read_bytes()
        fail = self.watcher(lambda: {"rateLimits": {} })
        fail.check()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertIn("не удалась", "\n".join(self.out))

    def test_state_is_only_numeric_no_private_content(self):
        self.watcher(lambda: response()).check()
        payload = self.store.path.read_text(encoding="utf-8")
        self.assertNotIn("SECRET", payload)
        self.assertNotIn("example.com", payload)
        self.assertEqual(json.loads(payload)["version"], 1)

    def test_corrupt_state_fails_closed_without_overwriting(self):
        self.store.path.write_text("{bad", encoding="utf-8")
        self.watcher(lambda: response()).check()
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "{bad")

    def test_atomic_write_retains_prior_state_on_failure(self):
        self.store.save(extract(response()))
        original = self.store.path.read_bytes()
        with patch("quota_probe.watch.os.replace", side_effect=OSError("crash")):
            with self.assertRaises(WatchError):
                self.store.save(extract(response(25)))
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob(".quota-*.tmp")), [])

    def test_single_instance_lock(self):
        with WatchLock(self.directory):
            with self.assertRaises(WatchError):
                with WatchLock(self.directory):
                    pass
        with WatchLock(self.directory):
            pass

    def test_run_sleep_not_busy_loop(self):
        delays = []
        snapshots = [response(20), response(20), response(40)]
        def fetch():
            return snapshots.pop(0)
        watcher = self.watcher(fetch, sleeper=delays.append)
        watcher.run(max_checks=3)
        self.assertEqual(delays, [60, 60])
        self.assertEqual(len(snapshots), 0)

    def test_invalid_interval(self):
        for bad in (0, 5, 59, 3601, float("nan"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                QuotaWatcher(interval=bad, fetch=lambda: response(),
                             store=self.store, write=self.out.append)

    def test_unknown_data_never_written_to_state(self):
        with self.assertRaises(WatchError):
            extract({"secret": "private"})
        with self.assertRaises(WatchError):
            extract({"rateLimits": {}})
        sample = response(pct=float("nan"), weekly=50)
        sample["rateLimits"]["primary"]["resetsAt"] = "private"
        windows = extract(sample)
        self.assertIsNone(windows["primary"].used_percent)
        self.assertIsNone(windows["primary"].reset_at)

    def test_partial_update_is_not_treated_as_full_snapshot(self):
        # Только read() с полной структурой; уведомления updates сами по себе
        # не выдаются за полное разрешение нового модельного хода.
        partial = {"rateLimits": {
            "primary": {"usedPercent": 20},
            "secondary": None,
        }}
        windows = extract(partial)
        self.assertIsNone(windows["primary"].reset_at)
        self.assertIsNone(windows["secondary"].used_percent)


if __name__ == "__main__":
    unittest.main()
