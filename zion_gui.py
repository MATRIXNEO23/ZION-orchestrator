#!/usr/bin/env python3
"""ZION: GUI leggera Windows con tre pannelli ed errori in finestra separata.

Non chiama Claude/Codex fino alla conferma. I log restano sul PC.
"""
from __future__ import annotations
import json, os, queue, re, shutil, subprocess, sys, threading, time, traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def locate_path():
    """Combina PATH della sessione, utente e directory uv/npm."""
    parts = [os.environ.get("PATH", ""), os.environ.get("APPDATA", "") + r"\npm",
             str(Path.home() / ".local" / "bin"), os.environ.get("UV_TOOL_BIN_DIR", ""),
             os.path.join(os.environ.get("ProgramFiles", ""), "Git", "cmd"),
             os.path.join(os.environ.get("ProgramFiles", ""), "nodejs")]
    if os.name == "nt":
        for scope in ("Machine", "User"):
            try:
                p = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command",
                     "[Environment]::GetEnvironmentVariable('Path','" + scope + "')"],
                    capture_output=True, text=True, timeout=10)
                if p.returncode == 0:
                    parts.append(p.stdout.strip())
            except (OSError, subprocess.TimeoutExpired):
                pass
    path = os.pathsep.join(p for p in parts if p)
    uv = shutil.which("uv", path=path)
    if uv:
        try:
            p = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True,
                               text=True, timeout=10, env={**os.environ, "PATH": path})
            location = p.stdout.strip().splitlines()
            if p.returncode == 0 and location and Path(location[0]).is_dir():
                path += os.pathsep + location[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return path


def agent_name(text):
    """Estrae un nome affidabile dal JSON di hcom, non lo indovina."""
    for line in reversed(text.splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        candidates = [parsed]
        if isinstance(parsed, dict):
            candidates += [parsed.get(k) for k in ("agent", "instance", "result", "data")]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            for field in ("name", "agent_name", "instance_name"):
                value = entry.get(field)
                if isinstance(value, str) and NAME.fullmatch(value):
                    return value
    return None


def error_hint(message):
    low = message.lower()
    if "database is locked" in low:
        return ("Database SQLite occupato: potrebbero esserci altri processi Codex/hcom. "
                "Chiudi solo le sessioni aggiuntive non necessarie. ZION non elimina database.")
    if "app server closed" in low or "codex integration" in low:
        return ("L'integrazione Codex App Server si interrompe. L'avvio di 'codex' "
                "da solo non prova che la comunicazione hcom funzioni.")
    if "non trovato" in low or "not found" in low:
        return "Componente non trovato. Controlla INSTALLA_ZION.bat e il PATH."
    return "Controlla le righe precedenti e il log completo nella cartella logs."


class ZionApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ZION — Claude · Orchestratore · Codex")
        self.geometry("1160x710")
        self.minsize(950, 620)
        self.configure(bg="#101721")
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.child = None
        self.events_child = None
        self.logfile = LOG_DIR / ("zion_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
        self.errors = []
        self.error_window = None
        self.error_view = None
        self.project = tk.StringVar()
        self.current = tk.StringVar(value="In attesa")
        self.agents = {"claude": "claude", "codex": "codex"}
        self.motion = {"codex": (0.0, 0), "claude": (0.0, 0)}
        self.create_ui()
        self.after(85, self.drain)
        self.after(70, self.animate)
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.log("zion", "Pronto. Nessuna richiesta inviata alle IA.")

    def create_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Z.TButton", padding=(9, 7))
        top = tk.Frame(self, bg="#101721")
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="Repository Git", fg="#d8e7ef", bg="#101721").pack(side="left")
        ttk.Entry(top, textvariable=self.project).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="Sfoglia", command=self.browse).pack(side="left")
        ttk.Button(top, text="Errori e diagnostica", command=self.show_errors).pack(side="left", padx=7)

        body = tk.Frame(self, bg="#101721")
        body.pack(fill="both", expand=True, padx=12)
        for col in (0, 2, 4):
            body.grid_columnconfigure(col, weight=1, uniform="panes")
        body.grid_columnconfigure(1, minsize=65)
        body.grid_columnconfigure(3, minsize=65)
        body.grid_rowconfigure(0, weight=1)
        self.textboxes = {}
        for index, role, title in ((0, "codex", "CODEX · Revisione"),
                                   (2, "zion", "ZION · Coordinamento"),
                                   (4, "claude", "CLAUDE · Sviluppo")):
            frame = tk.Frame(body, bg="#182637", highlightthickness=1,
                             highlightbackground="#365066")
            frame.grid(row=0, column=index, sticky="nsew")
            tk.Label(frame, text=title, bg="#182637", fg="#e6f2f5",
                     font=("Segoe UI", 11, "bold"), pady=9).pack(fill="x")
            text = scrolledtext.ScrolledText(frame, state="disabled", wrap="word",
                                             bg="#0f1a26", fg="#d6e7f1",
                                             font=("Consolas", 9), relief="flat",
                                             padx=8, pady=8)
            text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            self.textboxes[role] = text
        self.canvas = {}
        for idx, key in ((1, "codex"), (3, "claude")):
            c = tk.Canvas(body, bg="#101721", highlightthickness=0, width=66)
            c.grid(row=0, column=idx, sticky="nsew")
            self.canvas[key] = c

        lower = tk.Frame(self, bg="#101721")
        lower.pack(fill="x", padx=12, pady=12)
        tk.Label(lower, text="Compito da assegnare", bg="#101721", fg="#d8e7ef").pack(anchor="w")
        self.task = tk.Text(lower, height=3, wrap="word", bg="#182637",
                            fg="#f5f7f9", relief="flat", font=("Segoe UI", 10))
        self.task.pack(fill="x", pady=(5, 9))
        bar = tk.Frame(lower, bg="#101721")
        bar.pack(fill="x")
        self.go = ttk.Button(bar, text="Avvia lavoro", command=self.start)
        self.go.pack(side="left")
        self.stop_button = ttk.Button(bar, text="Ferma ZION", command=self.stop_work,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=8)
        tk.Label(bar, textvariable=self.current, bg="#101721", fg="#86cbdf").pack(side="left", padx=12)
        ttk.Button(bar, text="Cartella log", command=self.open_log_folder).pack(side="right")

    def browse(self):
        value = filedialog.askdirectory(title="Seleziona la cartella principale Git")
        if value:
            self.project.set(value)

    def open_log_folder(self):
        if os.name == "nt":
            os.startfile(str(LOG_DIR))
        else:
            messagebox.showinfo("Cartella log", str(LOG_DIR))

    def show_errors(self):
        if self.error_window and self.error_window.winfo_exists():
            self.error_window.lift()
            return
        win = tk.Toplevel(self)
        win.title("ZION — Registro errori e diagnostica")
        win.geometry("850x500")
        win.configure(bg="#101721")
        self.error_window = win
        tk.Label(win, text="Errori e diagnostica · aggiornamento automatico",
                 bg="#101721", fg="#f5f7f9",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=10)
        txt = scrolledtext.ScrolledText(win, wrap="word", bg="#0f1a26",
                                        fg="#f1bfc0", font=("Consolas", 9), relief="flat")
        txt.pack(fill="both", expand=True, padx=12)
        for line in self.errors:
            txt.insert("end", line + "\n")
        txt.configure(state="disabled")
        self.error_view = txt
        ttk.Button(win, text="Apri cartella log", command=self.open_log_folder).pack(
            anchor="e", padx=12, pady=10)

    def log(self, source, message, is_error=False):
        text = str(message).strip()[:3800]
        entry = f"[{datetime.now():%H:%M:%S}] [{source.upper()}] {text}"
        with self.logfile.open("a", encoding="utf-8") as stream:
            stream.write(entry + "\n")
        view = self.textboxes.get(source, self.textboxes["zion"])
        view.configure(state="normal")
        view.insert("end", entry + "\n")
        if int(view.index("end-1c").split(".")[0]) > 430:
            view.delete("1.0", "50.0")
        view.see("end")
        view.configure(state="disabled")
        if is_error:
            self.errors.append(entry)
            self.errors = self.errors[-1600:]
            if self.error_window and self.error_window.winfo_exists():
                self.error_view.configure(state="normal")
                self.error_view.insert("end", entry + "\n")
                self.error_view.see("end")
                self.error_view.configure(state="disabled")

    def post(self, event, *values):
        self.messages.put((event, values))

    def drain(self):
        for _ in range(110):
            try:
                event, data = self.messages.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                self.log(*data)
            elif event == "error":
                self.log("zion", data[0], is_error=True)
                self.log("zion", error_hint(data[0]), is_error=True)
                self.current.set("Errore, apri diagnostica")
                self.show_errors()
            elif event == "status":
                self.current.set(data[0])
            elif event == "arrow":
                self.motion[data[0]] = (time.monotonic(), data[1])
            elif event == "done":
                self.running = False
                self.go.configure(state="normal")
                self.stop_button.configure(state="disabled")
        self.after(85, self.drain)

    def animate(self):
        moment = time.monotonic()
        for role, canvas in self.canvas.items():
            canvas.delete("all")
            width = max(30, canvas.winfo_width())
            mid = max(30, canvas.winfo_height()) / 2
            x1, x2 = 8, width - 8
            canvas.create_line(x1, mid, x2, mid, fill="#455f71", width=2, arrow=tk.BOTH)
            started, direction = self.motion[role]
            elapsed = moment - started
            if elapsed < 2:
                phase = (elapsed * 1.4) % 1
                # Codex è a sinistra, Claude a destra.
                right = (direction == 1)
                x = x1 + (x2 - x1) * (phase if right else (1-phase))
                canvas.create_line(x-9, mid, x+9, mid, fill="#6fcce4",
                                   width=3, arrow=tk.LAST if right else tk.FIRST,
                                   arrowshape=(9, 10, 4))
            canvas.create_text(width/2, mid+20, text="scambio" if elapsed < 2 else "attesa",
                               fill="#8babb9", font=("Segoe UI", 8))
        self.after(70, self.animate)

    def start(self):
        if self.running:
            return
        folder = Path(self.project.get().strip().strip('"')).expanduser()
        instruction = self.task.get("1.0", "end").strip()
        if not folder.is_dir():
            messagebox.showerror("ZION", "Scegli una cartella Git valida.")
            return
        if not instruction:
            messagebox.showerror("ZION", "Scrivi prima un compito.")
            return
        if not messagebox.askyesno(
                "Conferma", "Avviare Claude e Codex su questa repository?\n\n"
                "Le richieste ai modelli consumano quota. Non vengono "
                "eseguiti commit o push automatici."):
            return
        self.running = True
        self.cancel.clear()
        self.current.set("Avvio agenti...")
        self.go.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(target=self.work, args=(folder.resolve(), instruction),
                         daemon=True).start()

    def command(self, argv, env, cwd, timeout=90):
        self.post("log", "zion", "Comando: " + " ".join(argv[:3]))
        try:
            proc = subprocess.run(argv, env=env, cwd=cwd, text=True,
                                  encoding="utf-8", errors="replace",
                                  capture_output=True, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Timeout {timeout}s: {argv[0]} {argv[1]}") from exc
        if proc.returncode:
            raise RuntimeError(f"{argv[0]} errore {proc.returncode}:\n"
                               f"{proc.stderr[-3000:]}\n{proc.stdout[-1200:]}")
        return proc.stdout

    def work(self, folder, task):
        try:
            env = {**os.environ, "PATH": locate_path()}
            for name in ("git", "hcom", "claude", "codex"):
                if not shutil.which(name, path=env["PATH"]):
                    raise RuntimeError(f"{name} non trovato. Esegui INSTALLA_ZION.bat.")
            repo = self.command(["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
                                env, folder, 15).strip()
            if Path(repo).resolve() != folder:
                raise RuntimeError(f"Seleziona la radice del repository: {repo}")
            dirty = self.command(["git", "-C", str(folder), "status", "--porcelain"],
                                 env, folder, 20)
            if any(not line[3:].replace("\\", "/").startswith(".zion/")
                   for line in dirty.splitlines()):
                raise RuntimeError("Ci sono modifiche locali non salvate: "
                                   "ZION si ferma per evitare sovrascritture.")
            # Avvio sequenziale per ridurre gli accessi contemporanei a SQLite.
            for kind in ("codex", "claude"):
                if self.cancel.is_set():
                    return
                self.post("status", f"Avvio {kind}...")
                self.post("log", kind, "Avvio tramite hcom --headless.")
                output = self.command(
                    ["hcom", kind, "--headless", "--json", "--dir", str(folder)],
                    env, folder, 90)
                name = agent_name(output)
                if not name:
                    raise RuntimeError(f"hcom non ha restituito un nome per {kind}. "
                                       f"Output: {output[-1200:]}")
                self.agents[kind] = name
                self.post("log", kind, f"Agente pronto: {name}")
                time.sleep(1.5)
            if self.cancel.is_set():
                return
            self.post("status", "Coordinamento in corso")
            self.listen_events(env, folder)
            # Se la GUI usa pythonw.exe, lanciare il motore con python.exe:
            # pythonw non garantisce stdout/stderr leggibili dal coordinatore.
            engine = sys.executable
            if Path(engine).name.lower() in ("pythonw.exe", "pyw.exe"):
                candidate = str(Path(engine).with_name("python.exe"))
                engine = candidate if Path(candidate).is_file() else (
                    shutil.which("python", path=env["PATH"]) or engine)
            args = [engine, str(BASE / "zion.py"), "run", task,
                    "--project", str(folder), "--codex", self.agents["codex"],
                    "--claude", self.agents["claude"]]
            self.child = subprocess.Popen(
                args, cwd=BASE, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for raw in self.child.stdout:
                line = raw.strip()
                if not line:
                    continue
                is_err = any(word in line.lower() for word in (
                    "errore", "error", "database is locked", "preflight failed", "timeout"))
                self.post("log", "zion", line, is_err)
                if "claude_incaricato" in line:
                    self.post("arrow", "claude", 1)
                    self.post("log", "claude", "Incarico ricevuto")
                elif "claude_rapporto_ricevuto" in line or "claude_bloccato" in line:
                    self.post("arrow", "claude", -1)
                elif "codex_incaricato" in line:
                    self.post("arrow", "codex", -1)
                    self.post("log", "codex", "Revisione richiesta")
                elif "codex_rapporto_ricevuto" in line or "revisione_superata" in line:
                    self.post("arrow", "codex", 1)
            code = self.child.wait()
            if self.cancel.is_set():
                self.post("status", "Coordinamento interrotto")
            elif code == 0:
                self.post("status", "Lavoro concluso")
            else:
                raise RuntimeError(f"Coordinamento interrotto, codice {code}. "
                                   "Vedi anche .zion/runs nel progetto.")
        except Exception as exc:
            self.post("error", str(exc))
        finally:
            self.stop_events()
            self.child = None
            self.post("done")

    def listen_events(self, env, folder):
        def loop():
            try:
                self.events_child = subprocess.Popen(
                    ["hcom", "events", "--follow"], cwd=folder, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for raw in self.events_child.stdout:
                    if self.cancel.is_set():
                        break
                    if len(raw) > 12000:
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    agent = str(event.get("agent") or event.get("agent_name") or
                                event.get("name") or "")
                    label = str(event.get("type") or event.get("event") or "evento")
                    summary = str(event.get("summary") or event.get("status") or "")[:300]
                    for kind in ("codex", "claude"):
                        if agent == self.agents[kind] or kind in agent.lower():
                            self.post("log", kind, label + (" · " + summary if summary else ""))
                            break
            except OSError as exc:
                self.post("log", "zion", f"Monitor eventi hcom non disponibile: {exc}")
        threading.Thread(target=loop, daemon=True).start()

    def stop_events(self):
        proc = self.events_child
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.events_child = None

    def stop_work(self):
        if not self.running:
            return
        if not messagebox.askyesno("Ferma ZION",
                "Fermare il coordinamento?\nLe sessioni degli agenti "
                "potrebbero rimanere attive e vanno controllate separatamente."):
            return
        self.cancel.set()
        if self.child and self.child.poll() is None:
            self.child.terminate()
        self.post("log", "zion", "Interruzione richiesta dall'utente.")
        self.post("status", "Arresto richiesto")

    def close_app(self):
        if self.running and not messagebox.askyesno(
                "Uscita", "Il coordinamento è in corso. Vuoi interromperlo e uscire?"):
            return
        self.cancel.set()
        if self.child and self.child.poll() is None:
            self.child.terminate()
        self.stop_events()
        self.destroy()


def main():
    try:
        ZionApp().mainloop()
    except Exception:
        with (LOG_DIR / "zion_gui_crash.log").open("a", encoding="utf-8") as out:
            out.write(datetime.now().isoformat() + "\n" + traceback.format_exc() + "\n")
        raise


if __name__ == "__main__":
    main()
