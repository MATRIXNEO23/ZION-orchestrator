#!/usr/bin/env python3
"""ZION: coordinatore minimo Claude Code <-> Codex CLI tramite hcom."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_REPORT_BYTES = 65536
MAX_ISSUES_SENT = 12
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ZionError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read_report(path: Path, run_id: str, round_no: int, timeout: int, poll: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            if path.stat().st_size > MAX_REPORT_BYTES:
                raise ZionError(f"Rapporto troppo grande: {path.name}")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(poll)
                continue
            if not isinstance(data, dict) or data.get("run_id") != run_id or data.get("round") != round_no:
                raise ZionError(f"Rapporto non valido o appartenente a un altro ciclo: {path.name}")
            return data
        time.sleep(poll)
    raise ZionError(f"Timeout: rapporto non ricevuto ({path.name})")


def compact_issues(data: dict) -> list[dict]:
    issues = data.get("issues", [])
    if not isinstance(issues, list):
        raise ZionError("Il campo issues deve essere una lista")
    out = []
    for item in issues[:MAX_ISSUES_SENT]:
        if not isinstance(item, dict):
            continue
        out.append({
            "file": str(item.get("file", ""))[:180],
            "line": str(item.get("line", ""))[:20],
            "severity": str(item.get("severity", ""))[:16],
            "problem": str(item.get("problem", ""))[:420],
        })
    return out


def issue_digest(issues: list[dict]) -> str:
    return hashlib.sha256(json.dumps(issues, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def git_preflight(project: Path, allow_dirty: bool) -> None:
    try:
        root = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=15
        ).stdout.strip()
        result = subprocess.run(
            ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, check=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ZionError(f"Il progetto deve essere una repo Git accessibile: {exc}") from exc
    if Path(root).resolve() != project:
        raise ZionError("Indica la radice del repository Git con --project")
    dirty = [line for line in result.stdout.splitlines()
             if not line[3:].replace("\\", "/").startswith(".zion/")]
    if dirty and not allow_dirty:
        raise ZionError("Git contiene modifiche preesistenti. Usa un worktree pulito "
                        "oppure --allow-dirty consapevolmente.")


def hcom_preflight(names: list[str]) -> None:
    if not shutil.which("hcom"):
        raise ZionError("hcom non trovato nel PATH. Installa prima hcom.")
    for name in names:
        if not NAME_RE.fullmatch(name):
            raise ZionError(f"Nome agente non valido: {name}")
        result = subprocess.run(
            ["hcom", "list", name, "status"], capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0 or result.stdout.strip().lower() in {"gone", "inactive"}:
            raise ZionError(f"Agente hcom non attivo: {name}. Verifica con: hcom list")


def send_hcom(agent: str, message: str) -> None:
    result = subprocess.run(
        ["hcom", "send", "-b", "@" + agent, "--", message],
        capture_output=True, text=True, timeout=45
    )
    if result.returncode != 0:
        raise ZionError(f"Invio a {agent} fallito: {result.stderr.strip()[:500]}")


def report_instruction(path: Path, run_id: str, round_no: int) -> str:
    return (
        f"Al termine salva SOLO un JSON UTF-8 valido in {path}, scrivendo prima "
        f"{path}.tmp e rinominandolo atomicamente. Campi obbligatori: "
        f'{{"run_id":"{run_id}","round":{round_no},'
    )


def developer_prompt(task: str, issues: list[dict] | None, path: Path,
                     run_id: str, round_no: int) -> str:
    assignment = ("COMPITO: " + task[:3000]) if issues is None else (
        "CORREGGI SOLO QUESTI PROBLEMI: " +
        json.dumps(issues, ensure_ascii=False, separators=(",", ":"))
    )
    return (
        f"ZION DEV R{round_no}. {assignment}\n"
        "Lavora nel repository già aperto. Non fare commit, push, delete, "
        "installazioni o cambiamenti fuori progetto senza approvazione. "
        "Non parlare con Codex direttamente; non creare conversazioni. "
        "Modifica i file necessari, esegui test locali pertinenti. "
        + report_instruction(path, run_id, round_no)
        + '"status":"ready|blocked","summary":"max 500 caratteri",'
        '"files_changed":["percorso"],"tests":"esito breve"}. '
        "Usa status=blocked se serve un intervento umano."
    )


def reviewer_prompt(task: str, dev: dict, path: Path, run_id: str, round_no: int) -> str:
    files = dev.get("files_changed", [])
    if not isinstance(files, list):
        files = []
    files = [str(x)[:180] for x in files[:30]]
    first = ("OBIETTIVO: " + task[:1500] + ". ") if round_no == 1 else ""
    return (
        f"ZION REVIEW R{round_no}. {first}"
        "Verifica le modifiche REALI nel repository, inclusi i file nuovi, "
        "i test e i possibili regressi; il rapporto di Claude è solo un indice. "
        "Non modificare il codice; non fare commit/push/installazioni. "
        f"FILE: {json.dumps(files, ensure_ascii=False)}. "
        f"RESOCONTO: {str(dev.get('summary', ''))[:400]}. "
        + report_instruction(path, run_id, round_no)
        + '"status":"pass|fix|blocked","summary":"max 400 caratteri",'
        '"issues":[{"file":"path","line":12,"severity":"high|medium|low",'
        '"problem":"descrizione concreta"}]}. '
        "Usa pass SOLO con controlli sufficienti e nessun problema bloccante; "
        "fix per correzioni necessarie; blocked per impossibilità di verificare. "
        "Massimo 12 problemi, niente convenevoli."
    )


def workflow(args: argparse.Namespace, send=send_hcom, wait=read_report) -> int:
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise ZionError(f"Cartella non trovata: {project}")
    if args.claude == args.codex:
        raise ZionError("Claude e Codex devono avere due nomi diversi")
    git_preflight(project, args.allow_dirty)
    if not args.dry_run:
        hcom_preflight([args.claude, args.codex])

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    folder = project / ".zion" / "runs" / run_id
    state = {"run_id": run_id, "project": str(project), "started": now(),
             "task": args.task, "status": "running", "round": 0, "events": []}
    if args.dry_run:
        print("DRY-RUN: nessun messaggio inviato e nessun file modificato.")
        print(developer_prompt(args.task, None, folder / "claude-1.json", run_id, 1))
        return 0

    def note(event: str, round_no: int) -> None:
        state["round"] = round_no
        state["events"].append({"time": now(), "event": event})
        atomic_json(folder / "state.json", state)
        print(f"[ZION] {event} (ciclo {round_no})", flush=True)

    issues = None
    previous_digest = None
    try:
        for round_no in range(1, args.max_rounds + 1):
            dev_path = folder / f"claude-{round_no}.json"
            note("claude_incaricato", round_no)
            send(args.claude, developer_prompt(args.task, issues, dev_path, run_id, round_no))
            dev = wait(dev_path, run_id, round_no, args.timeout, args.poll)
            if dev.get("status") != "ready":
                state["status"] = "blocked"
                note("claude_bloccato", round_no)
                break

            review_path = folder / f"codex-{round_no}.json"
            note("codex_incaricato", round_no)
            send(args.codex, reviewer_prompt(args.task, dev, review_path, run_id, round_no))
            review = wait(review_path, run_id, round_no, args.timeout, args.poll)
            status = review.get("status")
            if status == "pass":
                state["status"] = "passed"
                note("revisione_superata", round_no)
                break
            if status == "blocked":
                state["status"] = "blocked"
                note("codex_bloccato", round_no)
                break
            if status != "fix":
                raise ZionError(f"Risposta Codex inattesa: {status!r}")
            issues = compact_issues(review)
            if not issues:
                raise ZionError("Codex ha chiesto correzioni senza indicare problemi")
            digest = issue_digest(issues)
            if digest == previous_digest:
                state["status"] = "needs_human"
                note("stessi_errori_ripetuti", round_no)
                break
            previous_digest = digest
            if round_no == args.max_rounds:
                state["status"] = "needs_human"
                note("limite_revisioni_raggiunto", round_no)
        state["ended"] = now()
        atomic_json(folder / "state.json", state)
        print(f"[ZION] ESITO={state['status']} | report={folder / 'state.json'}")
        return 0 if state["status"] == "passed" else 2
    except (OSError, subprocess.SubprocessError, ValueError, ZionError) as exc:
        state["status"] = "error"
        state["error"] = str(exc)[:600]
        state["ended"] = now()
        atomic_json(folder / "state.json", state)
        raise ZionError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ZION: orchestrazione Claude/Codex tramite hcom")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="avvia un lavoro con revisione automatica")
    run.add_argument("task", help="istruzione da assegnare a Claude")
    run.add_argument("--project", default=".", help="radice della repository da modificare")
    run.add_argument("--claude", default="claude", help="nome agente trovato con hcom list")
    run.add_argument("--codex", default="codex", help="nome agente trovato con hcom list")
    run.add_argument("--max-rounds", type=int, default=3, choices=(1, 2, 3))
    run.add_argument("--timeout", type=int, default=1800, help="secondi massimi per rapporto")
    run.add_argument("--poll", type=float, default=2.0)
    run.add_argument("--allow-dirty", action="store_true", help="permetti modifiche Git preesistenti")
    run.add_argument("--dry-run", action="store_true", help="mostra il primo prompt, senza inviarlo")
    status = sub.add_parser("status", help="leggi stato ultima esecuzione")
    status.add_argument("--project", default=".")
    doctor = sub.add_parser("doctor", help="controlla requisiti locali")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            for tool in ("git", "hcom", "claude", "codex"):
                print(f"{tool}: {'OK' if shutil.which(tool) else 'NON TROVATO'}")
            return 0
        if args.command == "status":
            root = Path(args.project).expanduser().resolve() / ".zion" / "runs"
            paths = sorted(root.glob("*/state.json")) if root.exists() else []
            if not paths:
                print("Nessuna esecuzione registrata")
                return 1
            data = json.loads(paths[-1].read_text(encoding="utf-8"))
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.timeout < 10 or args.poll <= 0 or args.poll > 60:
            raise ZionError("Imposta --timeout >= 10 e 0 < --poll <= 60")
        return workflow(args)
    except (ZionError, OSError, subprocess.SubprocessError) as exc:
        print(f"[ZION] ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
