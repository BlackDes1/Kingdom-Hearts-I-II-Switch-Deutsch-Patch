"""Combined build tests using generated video and synthetic native archive data."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import archive_core as core
import kh2_switch as tool
import movie_import as movies
from test_workflow import asset_blob


class MergeTests(unittest.TestCase):
    def test_case_collision_is_rejected_before_moving_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            (first / "SAME.pkg").write_bytes(b"first")
            (second / "same.pkg").write_bytes(b"second")
            with self.assertRaisesRegex(ValueError, "Doppeltes Ausgabeziel"):
                tool.merge_outputs([first, second], root / "ready")
            self.assertFalse((root / "ready").exists())
            self.assertEqual((first / "SAME.pkg").read_bytes(), b"first")
            self.assertEqual((second / "same.pkg").read_bytes(), b"second")

    def test_file_directory_collision_is_rejected_before_moving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "first", root / "second"
            first.mkdir()
            (second / "folder").mkdir(parents=True)
            (first / "FOLDER").write_text("file")
            (second / "folder/child.mp4").write_text("child")
            with self.assertRaisesRegex(ValueError, "überschneiden"):
                tool.merge_outputs([first, second], root / "ready")
            self.assertFalse((root / "ready").exists())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe not installed")
class CombinedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_temp = tempfile.TemporaryDirectory()
        cls.media = Path(cls.media_temp.name)
        ffmpeg = shutil.which("ffmpeg")
        for name, size, fps, level, tone in (("native", "160x90", "25", "3.1", "440"),
                                            ("german", "160x90", "25", "3.1", "880"),
                                            ("convert", "320x180", "30", "4.0", "880")):
            subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                            f"testsrc2=size={size}:rate={fps}", "-f", "lavfi", "-i",
                            "sine=frequency=" + tone + ":sample_rate=48000", "-t", "1",
                            "-c:v", "libx264", "-threads", "1", "-profile:v", "high", "-level:v", level,
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-ar", "48000",
                            "-movflags", "+faststart", str(cls.media / (name + ".mp4"))],
                           check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.media_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.romfs = self.root / "romfs"
        movie_dir = self.romfs / "Switch/juefigs/KH2ReSource/zmovie/en"
        movie_dir.mkdir(parents=True)
        for film in movies.FILMS:
            shutil.copyfile(self.media / "native.mp4", movie_dir / (film + ".mp4"))
        self.patch = self.root / "German voices.kh2pcpatch"
        self.canonical = "kh2_sixth/remastered/obj/P_EX100.a.us/battle.win32.scd"
        self.audio = b"SEDBSSCF" + b"new-German-voice" * 100
        with zipfile.ZipFile(self.patch, "w") as archive:
            archive.writestr(self.canonical, self.audio)
        blob = asset_blob(b"SEDBSSCF" + b"old-native-voice" * 100)
        (self.romfs / "kh2_sixth.pkg").write_bytes(blob)
        (self.romfs / "kh2_sixth.hed").write_bytes(core.HED_ROW.pack(core.md5_name("obj/P_EX100.a.us"), 0, len(blob), 16))
        self.films = self.root / "German movies.zip"
        self.write_movies()

    def tearDown(self):
        self.temp.cleanup()

    def write_movies(self, convert=False, corrupt=False, platform="EPIC"):
        with zipfile.ZipFile(self.films, "w") as archive:
            for film in movies.FILMS:
                name = platform + "/juefigs/KH2ReSource/zmovie/en/" + film + ".mp4"
                if corrupt and film == "ena":
                    archive.writestr(name, b"corrupted media")
                else:
                    archive.write(self.media / ("convert.mp4" if convert and film == "ena" else "german.mp4"), name)

    def test_complete_build_has_checked_archives_and_twelve_films_in_one_tree(self):
        before = {p.relative_to(self.root).as_posix(): movies.digest(p)
                  for p in self.root.rglob("*") if p.is_file()}
        output = self.root / "complete"
        with tool.PatchSource(self.patch) as source:
            destination = tool.build_all(self.romfs, source, self.films, output, tool.COLLECTION_ID, "test-version")
        self.assertEqual(destination, output / "atmosphere/contents" / tool.TITLE_ID / "romfs")
        self.assertTrue((destination / "kh2_sixth.hed").is_file())
        self.assertTrue((destination / "kh2_sixth.pkg").is_file())
        self.assertEqual(len(list(destination.rglob("*.mp4"))), 12)
        self.assertEqual(movies.digest(destination / "Switch/juefigs/KH2ReSource/zmovie/en/ena.mp4"),
                         movies.digest(self.media / "german.mp4"))
        for name, sha in before.items():
            self.assertEqual(movies.digest(self.root / name), sha)
        report = json.loads((output / "build-result.json").read_text())
        self.assertEqual(report["mode"], "voices_and_movies")
        self.assertTrue(report["voices_completed"] and report["movies_completed"])
        self.assertEqual(report["audio_files_verified"], 1)
        self.assertEqual(report["movies_verified"], 12)
        self.assertEqual(report["output_files"], 14)
        self.assertFalse(report["hardware_tested"])
        self.assertFalse((output / "work").exists())
        with zipfile.ZipFile(output / "KH2-Gesamt-Diagnose.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("KH2-Switch-Diagnose.zip", archive.namelist())
            self.assertIn("KH2-Film-Diagnose.zip", archive.namelist())
            self.assertFalse(any(n.endswith((".mp4", ".pkg")) for n in archive.namelist()))

    def test_combined_mode_converts_source_film_and_keeps_audio_build(self):
        self.write_movies(convert=True)
        with tool.PatchSource(self.patch) as source:
            destination = tool.build_all(self.romfs, source, self.films, self.root / "convert-all")
        film = destination / "Switch/juefigs/KH2ReSource/zmovie/en/ena.mp4"
        metadata = movies.MediaTools().probe(film)
        self.assertEqual(metadata["video"]["width"], 160)
        self.assertTrue((destination / "kh2_sixth.pkg").is_file())
        report = json.loads((self.root / "convert-all/build-result.json").read_text())
        self.assertEqual(report["movies_converted"], 1)

    def test_voice_failure_keeps_diagnosis_and_publishes_no_partial_mod(self):
        with zipfile.ZipFile(self.patch, "a") as archive:
            archive.writestr("kh2_sixth/original/obj/P_EX100.a.us", b"unsupported original change")
        output = self.root / "voice-failed"
        with tool.PatchSource(self.patch) as source:
            with self.assertRaisesRegex(ValueError, "KH2-Gesamt-Diagnose.zip"):
                tool.build_all(self.romfs, source, self.films, output)
        self.assertFalse((output / "atmosphere").exists())
        self.assertFalse((output / "work").exists())
        with zipfile.ZipFile(output / "KH2-Gesamt-Diagnose.zip") as archive:
            self.assertIn("KH2-Switch-Diagnose.zip", archive.namelist())
            report = json.loads(archive.read("build-result.json"))
            self.assertFalse(report["voices_completed"])
            self.assertEqual(report["phase"], "Stimmen")

    def test_movie_failure_removes_staged_voice_mod_but_preserves_both_diagnoses(self):
        self.write_movies(corrupt=True)
        output = self.root / "movie-failed"
        with tool.PatchSource(self.patch) as source:
            with self.assertRaisesRegex(ValueError, "KH2-Gesamt-Diagnose.zip"):
                tool.build_all(self.romfs, source, self.films, output)
        self.assertFalse((output / "atmosphere").exists())
        self.assertFalse((output / "work").exists())
        self.assertFalse(list(output.rglob("*.pkg")))
        with zipfile.ZipFile(output / "KH2-Gesamt-Diagnose.zip") as archive:
            report = json.loads(archive.read("build-result.json"))
            self.assertTrue(report["voices_completed"])
            self.assertFalse(report["movies_completed"])
            self.assertIn("KH2-Switch-Diagnose.zip", archive.namelist())
            self.assertIn("KH2-Film-Diagnose.zip", archive.namelist())

    def test_cli_combined_steam_build_opens_voice_wrapper_and_converts_steam_movie(self):
        self.write_movies(convert=True, platform="STEAM")
        wrapper = self.root / "Nexus-German-voices.zip"
        with zipfile.ZipFile(wrapper, "w") as archive:
            archive.write(self.patch, self.patch.name)
        output = self.root / "cli-output"
        result = subprocess.run([sys.executable, str(PROJECT / "kh2_switch.py"), "--build-all", "--romfs",
                                 str(self.romfs), "--mod", str(wrapper), "--movies", str(self.films),
                                 "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((output / "atmosphere/contents" / tool.TITLE_ID / "romfs/kh2_sixth.pkg").is_file())
        self.assertEqual(len(list((output / "atmosphere").rglob("*.mp4"))), 12)
        report = json.loads((output / "build-result.json").read_text())
        self.assertEqual(report["movies_source"]["source_platform"], "STEAM")
        self.assertEqual(report["movies_converted"], 1)
        self.assertFalse((output / "atmosphere/contents" / tool.TITLE_ID / "romfs/STEAM").exists())

    def test_combined_build_accepts_original_scd_banks_with_steam_movies(self):
        from test_native import make_bank, make_adpcm_bank, native_asset
        ogg = self.root / 'voice.ogg'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.media / 'german.mp4'), '-vn',
                        '-c:a', 'libvorbis', '-ac', '1', '-ar', '48000', str(ogg)], check=True, capture_output=True)
        bank = make_bank(ogg.read_bytes())
        wave = self.root / 'native-voice.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.media / 'native.mp4'), '-vn',
                        '-c:a', 'adpcm_ms', '-block_size', '32', '-ac', '1', '-ar', '48000', str(wave)],
                       check=True, capture_output=True)
        native_bank = make_adpcm_bank(wave.read_bytes())
        quad = self.root / 'native-quad.ogg'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.media / 'native.mp4'), '-vn',
                        '-af', 'pan=quad|FL=c0|FR=c0|BL=c0|BR=c0', '-c:a', 'libvorbis', str(quad)],
                       check=True, capture_output=True)
        quad_bank = make_bank(quad.read_bytes(), channels=4)
        asset = 'voice/us/event/native-original.win32.scd'
        raw = native_asset(native_bank)
        pkg, hed = self.romfs / 'kh2_sixth.pkg', self.romfs / 'kh2_sixth.hed'
        with pkg.open('ab') as stream:
            offset = stream.tell()
            stream.write(raw)
        with hed.open('ab') as stream:
            stream.write(core.HED_ROW.pack(core.md5_name(asset), offset, len(raw), len(native_bank)))
        quad_asset = 'voice/us/event/native-quad.win32.scd'
        quad_raw = native_asset(quad_bank)
        with pkg.open('ab') as stream:
            quad_offset = stream.tell()
            stream.write(quad_raw)
        with hed.open('ab') as stream:
            stream.write(core.HED_ROW.pack(core.md5_name(quad_asset), quad_offset, len(quad_raw), len(quad_bank)))
        with zipfile.ZipFile(self.patch, 'w') as archive:
            archive.writestr('kh2_sixth/original/' + asset, bank)
            archive.writestr('kh2_sixth/original/' + quad_asset, native_bank)
        self.write_movies(platform='STEAM')
        output = self.root / 'native-combined'
        with tool.PatchSource(self.patch) as source:
            dest = tool.build_all(self.romfs, source, self.films, output)
        report = json.loads((output / 'build-result.json').read_text())
        voice = json.loads((output / 'voice-result.json').read_text())
        self.assertEqual(voice['original_scd_verified'], 2)
        self.assertEqual(voice['scd_checks'][0]['replacement_codecs'], [12])
        self.assertEqual(len(voice['scd_checks'][0]['codec_conversions']), 1)
        self.assertEqual(voice['scd_checks'][1]['replacement_channels'], [4])
        self.assertEqual(len(voice['scd_checks'][1]['channel_conversions']), 1)
        self.assertEqual(report['movies_verified'], 12)
        self.assertEqual(len(list(dest.rglob('*.mp4'))), 12)
        self.assertTrue((dest / 'kh2_sixth.pkg').exists())

    def test_default_menu_collects_both_sources_and_calls_combined_builder(self):
        destination = self.root / "result"
        with mock.patch("builtins.input", side_effect=["", "", ""]), \
             mock.patch.object(tool, "ask_path", side_effect=[self.romfs, self.patch, self.films, self.root]) as dialogs, \
             mock.patch.object(tool, "build_all", return_value=destination) as combined, \
             mock.patch.object(tool, "build") as audio_only, mock.patch("sys.stdout", new=io.StringIO()):
            tool.interactive()
        self.assertEqual(combined.call_count, 1)
        args = combined.call_args.args
        self.assertEqual(args[0], self.romfs)
        self.assertEqual(args[2], self.films)
        self.assertIn("STEAM", dialogs.call_args_list[2].args[0])
        audio_only.assert_not_called()

    def test_protected_input_directories_existing_output_and_wrong_id_remain_untouched(self):
        with tool.PatchSource(self.patch) as source:
            with self.assertRaisesRegex(ValueError, "außerhalb"):
                tool.build_all(self.romfs, source, self.films, self.romfs / "output")
            existing = self.root / "existing"
            existing.mkdir()
            marker = existing / "keep.txt"
            marker.write_text("keep")
            with self.assertRaisesRegex(ValueError, "neuen Ausgabeordner"):
                tool.build_all(self.romfs, source, self.films, existing)
            self.assertEqual(marker.read_text(), "keep")
            with self.assertRaises(ValueError):
                tool.build_all(self.romfs, source, self.films, self.root / "wrong-id", "010090C022978001")
        self.assertFalse((self.root / "wrong-id").exists())

    def test_missing_ffmpeg_blocks_build_early_and_keeps_combined_diagnostic(self):
        output = self.root / "missing-tool"
        with tool.PatchSource(self.patch) as source, \
             mock.patch.object(movies.MediaTools, "find", side_effect=ValueError("ffmpeg fehlt")), \
             mock.patch.object(tool, "build") as audio_build:
            with self.assertRaisesRegex(ValueError, "ffmpeg fehlt"):
                tool.build_all(self.romfs, source, self.films, output)
        audio_build.assert_not_called()
        self.assertFalse((output / "work").exists())
        self.assertFalse((output / "atmosphere").exists())
        self.assertTrue((output / "KH2-Gesamt-Diagnose.zip").is_file())


if __name__ == "__main__":
    unittest.main()
