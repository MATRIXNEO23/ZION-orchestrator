import unittest
from zion_gui import agent_name, error_hint

class GuiUnitTests(unittest.TestCase):
    def test_extract_codex_agent(self):
        self.assertEqual(agent_name('{"name":"codex-1"}'), "codex-1")

    def test_hcom_real_headless_launch_response(self):
        """Regression: hcom 0.7.31 returns a names list, not name."""
        report = ('{"action":"launch","background":true,"batch_id":"1e32ebde",'
                  '"errors":[],"failed":0,"launched":1,"names":["zumi"],'
                  '"readiness":null,"requested":1,"tool":"codex"}')
        self.assertEqual(agent_name(report), "zumi")

    def test_hcom_launch_rejects_missing_or_ambiguous_agents(self):
        self.assertIsNone(agent_name(
            '{"action":"launch","errors":[],"failed":0,"launched":0,"names":[]}'))
        self.assertIsNone(agent_name(
            '{"action":"launch","errors":[],"failed":0,"launched":2,"names":["a","b"]}'))
        self.assertIsNone(agent_name(
            '{"action":"launch","errors":[],"failed":1,"launched":1,"names":["a"]}'))
        self.assertIsNone(agent_name(
            '{"action":"launch","errors":["failure"],"failed":0,"launched":1,"names":["a"]}'))
        self.assertIsNone(agent_name(
            '{"action":"launch","errors":[],"failed":0,"launched":1,"names":["bad name"]}'))

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

    def test_winerror_2_hint(self):
        self.assertIn("eseguibile", error_hint("[WinError 2] File non trovato"))

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
            app.log("zion", "Comando: hcom codex --headless")
            app.log("zion", "database is locked", is_error=True)
            self.assertTrue(any("database is locked" in x for x in app.errors))
            self.assertTrue(any("Comando: hcom codex" in x for x in app.errors))
            self.assertIn("Comando: hcom codex", app.error_view.get("1.0", "end"))
        finally:
            app.destroy()


    def test_launch_error_names_failing_command(self):
        import tkinter
        from unittest.mock import patch
        from zion_gui import ZionApp
        try:
            app = ZionApp()
        except tkinter.TclError as ex:
            self.skipTest("Desktop UI unavailable: " + str(ex))
        try:
            with patch("zion_gui.subprocess.run", side_effect=FileNotFoundError(2, "file not found")):
                with self.assertRaisesRegex(RuntimeError, "hcom.*codex"):
                    app.command(["hcom", "codex", "--headless"], {}, ".", timeout=1)
        finally:
            app.destroy()


    def test_codex_warmup_rpc_without_model_calls(self):
        import io
        from unittest.mock import patch
        from zion_gui import warm_codex_app_server

        class Process:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(chr(10).join([
                    '{"id":1,"result":{}}',
                    '{"id":2,"result":{"hooks":[]}}'
                ]) + chr(10))
                self.stderr = io.StringIO()
                self.pid = 123
            def wait(self, timeout=None):
                return 0
            def poll(self):
                return 0

        process = Process()
        with patch("zion_gui.subprocess.Popen", return_value=process) as popen:
            first, second = warm_codex_app_server(
                "codex.cmd", ".", timeout=5)
        self.assertGreaterEqual(first, 0)
        self.assertGreaterEqual(second, 0)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(popen.called)

    def test_codex_warmup_missing_reply_is_error(self):
        import io
        from unittest.mock import patch
        from zion_gui import warm_codex_app_server

        class Process:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.pid = 123
            def wait(self, timeout=None):
                return 0
            def poll(self):
                return 0

        with patch("zion_gui.subprocess.Popen", return_value=Process()):
            with self.assertRaisesRegex(RuntimeError, "initialize senza risposta"):
                warm_codex_app_server("codex.cmd", ".", timeout=1)

    def test_window_close_kills_only_agents_owned_by_this_app(self):
        import os
        import subprocess
        import tkinter
        from zion_gui import ZionApp
        try:
            app = ZionApp()
        except tkinter.TclError as exc:
            self.skipTest("Desktop GUI unavailable: " + str(exc))
        app.withdraw()
        calls = []
        def runner(args, **kwargs):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        try:
            app.sessions._runner = runner
            app.sessions.configure("hcom.exe", dict(os.environ), os.getcwd())
            app.sessions.register("codex", "miso")
            app.sessions.register("claude", "nuve")
            app.close_app()
            self.assertTrue(app.closing)
            app.finish_close()
            if app.close_cleanup_thread:
                app.close_cleanup_thread.join(timeout=5)
            # The first call can already have completed closing the window.
            try:
                still_open = bool(app.winfo_exists())
            except tkinter.TclError:
                still_open = False
            if still_open:
                app.finish_close()
            self.assertEqual(calls, [
                ["hcom.exe", "kill", "miso"],
                ["hcom.exe", "kill", "nuve"]])
            self.assertFalse(any("all" in args for args in calls))
        finally:
            try:
                if app.winfo_exists():
                    app.destroy()
            except tkinter.TclError:
                pass

    def test_close_waits_for_launch_worker_before_cleanup(self):
        import tkinter
        from unittest.mock import Mock, patch
        from zion_gui import ZionApp
        try:
            app = ZionApp()
        except tkinter.TclError as exc:
            self.skipTest("Desktop GUI unavailable: " + str(exc))
        try:
            app.withdraw()
            app.worker_thread = Mock()
            app.worker_thread.is_alive.return_value = True
            with patch.object(app, "after") as after:
                with patch.object(app, "destroy") as destroy:
                    app.close_app()
                    app.finish_close()
                    self.assertFalse(destroy.called)
                    self.assertTrue(after.called)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
