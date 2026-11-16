"""Monitor and clean up only hcom agents launched by this Zion instance.

No global hcom reset/kill-all. hcom CLI commands are always sent to explicit,
validated agent names. The registry is kept in memory for the GUI lifetime.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

AGENT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class OwnedHcomAgents:
    def __init__(self, runner=None):
        self._runner = runner or subprocess.run
        self._lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._agents = {}
        self._binary = None
        self._env = None
        self._folder = None

    def configure(self, binary, env, folder):
        with self._lock:
            self._binary = str(binary)
            self._env = dict(env)
            self._folder = str(folder)

    def register(self, role, name):
        if role not in ("claude", "codex") or not isinstance(name, str) or not AGENT_NAME.fullmatch(name):
            raise ValueError("Invalid role or hcom agent name")
        with self._lock:
            if role in self._agents:
                raise RuntimeError("A previously launched agent is still registered: " + role)
            self._agents[role] = name

    def snapshot(self):
        with self._lock:
            return dict(self._agents)

    def cleanup(self):
        """Return [(role, name, success, detail)] and retain failures for retry."""
        results = []
        with self._cleanup_lock:
            for role, name in self.snapshot().items():
                with self._lock:
                    binary, env, folder = self._binary, self._env, self._folder
                if not binary:
                    results.append((role, name, False, "Percorso hcom non disponibile"))
                    continue
                success, detail = False, ""
                for attempt in range(2):
                    try:
                        result = self._runner(
                            [binary, "kill", name], cwd=folder, env=env,
                            capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        success = result.returncode == 0
                        detail = (result.stderr or result.stdout or "").strip()[-350:]
                    except (OSError, subprocess.SubprocessError) as exc:
                        detail = str(exc)[:350]
                    if success:
                        break
                    if attempt == 0:
                        time.sleep(0.5)
                if success:
                    with self._lock:
                        if self._agents.get(role) == name:
                            del self._agents[role]
                results.append((role, name, success, detail))
        return results

    def statuses(self):
        """Read actual hcom state for owned agents; no state is inferred."""
        with self._lock:
            binary, env, folder = self._binary, self._env, self._folder
            names = set(self._agents.values())
        if not binary or not names:
            return {}
        try:
            result = self._runner(
                [binary, "list", "--json"], cwd=folder, env=env,
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("hcom list non disponibile: " + str(exc)) from exc
        if result.returncode != 0:
            raise RuntimeError("hcom list fallito: " + result.stderr.strip()[-250:])
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError("Risposta JSON hcom list non valida") from exc
        if not isinstance(data, list):
            raise RuntimeError("Formato hcom list inatteso")
        return {row["name"]: row for row in data
                if isinstance(row, dict) and row.get("name") in names}


def blocked_status(info):
    """hcom launch_blocked means unavailable, not 'working'."""
    if not isinstance(info, dict):
        return False
    status = str(info.get("status") or "").lower()
    context = str(info.get("status_context") or "").lower()
    return (status.startswith("blocked")
            or "launch_blocked" in context)


def visible_status(info):
    if not isinstance(info, dict):
        return "Nessun dato hcom"
    state = str(info.get("status") or "sconosciuto")[:40]
    context = str(info.get("status_context") or "")[:60]
    detail = str(info.get("status_detail") or info.get("description") or "")[:180]
    bits = [state]
    if context and context.lower() not in state.lower():
        bits.append(context)
    if detail:
        bits.append(detail)
    return " · ".join(bits)
