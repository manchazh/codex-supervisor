"""Проверки ядра Supervisor без Tkinter, сети, Codex и платных модельных ходов."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from supervisor.core import ActionError, Supervisor
from supervisor.mock import MockBackend
from supervisor.storage import AlreadyRunning, InstanceLock, StateError, StateStore


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name))
        self.app = Supervisor(self.store)

    def test_default_demo_starts_idle(self):
        self.assertEqual(self.app.active()["status"], "idle")
        self.assertEqual(len(self.app.projects()), 1)

    def test_submit_and_complete_mock(self):
        sid, tid = self.app.submit("одна\nдве")
        self.assertEqual(self.app.active()["status"], "running")
        answer = MockBackend().reply("одна\nдве")
        self.assertIn("Строк: 2", answer)
        self.assertTrue(self.app.finish_mock(sid, tid, answer))
        self.assertEqual(self.app.active()["status"], "idle")
        self.assertEqual([m["role"] for m in self.app.active()["messages"]],
                         ["user", "assistant"])

    def test_does_not_deliver_duplicate_turn(self):
        sid, tid = self.app.submit("привет")
        self.assertTrue(self.app.finish_mock(sid, tid, "ответ"))
        self.assertFalse(self.app.finish_mock(sid, tid, "повтор"))
        self.assertEqual(len(self.app.active()["messages"]), 2)

    def test_pause_during_running_applies_after_completion(self):
        sid, tid = self.app.submit("тест")
        self.app.pause()
        self.assertTrue(self.app.active()["pause_after"])
        self.assertTrue(self.app.finish_mock(sid, tid, "ответ"))
        self.assertEqual(self.app.active()["status"], "paused")
        with self.assertRaises(ActionError):
            self.app.submit("второй ход")
        self.app.resume()
        self.assertEqual(self.app.active()["status"], "idle")

    def test_pause_while_idle(self):
        self.app.pause()
        self.assertEqual(self.app.active()["status"], "paused")
        self.app.resume()
        self.assertEqual(self.app.active()["status"], "idle")

    def test_stop_cancels_pending_reply(self):
        sid, tid = self.app.submit("не повторять")
        self.app.stop()
        self.assertFalse(self.app.finish_mock(sid, tid, "запоздалый ответ"))
        self.assertEqual(self.app.active()["status"], "stopped")
        self.assertEqual(len(self.app.active()["messages"]), 1)
        with self.assertRaises(ActionError):
            self.app.resume()

    def test_completed_task_rejects_new_turns(self):
        self.app.complete()
        self.assertEqual(self.app.active()["status"], "completed")
        with self.assertRaises(ActionError):
            self.app.submit("не должен начаться")
        self.app.stop()
        self.assertEqual(self.app.active()["status"], "completed")

    def test_new_session_after_stop(self):
        self.app.stop()
        before = self.app.data["active_session_id"]
        after = self.app.new_session()
        self.assertNotEqual(before, after)
        self.assertEqual(self.app.active()["status"], "idle")
        self.assertEqual(len(self.app.sessions()), 2)

    def test_project_isolation(self):
        sid, tid = self.app.submit("первый")
        self.app.finish_mock(sid, tid, "ответ 1")
        first_project = self.app.data["active_project_id"]
        second = self.app.add_project("Второй")
        self.assertEqual(len(self.app.active()["messages"]), 0)
        with self.assertRaises(ActionError):
            self.app.select_session(sid)
        self.app.select_project(first_project)
        self.assertEqual(len(self.app.active()["messages"]), 2)
        self.app.select_project(second)
        self.assertEqual(len(self.app.active()["messages"]), 0)

    def test_bad_inputs(self):
        with self.assertRaises(ActionError):
            self.app.add_project("  ")
        with self.assertRaises(ActionError):
            self.app.select_project("not-a-real-project")
        with self.assertRaises(ActionError):
            self.app.submit("  ")
        with self.assertRaises(ActionError):
            self.app.submit("x" * 20001)
        sid, tid = self.app.submit("busy")
        with self.assertRaises(ActionError):
            self.app.submit("another")
        self.app.finish_mock(sid, tid, "done")

    def test_state_persists_between_instances(self):
        sid, tid = self.app.submit("сохранить")
        self.app.finish_mock(sid, tid, "сохранено")
        second = Supervisor(self.store)
        self.assertEqual(second.active()["messages"][-1]["text"], "сохранено")
        self.assertEqual(second.active()["status"], "idle")

    def test_crash_does_not_replay_unknown_turn(self):
        sid, tid = self.app.submit("неизвестный результат")
        restarted = Supervisor(self.store)
        self.assertEqual(restarted.active()["status"], "needs_review")
        self.assertEqual(restarted.active()["pending_turn"]["id"], tid)
        # Даже после ручного снятия блокировки повторной отправки нет.
        restarted.resume()
        self.assertEqual(restarted.active()["status"], "idle")
        self.assertIsNone(restarted.active()["pending_turn"])
        self.assertEqual(
            [m["role"] for m in restarted.active()["messages"]],
            ["user", "system"],
        )
        self.assertFalse(restarted.finish_mock(sid, tid, "дублированный ответ"))

    def test_corrupted_json_blocks_start_without_erasing(self):
        self.store.path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(StateError):
            Supervisor(self.store)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "{not-json")

    def test_invalid_schema_blocks_start(self):
        self.store.path.write_text(json.dumps({"version": 999}), encoding="utf-8")
        with self.assertRaises(StateError):
            Supervisor(self.store)

    def test_cross_project_selection_rejected(self):
        first = self.app.data["active_session_id"]
        self.app.add_project("Второй")
        with self.assertRaises(ActionError):
            self.app.select_session(first)

    def test_fake_limit_never_retries_automatically(self):
        self.app.simulate_wait()
        self.assertEqual(self.app.active()["status"], "await_limit")
        with self.assertRaises(ActionError):
            self.app.submit("продолжай")
        self.app.resume()  # Осознанное ручное действие только в демо.
        self.assertEqual(self.app.active()["messages"], [])

    def test_mock_backend_is_deterministic(self):
        backend = MockBackend()
        self.assertEqual(backend.reply("test"), backend.reply("test"))
        self.assertIn("Настоящий Codex не запускался", backend.reply("test"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_one_instance_lock(self):
        a = InstanceLock(self.directory)
        b = InstanceLock(self.directory)
        a.acquire()
        try:
            with self.assertRaises(AlreadyRunning):
                b.acquire()
        finally:
            a.close()
        b.acquire()
        b.close()

    def test_atomic_save_preserves_old_state_if_replace_fails(self):
        store = StateStore(self.directory)
        store.save({"version": 1, "value": "старое"})
        with patch("supervisor.storage.os.replace", side_effect=OSError("crash")):
            with self.assertRaises(StateError):
                store.save({"version": 1, "value": "новое"})
        self.assertEqual(store.load()["value"], "старое")
        self.assertFalse(list(self.directory.glob(".state-*.tmp")))

    def test_local_state_is_valid_utf8_json(self):
        store = StateStore(self.directory)
        store.save({"message": "проверка ё и «кавычки»"})
        self.assertEqual(store.load()["message"], "проверка ё и «кавычки»")


if __name__ == "__main__":
    unittest.main()
