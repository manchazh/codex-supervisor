"""Офлайн-проверки запускалки: настоящий Codex никогда не вызывается."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from launcher.launch import LaunchError, launch, main, project_directory
from tools.build_launcher import FILES, build


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "Проект с пробелами & символами"
        self.project.mkdir()
        (self.project / "project-marker.txt").write_text("fixture", encoding="ascii")

    def test_directory_with_quotes_spaces_unicode_and_metacharacters(self):
        self.assertEqual(project_directory(f'"{self.project}"'), self.project.resolve())

    def test_bad_project_and_file_refused(self):
        file = self.project / "file.txt"
        file.write_text("пример", encoding="utf-8")
        for value in ("", str(file), str(self.project / "missing")):
            with self.subTest(value=value), self.assertRaises(LaunchError):
                project_directory(value)

    @patch("launcher.launch.subprocess.call", return_value=0)
    @patch("launcher.launch.shutil.which", return_value="codex")
    def test_new_session_has_no_prompt_or_policy_override(self, which, call):
        self.assertEqual(launch(self.project), 0)
        call.assert_called_once_with(["codex"], cwd=str(self.project), shell=False)

    @patch("launcher.launch.subprocess.call", return_value=7)
    @patch("launcher.launch.shutil.which", return_value="codex")
    def test_resume_uses_picker_and_preserves_exit_code(self, which, call):
        self.assertEqual(launch(self.project, resume=True), 7)
        call.assert_called_once_with(["codex", "resume"], cwd=str(self.project), shell=False)

    @patch("launcher.launch.subprocess.call")
    @patch("launcher.launch.shutil.which", return_value=None)
    def test_missing_cli_never_starts_child(self, which, call):
        with self.assertRaises(LaunchError):
            launch(self.project)
        call.assert_not_called()

    @patch("launcher.launch.subprocess.call", side_effect=OSError("отказ"))
    @patch("launcher.launch.shutil.which", return_value="codex")
    def test_start_failure_is_reported_without_retry(self, which, call):
        with self.assertRaises(LaunchError):
            launch(self.project)
        self.assertEqual(call.call_count, 1)

    @patch("launcher.launch.launch")
    def test_cancel_and_invalid_mode_do_not_launch(self, child):
        for mode, expected in (("", 0), ("9", 1)):
            with patch("builtins.input", side_effect=[str(self.project), mode]):
                self.assertEqual(main([]), expected)
        child.assert_not_called()

    @patch("launcher.launch.launch", return_value=0)
    def test_explicit_project_skips_interactive_prompt(self, child):
        with patch("builtins.input", side_effect=AssertionError("не нужен ввод")):
            self.assertEqual(main([str(self.project), "--resume"]), 0)
        child.assert_called_once_with(self.project.resolve(), True)

    @patch("launcher.launch.launch")
    def test_eof_and_interrupt_cancel(self, child):
        for error, expected in ((EOFError, 0), (KeyboardInterrupt, 130)):
            with patch("builtins.input", side_effect=error):
                self.assertEqual(main([]), expected)
        child.assert_not_called()

    @patch("launcher.launch.subprocess.call", side_effect=KeyboardInterrupt)
    @patch("launcher.launch.shutil.which", return_value="codex")
    def test_interrupt_does_not_restart_codex(self, which, call):
        self.assertEqual(main([str(self.project)]), 130)
        call.assert_called_once()

    def test_archive_contains_only_allowlisted_files_and_can_move(self):
        root = Path(__file__).resolve().parents[1]
        output = build(root, Path(self.temp.name) / "archive.zip")
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"codex-launcher/" + n for n in FILES})
            self.assertNotIn(b"\r", archive.read("codex-launcher/start.sh"))
            cmd = archive.read("codex-launcher/start.cmd")
            self.assertIn(b"\r\n", cmd)
            self.assertNotIn(b"\n", cmd.replace(b"\r\n", b""))
            archive.extractall(Path(self.temp.name) / "перенос")
        moved = Path(self.temp.name) / "перенос" / "codex-launcher"
        self.assertTrue((moved / "launch.py").is_file())
        self.assertEqual((moved / "launch.py").read_bytes(), (root / "launcher/launch.py").read_bytes())

    @unittest.skipUnless(os.name == "nt", "Windows CMD проверяется на Windows")
    def test_extracted_launcher_runs_fake_cli_on_windows(self):
        root = Path(__file__).resolve().parents[1]
        output = build(root, Path(self.temp.name) / "smoke.zip")
        with zipfile.ZipFile(output) as archive:
            archive.extractall(Path(self.temp.name) / "копия")
        fake_bin = Path(self.temp.name) / "fake-bin"
        fake_bin.mkdir()
        # Самостоятельный фиктивный CLI: никакого настоящего Codex даже
        # при ошибке определения PATH. Не выводит пути и пользовательские данные.
        (fake_bin / "codex.cmd").write_text(
            "@echo off\nif not exist project-marker.txt exit /b 8\n"
            "if not \"%1\"==\"resume\" exit /b 9\n"
            "echo FAKE_CODEX_OK\nexit /b 7\n", encoding="ascii"
        )
        env = dict(os.environ, PATH=str(fake_bin))
        script = Path(self.temp.name) / "копия/codex-launcher/launch.py"
        result = subprocess.run(
            [sys.executable, str(script), str(self.project), "--resume"],
            env=env, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn(b"FAKE_CODEX_OK", result.stdout)

    def windows_entry(self, arguments=(), fake_cli=True):
        root = Path(__file__).resolve().parents[1]
        output = build(root, Path(self.temp.name) / "entry.zip")
        with zipfile.ZipFile(output) as archive:
            archive.extractall(Path(self.temp.name) / "распаковка с пробелами")
        fake_bin = Path(self.temp.name) / "fake-entry-bin"
        fake_bin.mkdir()
        if fake_cli:
            (fake_bin / "codex.cmd").write_text(
                "@echo off\nif not exist project-marker.txt exit /b 8\n"
                "if not \"%2\"==\"\" exit /b 9\n"
                "if not \"%1\"==\"\" if not \"%1\"==\"resume\" exit /b 10\n"
                "echo new> launcher-result.txt\n"
                "if \"%1\"==\"resume\" echo resume> launcher-result.txt\n"
                "exit /b 0\n", encoding="ascii"
            )
        # Без Python и настоящего Codex в PATH. Копируем только CMD в проект.
        env = dict(os.environ, PATH=os.pathsep.join([
            str(fake_bin),
            str(Path(os.environ["SystemRoot"]) / "System32"),
        ]))
        report = Path(self.temp.name) / "shell-report.json"
        env["LAUNCHER_TEST_REPORT"] = str(report)
        source = (Path(self.temp.name) / "распаковка с пробелами/codex-launcher/start.cmd").read_bytes().decode("utf-8")
        # Для автоматического теста дождаться нового окна и закрыть его после
        # команды. Основной PowerShell-код сохраняется, добавляется диагностика.
        source = source.replace('start "Codex | Windows PowerShell"', 'start /wait "Codex | Windows PowerShell"')
        source = source.replace("-NoExit", "-NoProfile")
        location = "Set-Location -LiteralPath $launcherProject -ErrorAction Stop;"
        source = source.replace(location, location + " [pscustomobject]@{ shell = (Get-Process -Id $PID).Path; title = $Host.UI.RawUI.WindowTitle; cwd = (Get-Location).Path; background = [string]$Host.UI.RawUI.BackgroundColor } | ConvertTo-Json | Set-Content -LiteralPath $env:LAUNCHER_TEST_REPORT -Encoding UTF8;")
        entry = self.project / "start.cmd"
        # Сохранять байты из ZIP, включая окончания строк: write_text на Windows
        # незаметно исправлял LF в CRLF и скрывал поломку реальной поставки.
        entry.write_bytes(source.encode("utf-8"))
        result = subprocess.run(
            [str(entry), *arguments], cwd=self.temp.name,
            env=env, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report.exists(), result.stderr)
        return json.loads(report.read_text(encoding="utf-8-sig"))

    @unittest.skipUnless(os.name == "nt", "PowerShell проверяется на Windows")
    def test_windows_double_click_uses_own_folder_and_separate_powershell(self):
        report = self.windows_entry()
        self.assertEqual(Path(report["cwd"]), self.project.resolve())
        self.assertTrue(report["shell"].lower().endswith("\\powershell.exe"))
        self.assertEqual(report["title"], "Codex | Windows PowerShell")
        self.assertEqual(report["background"], "DarkBlue")
        self.assertEqual((self.project / "launcher-result.txt").read_text().strip(), "new")

    @unittest.skipUnless(os.name == "nt", "PowerShell проверяется на Windows")
    def test_windows_resume_uses_own_folder_without_python(self):
        report = self.windows_entry(["--resume"])
        self.assertEqual(Path(report["cwd"]), self.project.resolve())
        self.assertEqual((self.project / "launcher-result.txt").read_text().strip(), "resume")

    @unittest.skipUnless(os.name == "nt", "PowerShell проверяется на Windows")
    def test_windows_missing_cli_does_not_launch_or_change_project(self):
        report = self.windows_entry(fake_cli=False)
        self.assertEqual(Path(report["cwd"]), self.project.resolve())
        self.assertFalse((self.project / "launcher-result.txt").exists())
        self.assertEqual((self.project / "project-marker.txt").read_text(), "fixture")


if __name__ == "__main__":
    unittest.main()
