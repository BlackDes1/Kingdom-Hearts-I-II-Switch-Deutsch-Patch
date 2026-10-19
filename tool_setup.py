"""Download and install the pinned Windows FFmpeg tools without blocking Tk."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import subprocess
import tempfile
import threading
import urllib.request
import zipfile

FFMPEG_VERSION = "9.0.2"
DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip"
DOWNLOAD_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
MAX_DOWNLOAD = 256 * 1024**2
MAX_EXECUTABLE = 512 * 1024**2
NAMES = ("ffmpeg.exe", "ffprobe.exe")


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Tool download cancelled.")


def validate_tools(folder):
    """Check both programs and the actual codecs needed by the KH2 engine."""
    folder = Path(folder)
    options = {"creationflags": 0x08000000} if os.name == "nt" else {}

    def run(name, *args):
        path = folder / name
        if not path.is_file():
            raise ValueError(name + " is missing.")
        try:
            result = subprocess.run([str(path), *args], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=30, **options)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(name + " could not be run: " + str(error)) from error
        if result.returncode:
            raise ValueError(name + " reported an error: " + (result.stderr or result.stdout)[-600:])
        return result.stdout + "\n" + result.stderr

    versions = {}
    for name in NAMES:
        output = run(name, "-version")
        if not output.startswith(name.removesuffix(".exe") + " version "):
            raise ValueError(name + " returned invalid version information.")
        versions[name] = output.splitlines()[0]
    decoders = run("ffmpeg.exe", "-hide_banner", "-decoders")
    encoders = run("ffmpeg.exe", "-hide_banner", "-encoders")
    for listing, codec, kind in ((decoders, "libvorbis", "Decoder"),
                                 (encoders, "libvorbis", "Encoder"),
                                 (encoders, "libx264", "Encoder")):
        if not re.search(r"(?m)^\s*\S{6}\s+" + codec + r"\b", listing):
            raise ValueError("FFmpeg: " + codec + "-" + kind + " is missing.")
    return {"folder": str(folder.resolve()), "versions": versions}


def download_archive(target, progress, cancel):
    check_cancel(cancel)
    progress({"phase": "download", "label": "Download FFmpeg", "value": 0})
    request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "KH-Switch-Patcher/1.1"})
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=20) as response:
        if not response.geturl().startswith("https://"):
            raise ValueError("The download source does not use HTTPS.")
        try:
            total = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            total = 0
        if total > MAX_DOWNLOAD:
            raise ValueError("The FFmpeg archive is larger than expected.")
        with Path(target).open("wb") as output:
            while True:
                check_cancel(cancel)
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_DOWNLOAD:
                    raise ValueError("The FFmpeg archive exceeds the allowed download size.")
                output.write(chunk)
                digest.update(chunk)
                progress({"phase": "download", "label": "Download FFmpeg",
                          "value": min(84, 84 * received / total) if total > 0 else 0,
                          "received": received, "total": total})
    check_cancel(cancel)
    if total > 0 and received != total:
        raise ValueError("The FFmpeg download is incomplete. Please retry.")
    if digest.hexdigest() != DOWNLOAD_SHA256:
        raise ValueError("The FFmpeg download SHA-256 checksum does not match. Nothing was installed.")


def unpack_archive(archive_path, staging, cancel):
    """Copy only selected members to fixed filenames, never extract ZIP paths."""
    staging = Path(staging)
    with zipfile.ZipFile(archive_path) as archive:
        for name in NAMES:
            check_cancel(cancel)
            matches = [item for item in archive.infolist()
                       if re.fullmatch(r"[^/]+/bin/" + re.escape(name), item.filename)]
            if len(matches) != 1:
                raise ValueError("The FFmpeg archive does not contain exactly one copy of " + name + ".")
            member = matches[0]
            if member.file_size < 2 or member.file_size > MAX_EXECUTABLE:
                raise ValueError("Invalid file size for " + name + ".")
            with archive.open(member) as source, (staging / name).open("wb") as output:
                if source.read(2) != b"MZ":
                    raise ValueError(name + " is not a Windows program.")
                output.write(b"MZ")
                while True:
                    check_cancel(cancel)
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        # Preserve the distributor's license and readme beside the programs.
        for member in archive.infolist():
            name = PurePosixPath(member.filename).name.lower()
            if name in ("license", "license.txt", "readme.txt") and member.file_size <= 1024**2:
                target = "FFmpeg-LICENSE.txt" if name.startswith("license") else "FFmpeg-README.txt"
                (staging / target).write_bytes(archive.read(member))


def publish_tools(staging, target):
    """Roll back a failed replacement so an existing pair remains usable."""
    staging, target = Path(staging), Path(target)
    files = [staging / name for name in NAMES]
    files += sorted(path for path in staging.iterdir() if path.name.startswith("FFmpeg-"))
    files.append(staging / "ffmpeg-download.json")
    backups, installed = [], []
    try:
        for source in files:
            destination = target / source.name
            if destination.exists():
                backup = staging / (source.name + ".previous")
                destination.replace(backup)
                backups.append((backup, destination))
            source.replace(destination)
            installed.append(destination)
    except OSError:
        for path in reversed(installed):
            path.unlink(missing_ok=True)
        for backup, destination in reversed(backups):
            backup.replace(destination)
        raise


def ensure_windows_tools(target, progress=lambda event: None, cancel=None):
    """Reuse a working bin pair offline; install a checked pair if it is missing."""
    target = Path(target).resolve()
    check_cancel(cancel)
    progress({"phase": "check", "label": "Checking FFmpeg and ffprobe", "value": 0})
    try:
        result = validate_tools(target)
    except ValueError as error:
        progress({"phase": "log", "text": "Setting up tools: " + str(error)})
    else:
        return dict(result, downloaded=False)
    check_cancel(cancel)
    target.mkdir(parents=True, exist_ok=True)
    # Same filesystem as bin: moving validated programs never copies partial files.
    with tempfile.TemporaryDirectory(prefix=".ffmpeg-setup-", dir=target) as directory:
        staging = Path(directory)
        archive = staging / "ffmpeg.zip.part"
        download_archive(archive, progress, cancel)
        progress({"phase": "extract", "label": "Checksum verified · extracting tools", "value": 87})
        unpack_archive(archive, staging, cancel)
        check_cancel(cancel)
        progress({"phase": "validate", "label": "Checking FFmpeg and required codecs", "value": 94})
        result = validate_tools(staging)
        check_cancel(cancel)
        metadata = {"version": FFMPEG_VERSION, "url": DOWNLOAD_URL, "sha256": DOWNLOAD_SHA256,
                    "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "versions": result["versions"], "license": "GPL-3.0-or-later"}
        (staging / "ffmpeg-download.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        publish_tools(staging, target)
    return dict(result, folder=str(target), downloaded=True)


class ToolSetup:
    """Own queue and cancellation: a download is independent of a patch job."""
    def __init__(self):
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, target):
        if self.running:
            return False
        self.cancel.clear()
        self.thread = threading.Thread(target=self._run, args=(Path(target),), daemon=True)
        self.thread.start()
        return True

    def _run(self, target):
        try:
            result = ensure_windows_tools(target, self.events.put, self.cancel)
            self.events.put({"phase": "finished", "success": True, **result})
        except Exception as error:
            self.events.put({"phase": "finished", "success": False,
                             "cancelled": self.cancel.is_set(), "error": str(error)})
