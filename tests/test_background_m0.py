"""Безопасные offline-тесты фонового M0. Codex, сеть и токены не используются."""

import unittest

from background_supervisor.monitor import (
    BackgroundMonitor, Binding, Mode, QuotaSnapshot,
)
from background_supervisor.doctor import inspect_cli


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding("demo", "feature", "thread-1", "account-1", "codex", "issue-10")
        self.monitor = BackgroundMonitor(self.binding, guard_seconds=5, max_snapshot_age=10)
        self.now = 2000000000.0

    def limit(self, **overrides):
        data = {
            "code": "usageLimitExceeded", "account_id": "account-1",
            "thread_id": "thread-1", "turn_id": "turn-1",
            "server_will_retry": False, "failed_confirmed": True,
        }
        data.update(overrides)
        return self.monitor.quota_error(**data)

    def sample(self, now=None, **overrides):
        now = self.now if now is None else now
        data = {
            "account_id": "account-1", "limit_id": "codex",
            "ordinary_usage_allowed": True,
            "blocking_resets_at": (self.now - 10,),
            "observed_at": now, "complete": True,
        }
        data.update(overrides)
        return QuotaSnapshot(**data)

    def test_same_thread_limit_is_detected(self):
        self.assertTrue(self.limit())
        self.assertEqual(self.monitor.mode, Mode.WAITING_QUOTA)
        self.assertFalse(self.monitor.resume_intent())

    def test_ordinary_429_is_not_subscription_limit(self):
        self.assertFalse(self.limit(code="http429"))
        self.assertEqual(self.monitor.mode, Mode.WAITING_PERSON)
        self.assertFalse(self.monitor.resume_intent())

    def test_rate_limit_exceeded_is_not_subscription_limit(self):
        self.assertFalse(self.limit(code="rateLimitExceeded"))
        self.assertEqual(self.monitor.mode, Mode.WAITING_PERSON)

    def test_internal_retry_suppresses_external_retry(self):
        self.assertFalse(self.limit(server_will_retry=True))
        self.assertFalse(self.monitor.resume_intent())

    def test_unknown_turn_result_blocks_retry(self):
        self.assertFalse(self.limit(failed_confirmed=False))
        self.assertEqual(self.monitor.mode, Mode.NEEDS_REVIEW)

    def test_other_thread_or_account_blocks_resume(self):
        self.assertFalse(self.limit(thread_id="other"))
        self.assertEqual(self.monitor.mode, Mode.NEEDS_REVIEW)

    def test_incomplete_snapshot_blocks(self):
        self.limit()
        self.assertFalse(self.monitor.observe(self.sample(complete=False), now=self.now))
        self.assertEqual(self.monitor.mode, Mode.WAITING_PERSON)

    def test_missing_reset_blocks(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(blocking_resets_at=()), now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.WAITING_PERSON)

    def test_unknown_server_permission_never_means_recovered(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(ordinary_usage_allowed=None), now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.WAITING_QUOTA)

    def test_wrong_bucket_fails_closed(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(limit_id="other"), now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.NEEDS_REVIEW)

    def test_other_account_fails_closed(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(account_id="other"), now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.NEEDS_REVIEW)

    def test_only_last_blocking_window_counts(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(blocking_resets_at=(self.now - 100, self.now + 60)),
            now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.WAITING_QUOTA)

    def test_guard_period_blocks(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(blocking_resets_at=(self.now - 2,)), now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.WAITING_QUOTA)

    def test_stale_snapshot_blocks(self):
        self.limit()
        self.assertFalse(self.monitor.observe(
            self.sample(observed_at=self.now - 50), now=self.now
        ))
        self.assertEqual(self.monitor.mode, Mode.WAITING_QUOTA)

    def test_recovered_snapshot_issues_only_one_mock_intent(self):
        self.limit()
        self.assertTrue(self.monitor.observe(self.sample(), now=self.now))
        self.assertEqual(self.monitor.mode, Mode.READY)
        self.assertTrue(self.monitor.resume_intent())
        self.assertFalse(self.monitor.resume_intent())
        # Даже готовы — метод нигде не вызывает настоящую модель.
        self.assertFalse(self.monitor.observe(self.sample(), now=self.now))

    def test_pause_or_stop_prevents_resume(self):
        self.limit()
        self.monitor.pause()
        self.assertFalse(self.monitor.observe(self.sample(), now=self.now))
        self.assertFalse(self.monitor.resume_intent())
        self.monitor.stop()
        self.assertEqual(self.monitor.mode, Mode.STOPPED)

    def test_completed_is_terminal(self):
        self.limit()
        self.monitor.complete()
        self.assertEqual(self.monitor.mode, Mode.COMPLETED)
        self.assertFalse(self.monitor.observe(self.sample(), now=self.now))
        self.monitor.stop()
        self.assertEqual(self.monitor.mode, Mode.STOPPED)  # стоп пользователя сильнее

    def test_unknown_outcome_invalidates_readiness(self):
        self.limit()
        self.assertTrue(self.monitor.observe(self.sample(), now=self.now))
        self.monitor.unknown_outcome()
        self.assertEqual(self.monitor.mode, Mode.NEEDS_REVIEW)
        self.assertFalse(self.monitor.resume_intent())

    def test_invalid_binding_rejected(self):
        with self.assertRaises(ValueError):
            Binding("demo", "", "thread", "account", "codex", "task")


class DoctorTests(unittest.TestCase):
    def test_capabilities_are_read_only_text_checks(self):
        calls = []
        def fake_read(argv):
            calls.append(argv[1:])
            if argv[1:] == ["--version"]:
                return "codex-cli 0.162.1\n"
            if argv[1:] == ["--help"]:
                return "Usage: codex --remote --no-alt-screen app-server"
            if argv[1:] == ["resume", "--help"]:
                return "Usage: codex resume --remote"
            if argv[1:] == ["app-server", "--help"]:
                return "Usage: codex app-server"
            raise AssertionError("Неожиданная команда")
        result = inspect_cli(which=lambda key: "codex" if key == "codex" else None, read=fake_read)
        self.assertTrue(result["supports_remote_tui"])
        self.assertTrue(result["supports_remote_resume"])
        self.assertTrue(result["supports_app_server"])
        self.assertEqual(calls, [
            ["--version"], ["--help"], ["resume", "--help"], ["app-server", "--help"]
        ])

    def test_missing_codex_does_not_launch_processes(self):
        result = inspect_cli(which=lambda _: None, read=lambda _: self.fail("read called"))
        self.assertFalse(result["installed"])


if __name__ == "__main__":
    unittest.main()
