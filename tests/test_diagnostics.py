import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zion_diagnostics import collect_diagnostics, save_report


class DiagnosticsTests(unittest.TestCase):
    def test_safe_readonly_commands_and_redacted_output(self):
        calls = []

        def fake_run(argv, env, timeout):
            calls.append(argv[1:])
            cmd = tuple(argv[1:])
            if cmd == ("login", "status"):
                return 0, "Logged in using ChatGPT\nprivate-token-123", ""
            if cmd == ("auth", "status"):
                return 0, '{"loggedIn": true, "email": "private@example.com"}', ""
            if cmd == ("hooks", "status", "codex"):
                return 0, "Codex: per-run", ""
            if cmd == ("hooks", "status", "claude"):
                return 0, "Claude: per-run", ""
            return 0, "success", ""

        def which(name, path=None):
            return "/tools/" + name

        with patch("zion_diagnostics.shutil.which", side_effect=which):
            report = collect_diagnostics(env={"PATH": "/tools"}, runner=fake_run)
        self.assertIn(("OK", "Login Codex",
                       "Autenticazione locale rilevata (non prova connessione hcom)"), report)
        self.assertTrue(any(s == "OK" and l == "hcom hook codex" for s, l, d in report))
        self.assertTrue(any(l == "Limite" for s, l, d in report))
        all_text = str(report)
        self.assertNotIn("private-token-123", all_text)
        self.assertNotIn("private@example.com", all_text)
        self.assertNotIn(("install",), [tuple(x) for x in calls])
        self.assertFalse(any(x[:1] in (["claude"], ["codex"]) for x in calls))
        self.assertTrue(all(x[0] in ("--version", "hooks", "list", "login", "auth",
                                     "app-server") for x in calls))

    def test_missing_tools_are_reported_without_crash(self):
        with patch("zion_diagnostics.shutil.which", return_value=None):
            report = collect_diagnostics(env={"PATH": ""})
        self.assertEqual(len([1 for s, _, _ in report if s == "ERRORE"]), 4)

    def test_report_can_be_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = save_report([("OK", "hcom", "per-run")], temp)
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, Path(temp))
            self.assertIn("[OK] hcom: per-run", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
