import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zion import (
    ZionError, binary, compact_issues, developer_prompt, issue_digest,
    read_report, reviewer_prompt, workflow
)


class ZionTests(unittest.TestCase):
    def test_gui_resolved_hcom_path_is_reused(self):
        with patch.dict("os.environ", {"ZION_HCOM_BINARY": r"C:\\tools\\hcom.exe"}):
            self.assertEqual(binary("hcom"), r"C:\\tools\\hcom.exe")

    def test_hcom_preflight_rejects_blocked_before_sending_any_work(self):
        import subprocess
        from zion import hcom_preflight
        result = subprocess.CompletedProcess(
            ["hcom", "list", "rovo", "status"], 0, "blocked\n", "")
        with patch.dict("os.environ", {"ZION_HCOM_BINARY": "hcom.exe"}):
            with patch("zion.subprocess.run", return_value=result):
                with self.assertRaisesRegex(ZionError, "non disponibile"):
                    hcom_preflight(["rovo"])

    def test_compact_issues_caps_size(self):
        issues = [{"file": "a.py", "line": 12, "severity": "high",
                   "problem": "x" * 900} for _ in range(30)]
        result = compact_issues({"issues": issues})
        self.assertEqual(len(result), 12)
        self.assertEqual(len(result[0]["problem"]), 420)

    def test_digest_is_stable(self):
        data = [{"file": "a", "problem": "errore"}]
        self.assertEqual(issue_digest(data), issue_digest(data))
        self.assertNotEqual(issue_digest(data), issue_digest([]))

    def test_wrong_run_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps({"run_id": "wrong", "round": 1}))
            with self.assertRaises(ZionError):
                read_report(path, "right", 1, 1, 0.01)

    def test_prompt_minimal_and_no_push(self):
        path = Path("file.json")
        dev = developer_prompt("sistema", None, path, "abc", 1)
        review = reviewer_prompt(
            "sistema", {"summary": "fatto", "files_changed": ["x.py"]},
            path, "abc", 1
        )
        self.assertIn("non fare commit", dev.lower())
        self.assertIn("non fare commit", review.lower())
        self.assertIn('"run_id":"abc"', dev)

    def test_unicode_telemetry_safe_on_windows_cp1252_pipe(self):
        """Regression: a prompt containing '→' must not crash a cp1252 pipe."""
        import io
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                project=tmp, claude="claude", codex="codex",
                allow_dirty=False, dry_run=False,
                task="Verifica Claude → ZION → Codex; una prova breve",
                max_rounds=3, timeout=10, poll=0.01
            )
            calls = []
            def fake_wait(path, run_id, round_no, timeout, poll):
                if path.name.startswith("claude"):
                    return {"run_id": run_id, "round": round_no,
                            "status": "ready", "summary": "Letto → verificato",
                            "files_changed": []}
                return {"run_id": run_id, "round": round_no,
                        "status": "pass", "summary": "Revisione → OK",
                        "issues": []}

            buffer = io.BytesIO()
            output = io.TextIOWrapper(buffer, encoding="cp1252",
                                      errors="strict", write_through=True)
            try:
                with patch("zion.git_preflight"), patch("zion.hcom_preflight"):
                    with patch("zion.sys.stdout", output):
                        result = workflow(
                            args, send=lambda name, msg: calls.append(msg),
                            wait=fake_wait)
                output.flush()
                raw = buffer.getvalue().decode("cp1252")
            finally:
                output.detach()
            self.assertEqual(result, 0)
            self.assertEqual(len(calls), 2)
            self.assertIn("→", calls[0])
            events = [
                json.loads(line[len("ZION_UI_EVENT "):])
                for line in raw.splitlines()
                if line.startswith("ZION_UI_EVENT ")
            ]
            self.assertEqual(len(events), 4)
            self.assertIn("→", events[0]["prompt"])
            self.assertEqual(events[1]["summary"], "Letto → verificato")
            self.assertEqual(events[3]["summary"], "Revisione → OK")
            self.assertIn(r"\\u2192", raw)

    def test_max_three_rounds_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            args = argparse.Namespace(
                project=str(project), claude="claude", codex="codex",
                allow_dirty=False, dry_run=False, task="Correggi",
                max_rounds=3, timeout=10, poll=0.01
            )
            messages = []

            def fake_send(agent, message):
                messages.append((agent, message))

            def fake_wait(path, run_id, round_no, timeout, poll):
                if path.name.startswith("claude"):
                    return {"run_id": run_id, "round": round_no,
                            "status": "ready", "summary": "ok",
                            "files_changed": ["a.py"]}
                return {"run_id": run_id, "round": round_no,
                        "status": "fix", "issues": [{
                            "file": "a.py", "line": round_no,
                            "severity": "medium",
                            "problem": "da correggere"
                        }]}

            with patch("zion.git_preflight"), patch("zion.hcom_preflight"):
                result = workflow(args, send=fake_send, wait=fake_wait)
            self.assertEqual(result, 2)
            self.assertEqual(len(messages), 6)
            self.assertEqual([x[0] for x in messages], ["claude", "codex"] * 3)
            reports = sorted((project / ".zion" / "runs").glob("*/state.json"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(
                json.loads(reports[0].read_text())["status"], "needs_human"
            )

    def test_pass_after_one_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                project=tmp, claude="claude", codex="codex",
                allow_dirty=False, dry_run=False, task="Controlla",
                max_rounds=3, timeout=10, poll=0.01
            )
            sent = []
            def fake_wait(path, run_id, round_no, timeout, poll):
                data = {"run_id": run_id, "round": round_no}
                if path.name.startswith("claude"):
                    return dict(data, status="ready", files_changed=["x.py"])
                return dict(data, status="pass", issues=[])
            with patch("zion.git_preflight"), patch("zion.hcom_preflight"):
                result = workflow(
                    args, send=lambda agent, text: sent.append(agent),
                    wait=fake_wait
                )
            self.assertEqual(result, 0)
            self.assertEqual(sent, ["claude", "codex"])


if __name__ == "__main__":
    unittest.main()
