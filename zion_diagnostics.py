"""Diagnostica non distruttiva di ZION: nessuna installazione o chiamata ai modelli."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"


def _run(argv, env, timeout=12):
    """Non restituire nei log l'output grezzo: puo' contenere dati personali."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, (proc.stdout + "\n" + proc.stderr)[:4000], ""
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except OSError as exc:
        return None, "", type(exc).__name__


def collect_diagnostics(project=None, env=None, runner=None):
    """Restituisce righe prive di token/credenziali; non avvia agenti.

    La verifica del login conferma soltanto il risultato della CLI locale;
    non dimostra che un agente sia connesso e possa lavorare tramite hcom.
    """
    env = dict(env if env is not None else os.environ)
    runner = runner or _run
    records = []

    def add(status, label, detail):
        records.append((status, label, detail))

    binaries = {}
    add("INFO", "Ambiente", "Diagnostica in sola lettura; nessun modello chiamato")
    for tool in ("git", "hcom", "claude", "codex"):
        resolved = shutil.which(tool, path=env.get("PATH"))
        if not resolved:
            add("ERRORE", tool, "Comando non trovato nel PATH")
            continue
        binaries[tool] = resolved
        code, output, fault = runner([resolved, "--version"], env, 12)
        if code == 0:
            # Non includere stdout arbitrario: puo' contenere valori sensibili.
            version = next((line.strip() for line in output.splitlines()
                            if line.strip()), "versione disponibile")
            version = version[:120] if tool in ("git", "hcom") else "comando disponibile"
            add("OK", tool, version)
        else:
            add("ATTENZIONE", tool, "Versione non verificata" + (": " + fault if fault else ""))

    hcom = binaries.get("hcom")
    if hcom:
        for agent in ("codex", "claude"):
            code, output, fault = runner([hcom, "hooks", "status", agent], env, 12)
            if code == 0:
                kind = "per-run" if "per-run" in output.lower() else "comando riuscito"
                add("OK", "hcom hook " + agent, kind)
            else:
                add("ATTENZIONE", "hcom hook " + agent,
                    "Verifica non riuscita" + (": " + fault if fault else ""))
        code, output, fault = runner([hcom, "list"], env, 12)
        add("OK" if code == 0 else "ATTENZIONE", "hcom list",
            "Comando eseguito; agenti non necessariamente attivi" if code == 0
            else "Elenco non disponibile" + (": " + fault if fault else ""))

    codex = binaries.get("codex")
    if codex:
        code, output, fault = runner([codex, "login", "status"], env, 15)
        auth_text = output.lower()
        if code == 0 and ("logged in" in auth_text or "authenticated" in auth_text):
            add("OK", "Login Codex", "Autenticazione locale rilevata (non prova connessione hcom)")
        elif code == 0:
            add("ATTENZIONE", "Login Codex", "Stato non riconosciuto; verificare dall'app")
        else:
            add("ATTENZIONE", "Login Codex",
                "Non risulta confermato" + (": " + fault if fault else ""))
        code, output, fault = runner([codex, "app-server", "--help"], env, 15)
        add("OK" if code == 0 else "ATTENZIONE", "Codex App Server",
            "Comando disponibile (nessun server avviato)" if code == 0
            else "Disponibilita' non confermata" + (": " + fault if fault else ""))

    claude = binaries.get("claude")
    if claude:
        code, output, fault = runner([claude, "auth", "status"], env, 15)
        normalized = output.lower()
        # Non mostrare identita', email o eventuali token prodotti dal comando.
        if code == 0 and ('"loggedin": true' in normalized
                          or '"loggedin":true' in normalized
                          or "logged in" in normalized
                          or '"authenticated": true' in normalized):
            add("OK", "Login Claude", "Autenticazione locale rilevata")
        else:
            add("ATTENZIONE", "Login Claude",
                "Non confermato o comando non supportato" + (": " + fault if fault else ""))

    if project:
        folder = Path(project).expanduser()
        git = binaries.get("git")
        if not folder.is_dir():
            add("ERRORE", "Progetto", "Cartella non trovata")
        elif not git:
            add("ATTENZIONE", "Progetto", "Git non disponibile")
        else:
            code, output, fault = runner(
                [git, "-C", str(folder), "rev-parse", "--show-toplevel"], env, 12)
            if code != 0:
                add("ERRORE", "Progetto", "Non e' una repository Git valida")
            else:
                code, output, fault = runner(
                    [git, "-C", str(folder), "status", "--porcelain"], env, 12)
                if code == 0:
                    add("ATTENZIONE" if output.strip() else "OK", "Progetto",
                        "Modifiche locali presenti" if output.strip() else "Repository pulita")
                else:
                    add("ATTENZIONE", "Progetto", "Stato Git non leggibile")

    add("INFO", "Limite", "Test del dialogo reale Claude-Codex NON eseguito")
    return records


def save_report(records, log_dir=None):
    destination = Path(log_dir) if log_dir is not None else LOG_DIR
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = destination / ("diagnostica_" + stamp + ".log")
    summary = "\n".join(f"[{status}] {label}: {detail}" for status, label, detail in records)
    path.write_text("ZION - Diagnostica locale\n" + summary + "\n", encoding="utf-8")
    return path
