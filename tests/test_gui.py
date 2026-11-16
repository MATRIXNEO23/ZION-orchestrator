import unittest
from zion_gui import agent_name, error_hint

class GuiUnitTests(unittest.TestCase):
    def test_extract_codex_agent(self):
        self.assertEqual(agent_name('{"name":"codex-1"}'), "codex-1")

    def test_nested_agent(self):
        self.assertEqual(agent_name('{"agent":{"agent_name":"claude-22"}}'), "claude-22")

    def test_invalid_agent_does_not_guess(self):
        self.assertIsNone(agent_name('{"status":"ok"}'))
        self.assertIsNone(agent_name('bad json'))

    def test_database_locked_hint_is_non_destructive(self):
        hint = error_hint("database is locked")
        self.assertIn("SQLite", hint)
        self.assertIn("non elimina", hint)

    def test_app_server_hint(self):
        self.assertIn("App Server", error_hint("Codex App Server closed before response"))

if __name__ == "__main__":
    unittest.main()
