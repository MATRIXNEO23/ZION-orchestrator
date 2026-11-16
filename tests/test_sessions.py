"""Regression tests for owned hcom agents and live status visibility."""
import json
import subprocess
import unittest
from unittest.mock import Mock

from zion_sessions import OwnedHcomAgents, blocked_status, visible_status


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class OwnedAgentsTests(unittest.TestCase):
    def test_cleanup_kills_only_owned_agents_by_exact_name(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(list(args))
            return completed(args)
        sessions = OwnedHcomAgents(runner=runner)
        sessions.configure(r"C:\tools\hcom.exe", {"PATH": "mock"}, r"C:\repo")
        sessions.register("codex", "miso")
        sessions.register("claude", "nuve")
        result = sessions.cleanup()
        self.assertEqual(calls, [
            [r"C:\tools\hcom.exe", "kill", "miso"],
            [r"C:\tools\hcom.exe", "kill", "nuve"]
        ])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item[2] for item in result))
        self.assertEqual(sessions.snapshot(), {})
        self.assertEqual(sessions.cleanup(), [])

    def test_cleanup_retries_failed_kill_and_keeps_failures(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return completed(args, code=1, stderr="busy")
        sessions = OwnedHcomAgents(runner=runner)
        sessions.configure("hcom", {}, ".")
        sessions.register("claude", "rovo")
        with unittest.mock.patch("zion_sessions.time.sleep"):
            result = sessions.cleanup()
        self.assertEqual(len(calls), 2)
        self.assertFalse(result[0][2])
        self.assertEqual(sessions.snapshot(), {"claude": "rovo"})

        sessions._runner = lambda args, **kwargs: completed(args)
        self.assertTrue(sessions.cleanup()[0][2])
        self.assertEqual(sessions.snapshot(), {})

    def test_rejects_invalid_and_duplicate_registration(self):
        sessions = OwnedHcomAgents()
        with self.assertRaises(ValueError):
            sessions.register("unknown", "rovo")
        with self.assertRaises(ValueError):
            sessions.register("claude", "bad name")
        sessions.register("claude", "rovo")
        with self.assertRaises(RuntimeError):
            sessions.register("claude", "new")

    def test_statuses_only_show_own_agents(self):
        report = [
            {"name": "rovo", "status": "blocked",
             "status_context": "launch_blocked",
             "status_detail": "Claude mod never connected"},
            {"name": "zumi", "status": "listening"},
            {"name": "kira", "status": "active"}
        ]
        def runner(args, **kwargs):
            return completed(args, stdout=json.dumps(report))
        sessions = OwnedHcomAgents(runner=runner)
        sessions.configure("hcom", {}, ".")
        sessions.register("claude", "rovo")
        sessions.register("codex", "kira")
        statuses = sessions.statuses()
        self.assertEqual(set(statuses), {"rovo", "kira"})
        self.assertTrue(blocked_status(statuses["rovo"]))
        self.assertFalse(blocked_status(statuses["kira"]))
        self.assertIn("launch_blocked", visible_status(statuses["rovo"]))

    def test_statuses_reject_bad_json(self):
        sessions = OwnedHcomAgents(runner=lambda args, **kwargs:
                                   completed(args, stdout="not-json"))
        sessions.configure("hcom", {}, ".")
        sessions.register("claude", "rovo")
        with self.assertRaisesRegex(RuntimeError, "JSON"):
            sessions.statuses()


if __name__ == "__main__":
    unittest.main()
