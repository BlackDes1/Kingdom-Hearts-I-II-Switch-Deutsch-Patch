"""Bootstrap failures must never publish incomplete or unverified executables."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tool_setup as setup


class Response(io.BytesIO):
    def __init__(self, data, length=None, url="https://example.test/tools.zip"):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
        self.url = url

    def geturl(self):
        return self.url


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "application with spaces" / "bin"
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, entries=None):
        data = io.BytesIO()
        if entries is None:
            entries = {"ffmpeg-9.0.2/bin/ffmpeg.exe": b"MZffmpeg fixture",
                       "ffmpeg-9.0.2/bin/ffprobe.exe": b"MZffprobe fixture",
                       "ffmpeg-9.0.2/LICENSE": b"GPL fixture",
                       "ffmpeg-9.0.2/README.txt": b"Readme fixture",
                       "../../outside.exe": b"must never be extracted"}
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():archive.writestr(name, content)
        return data.getvalue()

    def install(self, data, digest=None, validator=None, length=None):
        if validator is None:
            def validator(folder):
                if Path(folder)==self.target:raise ValueError("missing tools")
                return {"folder": str(folder), "versions": {"ffmpeg.exe": "ffmpeg version fixture", "ffprobe.exe": "ffprobe version fixture"}}
        with mock.patch.object(setup.urllib.request, "urlopen", return_value=Response(data, length)), \
             mock.patch.object(setup, "DOWNLOAD_SHA256", digest or hashlib.sha256(data).hexdigest()), \
             mock.patch.object(setup, "validate_tools", side_effect=validator):
            return setup.ensure_windows_tools(self.target, self.events.append)

    def assert_no_temporary_files(self):
        self.assertFalse(list(self.target.glob(".ffmpeg-setup-*")))

    def test_valid_pair_is_installed_together_with_license_and_source_metadata(self):
        result = self.install(self.archive())
        self.assertTrue(result["downloaded"])
        self.assertEqual(result["folder"], str(self.target))
        self.assertEqual((self.target/"ffmpeg.exe").read_bytes(), b"MZffmpeg fixture")
        self.assertEqual((self.target/"ffprobe.exe").read_bytes(), b"MZffprobe fixture")
        self.assertEqual((self.target/"FFmpeg-LICENSE.txt").read_bytes(), b"GPL fixture")
        metadata = json.loads((self.target/"ffmpeg-download.json").read_text())
        self.assertEqual(metadata["url"], setup.DOWNLOAD_URL)
        self.assertEqual(metadata["sha256"], hashlib.sha256(self.archive()).hexdigest())
        self.assertFalse((self.root/"outside.exe").exists())
        self.assert_no_temporary_files()

    def test_cached_tools_need_no_network_or_write_access(self):
        cached = {"folder": str(self.target), "versions": {"ffmpeg.exe": "already installed"}}
        with mock.patch.object(setup, "validate_tools", return_value=cached), \
             mock.patch.object(setup.urllib.request, "urlopen") as network:
            result = setup.ensure_windows_tools(self.target)
        self.assertFalse(result["downloaded"])
        network.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_hash_mismatch_preserves_existing_programs(self):
        self.target.mkdir(parents=True)
        for name in setup.NAMES:(self.target/name).write_bytes(b"original " + name.encode())
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.install(self.archive(), digest="0"*64)
        for name in setup.NAMES:self.assertEqual((self.target/name).read_bytes(), b"original " + name.encode())
        self.assert_no_temporary_files()

    def test_missing_probe_does_not_publish_partial_ffmpeg(self):
        data = self.archive({"release/bin/ffmpeg.exe": b"MZfixture"})
        with self.assertRaisesRegex(ValueError, "ffprobe.exe"):
            self.install(data)
        self.assertFalse((self.target/"ffmpeg.exe").exists())
        self.assert_no_temporary_files()

    def test_invalid_windows_executable_is_rejected(self):
        data = self.archive({"release/bin/ffmpeg.exe": b"not an exe", "release/bin/ffprobe.exe": b"MZfixture"})
        with self.assertRaisesRegex(ValueError, "Windows program"):
            self.install(data)
        self.assertFalse((self.target/"ffmpeg.exe").exists())
        self.assert_no_temporary_files()

    def test_programs_without_required_codecs_are_never_published(self):
        def invalid(folder):raise ValueError("libvorbis-Decoder fehlt")
        with self.assertRaisesRegex(ValueError, "libvorbis"):
            self.install(self.archive(), validator=invalid)
        self.assertFalse((self.target/"ffmpeg.exe").exists())
        self.assert_no_temporary_files()

    def test_truncated_download_leaves_no_partial_installation(self):
        data = self.archive()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.install(data, length=len(data)+10)
        self.assertFalse((self.target/"ffmpeg.exe").exists())
        self.assert_no_temporary_files()

    def test_oversized_response_is_rejected_before_reading(self):
        with self.assertRaisesRegex(ValueError, "larger than expected"):
            self.install(b"", length=setup.MAX_DOWNLOAD+1)
        self.assert_no_temporary_files()

    def test_network_failure_can_be_retried(self):
        with mock.patch.object(setup, "validate_tools", side_effect=ValueError("missing")), \
             mock.patch.object(setup.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                setup.ensure_windows_tools(self.target)
        self.assert_no_temporary_files()
        self.assertTrue(self.install(self.archive())["downloaded"])

    def test_cancellation_before_download_does_not_touch_disk(self):
        cancel = threading.Event();cancel.set()
        with mock.patch.object(setup.urllib.request, "urlopen") as network:
            with self.assertRaises(InterruptedError):
                setup.ensure_windows_tools(self.target, cancel=cancel)
        network.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_cancellation_during_download_removes_temporary_files(self):
        cancel = threading.Event()
        data = self.archive()
        def progress(event):
            if event.get("received"):cancel.set()
        with mock.patch.object(setup, "validate_tools", side_effect=ValueError("missing")), \
             mock.patch.object(setup.urllib.request, "urlopen", return_value=Response(data)), \
             mock.patch.object(setup, "DOWNLOAD_SHA256", hashlib.sha256(data).hexdigest()):
            with self.assertRaises(InterruptedError):
                setup.ensure_windows_tools(self.target, progress, cancel)
        self.assertFalse((self.target/"ffmpeg.exe").exists())
        self.assert_no_temporary_files()

    def test_failed_second_replace_rolls_back_existing_pair(self):
        staging = self.root/"staging";staging.mkdir()
        self.target.mkdir(parents=True)
        for name in (*setup.NAMES, "ffmpeg-download.json"):
            (staging/name).write_bytes(b"new")
            (self.target/name).write_bytes(b"old")
        original = Path.replace
        def replace(path, destination):
            if path==staging/"ffprobe.exe":raise PermissionError("program is locked")
            return original(path, destination)
        with mock.patch.object(Path, "replace", replace):
            with self.assertRaises(PermissionError):setup.publish_tools(staging, self.target)
        for name in (*setup.NAMES, "ffmpeg-download.json"):
            self.assertEqual((self.target/name).read_bytes(), b"old")

    def test_executable_probe_checks_vorbis_decoder_and_both_encoders(self):
        self.target.mkdir(parents=True)
        for name in setup.NAMES:(self.target/name).touch()
        missing_decoder = False
        def execute(args, **kwargs):
            if args[-1]=="-version":output=Path(args[0]).stem + " version fixture\n"
            elif args[-1]=="-decoders":output="" if missing_decoder else " A....D libvorbis codec\n"
            else:output=" A....D libvorbis codec\n V....D libx264 codec\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        with mock.patch.object(setup.subprocess, "run", side_effect=execute) as run:
            self.assertEqual(setup.validate_tools(self.target)["folder"], str(self.target))
            self.assertEqual(run.call_count, 4)
        missing_decoder = True
        with mock.patch.object(setup.subprocess, "run", side_effect=execute):
            with self.assertRaisesRegex(ValueError, "libvorbis-Decoder"):
                setup.validate_tools(self.target)

    def test_setup_thread_reports_failure_then_allows_retry(self):
        controller = setup.ToolSetup()
        with mock.patch.object(setup, "ensure_windows_tools", side_effect=OSError("offline")):
            controller.start(self.target);controller.thread.join(3)
        self.assertFalse(controller.running)
        self.assertFalse(controller.events.get_nowait()["success"])
        with mock.patch.object(setup, "ensure_windows_tools", return_value={"downloaded": True}):
            controller.start(self.target);controller.thread.join(3)
        self.assertTrue(controller.events.get_nowait()["success"])


if __name__=="__main__":unittest.main()
