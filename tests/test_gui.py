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

    def test_error_window_opens_without_models(self):
        import tkinter
        from zion_gui import ZionApp
        try:
            app = ZionApp()
        except tkinter.TclError as ex:
            self.skipTest("Desktop UI unavailable on CI runner: " + str(ex))
        try:
            app.withdraw()
            app.update()
            app.show_errors()
            app.update()
            self.assertTrue(app.error_window.winfo_exists())
            app.log("zion", "database is locked", is_error=True)
            self.assertTrue(any("database is locked" in x for x in app.errors))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
