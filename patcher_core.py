"""Shared job model and process controller. The original patch engines stay isolated."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
import zipfile

VERSION = "1.2"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_ROOT
EVENT_PREFIX = "@@KH_GUI@@"
GAMES = {
    "kh1": {"name": "Kingdom Hearts I Final Mix", "short": "KH1", "id": "010090C022978001", "engine": "KH1 v2"},
    "kh2": {"name": "Kingdom Hearts II Final Mix", "short": "KH2", "id": "010090C022978003", "engine": "KH2 v9"},
}


def clean_path(value):
    return Path(str(value).strip().strip('"')).expanduser().resolve()


@dataclass
class JobSpec:
    game: str = "kh2"
    action: str = "build"
    mode: str = "all"
    voice_language: str = "de"
    romfs: str = ""
    mod: str = ""
    movies: str = ""
    output_base: str = ""
    tools: str = ""

    @property
    def needs_voices(self):
        return self.game == "kh1" or self.mode in ("all", "voices")

    @property
    def needs_movies(self):
        return self.game == "kh2" and self.mode in ("all", "movies")

    def validate(self):
        if self.game not in GAMES or self.action not in ("build", "diagnose"):
            raise ValueError("Select a game and a valid action.")
        if self.mode not in ("all", "voices", "movies") or (self.game == "kh1" and self.mode != "voices"):
            raise ValueError("KH1 supports voice mods only.")
        if self.voice_language not in ("de", "ja"):
            raise ValueError("Select German or Japanese as the voice source.")
        if not self.romfs.strip() or not clean_path(self.romfs).is_dir():
            raise ValueError("Select the extracted ROMFS folder for the chosen game.")
        inputs = [clean_path(self.romfs)]
        if self.needs_voices and (self.action == "build" or self.game == "kh2"):
            if not self.mod.strip() or not clean_path(self.mod).exists():
                raise ValueError("Select the voice mod ZIP or extracted mod folder.")
            inputs.append(clean_path(self.mod))
        if self.needs_movies:
            if not self.movies.strip() or not clean_path(self.movies).exists():
                raise ValueError("Select the German movies (STEAM) ZIP or its extracted folder.")
            inputs.append(clean_path(self.movies))
        if not self.output_base.strip():
            raise ValueError("Select an output folder.")
        base = clean_path(self.output_base)
        if base.exists() and not base.is_dir():
            raise ValueError("The output path must be a folder.")
        for source in inputs:
            if source.is_dir() and base.is_relative_to(source):
                raise ValueError("The output folder must be outside ROMFS and mod folders.")
        return self


def find_tools(selected="", need_probe=True):
    folders = [clean_path(selected)] if selected.strip() else [APP_ROOT / "bin", RESOURCE_ROOT / "bin"]
    found = {}
    for name in ("ffmpeg", "ffprobe") if need_probe else ("ffmpeg",):
        candidate = next((folder / (name + suffix) for folder in folders for suffix in (".exe", "")
                          if (folder / (name + suffix)).is_file()), None)
        if candidate is None and not selected.strip():
            candidate = shutil.which(name)
        if candidate is None:
            raise ValueError(name + " is missing. Retry automatic setup in the app or choose a tools folder.")
        found[name] = str(Path(candidate).resolve())
    return found


class ProgressTracker:
    """Progress follows emitted work steps; it never estimates completion from time."""
    def __init__(self, spec):
        self.spec, self.value, self.archive_count = spec, 2, 0
        self.label = "Checking inputs"

    def consume(self, line):
        combined = self.spec.game == "kh2" and self.spec.mode == "all"
        value, label = self.value, self.label
        if "[1/3]" in line:
            value, label = 5, "Checking voices"
        elif "[2/3]" in line:
            value, label = 72, "Checking and building German movies"
        elif "[3/3]" in line:
            value, label = 97, "Preparing combined output"
        elif "Native Patchdateien geprüft:" in line or "Vorprüfung:" in line or "Moddateien geprüft:" in line:
            match = re.search(r"(\d+)/(\d+)", line)
            if match and int(match[2]):
                value = 7 + 32 * int(match[1]) / int(match[2])
                label = "Checking voices · " + match[1] + " / " + match[2]
        elif "Sprachformat anpassen:" in line:
            label = "Adjusting voice format · " + line.rsplit("/", 1)[-1]
        elif "Native Stimmen und Ressourcen bauen:" in line:
            self.archive_count += 1
            value = 40 + min(20, self.archive_count * 3.3)
            label = "Building archive · " + line.split(":", 1)[1].strip()
        elif self.spec.game == "kh1" and "Switch-Archiv bauen" in line:
            match = re.search(r"\[(\d+)/4\] (\w+)", line)
            if match:
                value, label = 12 + 16 * (int(match[1]) - 1), "Building archive · " + match[2]
        elif "Ressourcen prüfen" in line or "Originaldaten prüfen" in line:
            value, label = (66 if combined else 91), "Checking patched files and original data"
        elif self.spec.needs_movies and re.match(r"\[\d+/12\]", line):
            match = re.match(r"\[(\d+)/12\]\s*(.*)", line)
            start = 72 if combined else 8
            value = start + (95 - start) * (int(match[1]) - 1) / 12
            movie_step = match[2].replace(" prüfen ...", "").replace(": ", " · ")
            label = "Movies · " + match[1] + " / 12 · " + movie_step
        elif "FFmpeg arbeitet weiter" in line:
            label = "Converting movie · FFmpeg is working"
        self.value = min(98, max(self.value, value))
        self.label = label
        return {"type": "progress", "value": self.value, "label": label}


def diagnostic_bundle(job_root):
    """Include reports and existing small diagnostic ZIPs, never full game archives."""
    target = job_root / "KH-Diagnose.zip"
    temp = target.with_suffix(".zip.tmp")
    names = {"KH1-Switch-Diagnose.zip", "KH2-Switch-Diagnose.zip", "KH2-Gesamt-Diagnose.zip", "KH2-Film-Diagnose.zip"}
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(job_root.rglob("*")):
            if not path.is_file() or path.is_symlink() or "atmosphere" in path.relative_to(job_root).parts:
                continue
            if ((path.suffix.lower() in (".json", ".log", ".txt") and path.stat().st_size <= 16 * 1024**2) or
                    (path.name in names and path.stat().st_size <= 64 * 1024**2)):
                archive.write(path, path.relative_to(job_root))
    temp.replace(target)
    return target


def worker_command(request):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", str(request)]
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe" and python.with_name("python.exe").is_file():
        python = python.with_name("python.exe")
    return [str(python), "-u", str(RESOURCE_ROOT / "worker.py"), str(request)]


class JobRunner:
    def __init__(self):
        self.events = queue.Queue()
        self.process = None
        self.thread = None
        self.job_root = None
        self.cancel_requested = threading.Event()
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, spec):
        if self.running:
            raise ValueError("A job is already running.")
        spec.validate()
        # Freeze the job before the UI can change its state.
        request = asdict(spec)
        self.cancel_requested.clear()
        self.job_root = None
        self.thread = threading.Thread(target=self._run, args=(request,), daemon=True)
        self.thread.start()

    def _run(self, request):
        result = {"success": False, "cancelled": False}
        root = None
        try:
            spec = JobSpec(**request)
            base = clean_path(spec.output_base)
            base.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            root = base / ("KH-Patch-" + GAMES[spec.game]["short"] + "-" + stamp + "-" + uuid.uuid4().hex[:6])
            root.mkdir()
            self.job_root = root
            logs = root / "Berichte-GUI"
            logs.mkdir()
            request.update(job_root=str(root))
            request_file = logs / "Auftrag.json"
            request_file.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
            self.events.put({"type": "started", "root": str(root)})
            tracker = ProgressTracker(spec)
            options = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
            environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            with (logs / "GUI-Protokoll.log").open("w", encoding="utf-8") as log:
                with self.lock:
                    if self.cancel_requested.is_set():
                        raise InterruptedError("Job cancelled.")
                    self.process = subprocess.Popen(worker_command(request_file), stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                                    env=environment, **options)
                for line in self.process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith(EVENT_PREFIX):
                        try:
                            event = json.loads(line[len(EVENT_PREFIX):])
                            self.events.put(event)
                            continue
                        except ValueError:
                            pass
                    self.events.put({"type": "log", "text": line.rstrip("\r\n")})
                    self.events.put(tracker.consume(line.strip()))
                code = self.process.wait()
            result_path = root / "Ergebnis.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            if code != 0 or not result.get("success"):
                result["success"] = False
                result.setdefault("error", "The patcher exited with code " + str(code) + " beendet.")
            if self.cancel_requested.is_set() and not result.get("success"):
                result.update(cancelled=True, error="Job cancelled. The input files were left unchanged.")
        except Exception as error:
            result.update(success=False, cancelled=self.cancel_requested.is_set(), error=str(error))
        finally:
            with self.lock:
                self.process = None
            if root:
                try:
                    # A cancellation can race the worker's final rename. Keep even
                    # a fully written tree out of the published location on failure.
                    ready = root / "atmosphere"
                    if not result.get("success") and ready.exists():
                        quarantine = root / "Berichte" / "Nicht-freigegeben"
                        quarantine.mkdir(parents=True, exist_ok=True)
                        ready.rename(quarantine / "atmosphere")
                    (root / "Ergebnis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    result["diagnostic"] = str(diagnostic_bundle(root))
                except OSError as error:
                    result["diagnostic_error"] = str(error)
                result["root"] = str(root)
            self.events.put({"type": "finished", **result})

    def cancel(self):
        self.cancel_requested.set()
        if self.job_root:
            (self.job_root / "Abbrechen.flag").touch()
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=0x08000000, timeout=15)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def settings_file():
    base = Path(os.environ.get("APPDATA", str(Path.home() / ".config")))
    return base / "KingdomHeartsSwitchPatcher" / "settings.json"


def load_settings():
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
