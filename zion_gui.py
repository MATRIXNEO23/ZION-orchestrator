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
from zion_sessions import OwnedHcomAgents, blocked_status, visible_status

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def locate_path():
    """Combina PATH della sessione, utente e directory uv/npm."""
    parts = [os.environ.get("PATH", ""), os.environ.get("APPDATA", "") + r"\npm",
             str(Path.home() / ".local" / "bin"), os.environ.get("UV_TOOL_BIN_DIR", ""),
             os.path.join(os.environ.get("ProgramFiles", ""), "Git", "cmd"),
             os.path.join(os.environ.get("ProgramFiles", ""), "nodejs"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "cmd")]
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
    """Estrae il nome restituito da hcom, incluse le risposte JSON --headless."""
    for line in reversed(text.splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        # hcom 0.7.31: {"action":"launch", "launched":1,
        #               "failed":0, "errors":[], "names":["zumi"], ...}
        # La GUI avvia sempre un solo agente: non scegliere un nome a caso.
        if parsed.get("action") == "launch":
            names = parsed.get("names")
            if (parsed.get("launched") == 1 and parsed.get("failed") == 0
                    and not parsed.get("errors") and isinstance(names, list)
                    and len(names) == 1 and isinstance(names[0], str)
                    and NAME.fullmatch(names[0])):
                return names[0]
            return None
        candidates = [parsed]
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
    if "winerror 2" in low or "file not found" in low or "no such file" in low:
        return ("Windows non riesce ad avviare un eseguibile. Il comando e il percorso "
                "sono riportati nella diagnostica; non reinstallare Codex alla cieca.")
    if "non trovato" in low or "not found" in low:
        return "Componente non trovato. Controlla INSTALLA_ZION.bat e il PATH."
    return "Controlla le righe precedenti e il log completo nella cartella logs."



def warm_codex_app_server(codex_binary, folder, timeout=40):
    """Warm up Codex app-server on cold Windows installs, without model calls.

    hcom 0.7.31 gives each app-server reply a fixed 10 seconds. On slower PCs
    the first hooks/list may take longer, while subsequent requests are fast.
    Use exactly the protocol methods needed for a harmless startup probe.
    """
    argv = [str(codex_binary), "-c", "features.hooks=true",
            "app-server", "--listen", "stdio://"]
    if os.name == "nt" and str(codex_binary).lower().endswith((".cmd", ".bat")):
        argv = ["cmd.exe", "/d", "/c"] + argv
    proc = subprocess.Popen(
        argv, cwd=str(folder), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    messages = queue.Queue()
    stderr_lines = []

    def read_stdout():
        try:
            for line in proc.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    def read_stderr():
        for line in proc.stderr:
            if len(stderr_lines) < 20:
                stderr_lines.append(line.strip()[:250])

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + timeout

    def rpc(payload):
        start = time.monotonic()
        proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        proc.stdin.flush()
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                break
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if isinstance(result, dict) and result.get("id") == payload["id"]:
                if result.get("error"):
                    raise RuntimeError(
                        f"Codex app-server {payload['method']}: "
                        f"{str(result['error'])[:350]}")
                return time.monotonic() - start
        extra = (" | stderr: " + " / ".join(stderr_lines[-3:])) if stderr_lines else ""
        raise RuntimeError(
            f"Codex app-server {payload['method']} senza risposta entro "
            f"{timeout}s{extra}")

    try:
        first = rpc({
            "method": "initialize", "id": 1,
            "params": {
                "clientInfo": {"name": "zion", "title": "Zion preflight",
                               "version": "0.2.0"},
                "capabilities": {"experimentalApi": True},
            },
        })
        proc.stdin.write('{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        second = rpc({"method": "hooks/list", "id": 2,
                      "params": {"cwds": [str(folder)]}})
        return first, second
    finally:
        # Close stdin first: Codex should exit when the protocol closes.
        # Killing only cmd.exe can otherwise leave a child with an open cwd.
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, text=True, timeout=8, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=4)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


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
        self.sessions = OwnedHcomAgents()
        self.worker_thread = None
        self.monitor_stop = threading.Event()
        self.closing = False
        self.close_cleanup_thread = None
        self.agent_sent_at = {}
        self.agent_hcom_state = {}
        self.agent_labels = {}
        self.logfile = LOG_DIR / ("zion_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
        self.errors = []
        self.error_window = None
        self.error_view = None
        self.project = tk.StringVar()
        self.current = tk.StringVar(value="In attesa")
        self.agents = {"claude": "claude", "codex": "codex"}
        self.motion = {"codex": (0.0, 0), "claude": (0.0, 0)}
        self.create_ui()
        self._drain_timer = self.after(85, self.drain)
        self._animate_timer = self.after(70, self.animate)
        self._label_timer = self.after(1000, self.refresh_agent_labels)
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
            if role in ("claude", "codex"):
                label_var = tk.StringVar(value="hcom: non avviato")
                self.agent_labels[role] = label_var
                tk.Label(frame, textvariable=label_var, bg="#182637",
                         fg="#a4c5d3", font=("Segoe UI", 9),
                         anchor="w").pack(fill="x", padx=8, pady=(0, 6))
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
        text = str(message).strip()[:8000]
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
        # Mostra anche i comandi precedenti, non soltanto la riga di errore.
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
            elif event == "agent_sent":
                self.agent_sent_at[data[0]] = time.monotonic()
            elif event == "agent_health":
                role, health = data
                if self.agent_hcom_state.get(role) != health:
                    self.agent_hcom_state[role] = health
                    self.log(role, "Stato rilevato da hcom: " + health)
            elif event == "done":
                self.running = False
                self.go.configure(state="normal")
                self.stop_button.configure(state="disabled")
        self._drain_timer = self.after(85, self.drain)

    def refresh_agent_labels(self):
        """Show observed hcom state and how long since the instruction was sent."""
        if self.closing:
            return
        for role, label_var in self.agent_labels.items():
            state = self.agent_hcom_state.get(role, "stato non ancora verificato")
            sent = self.agent_sent_at.get(role)
            if sent is None:
                label_var.set("hcom: " + state[:85])
            else:
                elapsed = max(0, int(time.monotonic() - sent))
                label_var.set(f"hcom: {state[:48]} | rapporto atteso da "
                              f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        self._label_timer = self.after(1000, self.refresh_agent_labels)

    def monitor_agents(self, stop_event):
        """Read actual agent state; report launch_blocked instead of waiting 30m."""
        blocked_counts = {}
        while not stop_event.is_set() and not self.cancel.is_set():
            try:
                statuses = self.sessions.statuses()
                for role, name in self.sessions.snapshot().items():
                    info = statuses.get(name)
                    if info is None:
                        continue
                    self.post("agent_health", role, visible_status(info))
                    if blocked_status(info):
                        blocked_counts[role] = blocked_counts.get(role, 0) + 1
                        if blocked_counts[role] >= 2:
                            self.post("error",
                                      f"{role} ({name}) risulta bloccato in hcom: "
                                      f"{visible_status(info)}. "
                                      "ZION interrompe l'attesa e chiude i propri agenti.")
                            self.cancel.set()
                            proc = self.child
                            if proc is not None and proc.poll() is None:
                                proc.terminate()
                            break
                    else:
                        blocked_counts[role] = 0
            except Exception as exc:
                self.post("log", "zion",
                          f"Monitor hcom momentaneamente non disponibile: {exc}")
            stop_event.wait(4)

    def cleanup_agents(self):
        """Stop only agents registered by this GUI, on success, stop, or close."""
        for role, name, success, detail in self.sessions.cleanup():
            if success:
                self.post("log", role, f"Sessione hcom {name} chiusa da ZION.")
            else:
                self.post("error",
                          f"Chiusura agente {role} ({name}) non riuscita: {detail}")

    def request_stop(self):
        self.cancel.set()
        self.monitor_stop.set()
        proc = self.child
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError as exc:
                self.post("log", "zion", f"Termine coordinatore: {exc}")

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
        self._animate_timer = self.after(70, self.animate)

    def start(self):
        if self.running or self.closing:
            return
        if self.sessions.snapshot():
            messagebox.showerror(
                "ZION", "Alcuni agenti del lavoro precedente non sono stati "
                "chiusi. Controlla la diagnostica prima di avviare altri agenti.")
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
        self.agent_sent_at.clear()
        self.agent_hcom_state.clear()
        self.monitor_stop = threading.Event()
        self.worker_thread = threading.Thread(
            target=self.work, args=(folder.resolve(), instruction),
            daemon=True)
        self.worker_thread.start()

    def command(self, argv, env, cwd, timeout=90):
        display = subprocess.list2cmdline([str(arg) for arg in argv])
        self.post("log", "zion", "Comando: " + display[:900])
        try:
            proc = subprocess.run(argv, env=env, cwd=cwd, text=True,
                                  encoding="utf-8", errors="replace",
                                  capture_output=True, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Timeout {timeout}s: {display[:900]}") from exc
        except OSError as exc:
            raise RuntimeError(f"Impossibile avviare il comando {display[:900]}: {exc}") from exc
        if proc.returncode:
            raise RuntimeError(f"{argv[0]} errore {proc.returncode}:\n"
                               f"{proc.stderr[-3000:]}\n{proc.stdout[-1200:]}")
        return proc.stdout

    def work(self, folder, task):
        try:
            env = {**os.environ, "PATH": locate_path()}
            binaries = {}
            for name in ("git", "hcom", "claude", "codex"):
                path = shutil.which(name, path=env["PATH"])
                if not path:
                    raise RuntimeError(f"{name} non trovato. Esegui INSTALLA_ZION.bat.")
                binaries[name] = str(Path(path).resolve())
            env["ZION_GIT_BINARY"] = binaries["git"]
            env["ZION_HCOM_BINARY"] = binaries["hcom"]
            self.sessions.configure(binaries["hcom"], env, folder)
            self.post("log", "zion", "Git: " + binaries["git"])
            self.post("log", "zion", "hcom: " + binaries["hcom"])
            repo = self.command([binaries["git"], "-C", str(folder), "rev-parse", "--show-toplevel"],
                                env, folder, 15).strip()
            if Path(repo).resolve() != folder:
                raise RuntimeError(f"Seleziona la radice del repository: {repo}")
            dirty = self.command([binaries["git"], "-C", str(folder), "status", "--porcelain"],
                                 env, folder, 20)
            if any(not line[3:].replace("\\", "/").startswith(".zion/")
                   for line in dirty.splitlines()):
                raise RuntimeError("Ci sono modifiche locali non salvate: "
                                   "ZION si ferma per evitare sovrascritture.")
            # Warm up Codex before hcom's 10-second hooks/list preflight.
            # This sends no model prompts and does not edit project files.
            if self.cancel.is_set():
                return
            self.post("status", "Preparo Codex (primo avvio)...")
            self.post("log", "zion", "Preavvio Codex app-server: nessuna chiamata ai modelli.")
            init_sec, hooks_sec = warm_codex_app_server(binaries["codex"], folder)
            self.post("log", "zion", f"App-server pronto: initialize={init_sec:.2f}s, "
                                    f"hooks/list={hooks_sec:.2f}s.")
            if self.cancel.is_set():
                return
            # Avvio sequenziale per ridurre gli accessi contemporanei a SQLite.
            for kind in ("codex", "claude"):
                if self.cancel.is_set():
                    return
                self.post("status", f"Avvio {kind}...")
                self.post("log", kind, "Avvio tramite hcom --headless.")
                output = self.command(
                    [binaries["hcom"], kind, "--headless", "--json", "--dir", str(folder)],
                    env, folder, 90)
                name = agent_name(output)
                if not name:
                    raise RuntimeError(f"hcom non ha restituito un nome per {kind}. "
                                       f"Output: {output[-1200:]}")
                self.sessions.register(kind, name)
                self.agents[kind] = name
                self.post("log", kind,
                          f"Processo agente avviato: {name}. Stato operativo da verificare.")
                time.sleep(1.5)
            if self.cancel.is_set():
                return
            self.post("status", "Coordinamento in corso")
            self.listen_events(env, folder, binaries["hcom"])
            threading.Thread(target=self.monitor_agents,
                             args=(self.monitor_stop,), daemon=True).start()
            if self.cancel.is_set():
                return
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
            if self.cancel.is_set():
                self.child.terminate()
            for raw in self.child.stdout:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("ZION_UI_EVENT "):
                    try:
                        event = json.loads(line[len("ZION_UI_EVENT "):])
                    except ValueError:
                        self.post("log", "zion", "Evento GUI malformato; consulta il log.")
                        continue
                    if not isinstance(event, dict) or event.get("agent") not in ("claude", "codex"):
                        continue
                    kind = event["agent"]
                    if event.get("type") == "prompt":
                        prompt = str(event.get("prompt", ""))[:5000]
                        self.post("log", kind,
                                  f"COMPITO PREPARATO PER {kind.upper()} (ciclo {event.get('round')}):\n"
                                  + prompt)
                        self.post("log", kind,
                                  "IN ATTESA DI RISCONTRO. L'invio non conferma "
                                  "che l'agente stia eseguendo il compito.")
                    elif event.get("type") == "report":
                        status = str(event.get("status", "non specificato"))[:32]
                        summary = str(event.get("summary", ""))[:500]
                        self.post("log", kind,
                                  f"RAPPORTO RICEVUTO: stato={status}; {summary}")
                    continue
                is_err = any(word in line.lower() for word in (
                    "errore", "error", "database is locked", "preflight failed", "timeout"))
                self.post("log", "zion", line, is_err)
                if "claude_incaricato" in line:
                    self.post("arrow", "claude", 1)
                    self.post("agent_sent", "claude")
                    self.post("log", "claude",
                              "Messaggio inviato a hcom. Esecuzione non ancora confermata.")
                elif "claude_rapporto_ricevuto" in line or "claude_bloccato" in line:
                    self.post("arrow", "claude", -1)
                elif "codex_incaricato" in line:
                    self.post("arrow", "codex", -1)
                    self.post("agent_sent", "codex")
                    self.post("log", "codex",
                              "Revisione inviata a hcom. Esecuzione non ancora confermata.")
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
            self.monitor_stop.set()
            self.stop_events()
            self.cleanup_agents()
            self.child = None
            self.post("done")

    def listen_events(self, env, folder, hcom_binary):
        def loop():
            try:
                self.events_child = subprocess.Popen(
                    [hcom_binary, "events", "--follow"], cwd=folder, env=env,
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
        if not self.running or self.closing:
            return
        if not messagebox.askyesno(
                "Ferma ZION",
                "Fermare il lavoro e chiudere anche gli agenti "
                "Claude/Codex avviati da questa finestra?"):
            return
        self.request_stop()
        self.post("log", "zion", "Arresto richiesto: chiusura dei propri agenti in corso.")
        self.post("status", "Arresto agenti in corso...")

    def destroy(self):
        # Cancel Tk timers explicitly, including when tests close a window.
        for attr in ("_drain_timer", "_animate_timer", "_label_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                try:
                    self.after_cancel(timer)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, attr, None)
        super().destroy()

    def close_app(self):
        if self.closing:
            return
        if self.running and not messagebox.askyesno(
                "Uscita", "Interrompere il lavoro e chiudere gli agenti "
                "Claude/Codex avviati da questa finestra?"):
            return
        self.closing = True
        self.request_stop()
        self.current.set("Chiusura agenti in corso...")
        self.go.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.after(180, self.finish_close)

    def finish_close(self):
        # Keep the Tk event loop alive until the launch thread has returned
        # and its finally block has cleaned up. Otherwise new agents could be
        # launched after the GUI had exited.
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.after(220, self.finish_close)
            return
        if self.sessions.snapshot() and self.close_cleanup_thread is None:
            self.close_cleanup_thread = threading.Thread(
                target=self.cleanup_agents, daemon=True)
            self.close_cleanup_thread.start()
        if self.close_cleanup_thread is not None and self.close_cleanup_thread.is_alive():
            self.after(220, self.finish_close)
            return
        if self.sessions.snapshot():
            if messagebox.askretrycancel(
                    "ZION — chiusura incompleta",
                    "Alcune sessioni di ZION non sono state chiuse. "
                    "Riprovare la chiusura automatica? "
                    "(Annulla chiude la finestra lasciando i processi attivi.)"):
                self.close_cleanup_thread = None
                self.after(180, self.finish_close)
                return
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
