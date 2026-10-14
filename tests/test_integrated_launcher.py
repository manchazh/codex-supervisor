"""M2 integration tests. Real Codex and real quota server NEVER run."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from launcher.__main__ import main as launcher_main
from launcher.managed import LaunchError, codex_argv, project_directory, run_managed
from quota_probe.watch import QuotaWatcher, WatchError


class FakeCodexProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.wait_called = 0

    def wait(self):
        self.wait_called += 1
        return self.returncode


class FakeWatcher:
    started = None
    finished = None

    def __init__(self, *, write, interval, store, sleeper=None):
        self.write = write
        self.interval = interval
        self.store = store

    def run(self, *, stop_event):
        self.started.set()
        self.write("Монитор проверяет квоты без модельных ходов")
        stop_event.wait(timeout=3)
        self.finished.set()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        base = Path(td.name)
        self.project = base / "Проект с пробелами & (скобками)"
        self.project.mkdir()
        self.state = base / "state"
        FakeWatcher.started = threading.Event()
        FakeWatcher.finished = threading.Event()
        self.messages = []

    def managed(self, *, resume=False, fake_process=None, watcher_factory=FakeWatcher):
        calls = []
        def fake_factory(args, *, cwd, shell):
            calls.append((args, cwd, shell))
            self.assertTrue(FakeWatcher.started.wait(2))
            return fake_process or FakeCodexProcess()
        rc = run_managed(
            self.project, resume=resume, interval=60,
            codex_path="codex-test.exe", state_dir=self.state,
            process_factory=fake_factory, watcher_factory=watcher_factory,
            stdout=self.messages.append,
        )
        return rc, calls

    def test_new_tui_starts_one_watcher_and_stops_after_quit(self):
        rc, calls = self.managed()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [
            (["codex-test.exe", "--remote", "unix://", "--cd", str(self.project.resolve()), "--no-alt-screen"],
             str(self.project.resolve()), False),
        ])
        self.assertTrue(FakeWatcher.finished.is_set())
        log = (self.state / "managed-watch.log").read_text(encoding="utf-8")
        self.assertIn("без модельных ходов", log)

    def test_resume_is_last_in_same_cwd_with_explicit_remote(self):
        rc, calls = self.managed(resume=True, fake_process=FakeCodexProcess(9))
        self.assertEqual(rc, 9)
        self.assertEqual(calls[0][0], [
            "codex-test.exe", "resume", "--last", "--remote", "unix://",
            "--cd", str(self.project.resolve()), "--no-alt-screen",
        ])
        self.assertTrue(FakeWatcher.finished.is_set())

    def test_two_instances_fail_closed_without_launch(self):
        from quota_probe.watch import WatchLock
        called = []
        with WatchLock(self.state):
            with self.assertRaises(LaunchError):
                run_managed(self.project, codex_path="codex-test.exe",
                            state_dir=self.state,
                            process_factory=lambda *a,**k: called.append((a,k)))
        self.assertEqual(called, [])

    def test_missing_codex_does_not_start_watcher(self):
        with patch("launcher.managed.shutil.which", return_value=None):
            with self.assertRaises(LaunchError):
                run_managed(self.project, state_dir=self.state)
        self.assertFalse(self.state.exists())

    def test_launch_failure_releases_lock_and_stops_watcher(self):
        def fail(*a, **k):
            self.assertTrue(FakeWatcher.started.wait(2))
            raise OSError("Secret: exact private error")
        with self.assertRaises(LaunchError) as ctx:
            run_managed(self.project, codex_path="codex-test.exe",
                        interval=60, state_dir=self.state,
                        process_factory=fail,
                        watcher_factory=FakeWatcher)
        self.assertNotIn("Secret", str(ctx.exception))
        self.assertTrue(FakeWatcher.finished.is_set())
        from quota_probe.watch import WatchLock
        with WatchLock(self.state):
            pass

    def test_project_path_with_unicode_and_shell_metacharacters(self):
        p = project_directory('"' + str(self.project) + '"')
        self.assertEqual(p, self.project.resolve())
        self.assertEqual(codex_argv("fake-codex", directory=p, resume=False), [
            "fake-codex", "--remote", "unix://", "--cd", str(p), "--no-alt-screen",
        ])
        with self.assertRaises(LaunchError):
            project_directory(self.project / "missing-folder")

    def test_dry_run_never_executes_model_or_watcher(self):
        with patch("launcher.__main__.run_managed",
                   side_effect=AssertionError("must NOT start")):
            self.assertEqual(launcher_main([
                "--project", str(self.project), "--new", "--dry-run",
            ]), 0)
            self.assertEqual(launcher_main([
                "--project", str(self.project), "--resume",
                "--dry-run", "--interval", "60",
            ]), 0)

    def test_cancel_and_bad_interval_are_safe(self):
        with patch("launcher.__main__.run_managed",
                   side_effect=AssertionError("must NOT start")):
            with patch("builtins.input", return_value="0"):
                self.assertEqual(launcher_main(["--project", str(self.project)]), 0)
            self.assertEqual(launcher_main([
                "--project", str(self.project), "--new", "--interval", "2",
            ]), 1)

    def test_python_launcher_does_not_modify_sandbox_policy(self):
        import inspect
        from launcher import managed
        source = inspect.getsource(managed)
        for forbidden in (
            "dangerously-bypass-approvals", "danger-full-access",
            "CODEX_HOME", "daemon restart", "Stop-Process",
            "thread/resume", "turn/start",
        ):
            self.assertNotIn(forbidden, source)

    def test_watch_run_event_quits_without_second_read(self):
        ev = threading.Event()
        reads = []
        wait_calls = []

        class DummyStop:
            def is_set(self):
                return ev.is_set()
            def wait(self, seconds):
                wait_calls.append(seconds)
                ev.set()
                return True

        from quota_probe.watch import WatchState
        watcher = QuotaWatcher(
            interval=60,
            fetch=lambda: reads.append(True) or {
                "rateLimits": {
                    "primary": {"usedPercent": 4, "resetsAt": 1, "windowDurationMins": 300},
                    "secondary": {"usedPercent": 40, "resetsAt": 2, "windowDurationMins": 10080},
                },
            },
            store=WatchState(self.state),
            write=lambda _: None,
            sleeper=lambda seconds: self.fail("Must honor stop event directly"),
        )
        watcher.run(stop_event=DummyStop())
        self.assertEqual(reads, [True])
        self.assertEqual(wait_calls, [60])


class BatchWrapperTests(unittest.TestCase):
    def test_windows_cmd_uses_literalpath_and_python(self):
        cmd = (Path(__file__).resolve().parents[1] /
               "start-codex-supervisor.cmd").read_text(encoding="utf-8")
        self.assertIn('powershell.exe', cmd.lower())
        self.assertIn('Set-Location -LiteralPath', cmd)
        self.assertIn("Get-Command python", cmd)
        self.assertIn("$env:SUPERVISOR_PROJECT", cmd)
        self.assertIn("$env:SUPERVISOR_BUNDLE", cmd)
        self.assertNotIn("ExecutionPolicy Bypass", cmd)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertNotIn("Stop-Process", cmd)


if __name__ == "__main__":
    unittest.main()
