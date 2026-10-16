"""Real, generated H.264/AAC fixtures. No game footage is included."""
import copy
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import kh2_switch as tool
import movie_import as movies


class MovieMetadataTests(unittest.TestCase):
    def test_reported_kh2_properties_only_require_ena_video_conversion(self):
        fixtures = json.loads((PROJECT / 'tests/fixtures/reported-film-properties.json').read_text())
        planned = {name: movies.plan_conversion(item['native'], item['german']) for name, item in fixtures.items()}
        self.assertEqual(len(planned), 12)
        self.assertEqual([name for name, plan in planned.items() if plan['action'] == 'convert'], ['ena'])
        self.assertIn('profile', planned['ena']['video_changes'])

    def test_significant_average_nominal_frame_difference_is_not_hidden(self):
        fixture = json.loads((PROJECT / 'tests/fixtures/reported-film-properties.json').read_text())['hca']
        source = copy.deepcopy(fixture['german'])
        source['video'].update(avg_frame_rate='29/1', r_frame_rate='30/1')
        plan = movies.plan_conversion(fixture['native'], source)
        self.assertEqual(plan['action'], 'convert')
        self.assertIn('frame_rate', plan['video_changes'])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe not installed")
class MovieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_temp = tempfile.TemporaryDirectory()
        cls.media = Path(cls.media_temp.name)
        cls.tools = movies.MediaTools()
        for name, size, fps, level, frequency, duration, channels, sample_rate in (
            ("native", "160x90", "25", "3.1", "440", "1", "2", "48000"),
            ("german", "160x90", "25", "3.1", "880", "1", "2", "48000"),
            ("convert", "320x180", "30", "4.0", "880", "1", "1", "44100"),
            ("audio-only", "160x90", "25", "3.1", "880", "1", "1", "44100"),
            ("long", "160x90", "25", "3.1", "880", "2", "2", "48000"),
        ):
            subprocess.run([cls.tools.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                            f"testsrc2=size={size}:rate={fps}", "-f", "lavfi", "-i",
                            f"sine=frequency={frequency}:sample_rate={sample_rate}", "-t", duration,
                            "-c:v", "libx264", "-threads", "1", "-profile:v", "high", "-level:v", level,
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", channels, "-ar", sample_rate,
                            "-movflags", "+faststart", str(cls.media / (name + ".mp4"))],
                           check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.media_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.romfs = self.root / "romfs"
        self.movie_dir = self.romfs / "Switch/juefigs/KH2ReSource/zmovie/en"
        self.movie_dir.mkdir(parents=True)
        for film in movies.FILMS:
            shutil.copyfile(self.media / "native.mp4", self.movie_dir / (film + ".mp4"))
        self.source = self.root / "movies.zip"
        self.write_source()

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, changed=None, omit=(), platform="EPIC"):
        with zipfile.ZipFile(self.source, "w", zipfile.ZIP_STORED) as archive:
            for film in sorted(movies.FILMS - set(omit)):
                file = self.media / ((changed or {}).get(film, "german") + ".mp4")
                archive.write(file, platform + "/juefigs/KH2ReSource/zmovie/en/" + film + ".mp4")

    def test_steam_zip_is_detected_from_payload_and_copied_to_native_switch_paths(self):
        self.source = self.root / "arbitrarily-renamed.zip"
        self.write_source(platform="STEAM")
        with movies.MovieSource(self.source) as source:
            self.assertEqual(source.summary()["source_platform"], "STEAM")
            self.assertEqual(source.summary()["detected_download"], "German movies (STEAM)")
            self.assertEqual(source.summary()["nexus_file_id"], 816)
        output = self.root / "steam-movies"
        destination = tool.build_movies(self.romfs, self.source, output)
        self.assertEqual(len(list(destination.rglob("*.mp4"))), 12)
        self.assertFalse((destination / "STEAM").exists())
        self.assertEqual(movies.digest(destination / "Switch/juefigs/KH2ReSource/zmovie/en/ena.mp4"),
                         movies.digest(self.media / "german.mp4"))
        report = json.loads((output / "movie-result.json").read_text())
        self.assertEqual(report["source"]["source_platform"], "STEAM")
        self.assertEqual(report["movies_verified"], 12)

    def test_extracted_steam_root_is_detected_when_relative_paths_start_with_juefigs(self):
        self.write_source(platform="STEAM")
        folder = self.root / "unpacked"
        with zipfile.ZipFile(self.source) as archive:
            archive.extractall(folder)  # Locally generated fixture.
        with movies.MovieSource(folder / "STEAM") as source:
            self.assertEqual(source.summary()["source_platform"], "STEAM")
            self.assertEqual(source.summary()["missing_films"], [])
            self.assertTrue(source.records["ena"]["path"].startswith("juefigs/"))
            with source.open("ena") as stream:
                self.assertEqual(stream.read(), (self.media / "german.mp4").read_bytes())

    def test_mixed_steam_epic_payload_is_rejected(self):
        with zipfile.ZipFile(self.source, "w") as archive:
            for index, film in enumerate(sorted(movies.FILMS)):
                platform = "STEAM" if index < 6 else "EPIC"
                archive.write(self.media / "german.mp4", platform + "/juefigs/KH2ReSource/zmovie/en/" + film + ".mp4")
        with self.assertRaisesRegex(ValueError, "gemischt"):
            with movies.MovieSource(self.source):
                pass

    def test_archive_filename_does_not_mislabel_payload_without_platform_folder(self):
        archive_path = self.root / "German movies (STEAM).zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for film in movies.FILMS:
                archive.write(self.media / "german.mp4", "KH2ReSource/zmovie/en/" + film + ".mp4")
        with movies.MovieSource(archive_path) as source:
            self.assertIsNone(source.summary()["source_platform"])
            self.assertIsNone(source.summary()["nexus_file_id"])
            self.assertEqual(len(source.records), 12)

    def test_full_zip_build_keeps_source_and_native_bytes_and_checks_all_films(self):
        hashes = {p.name: movies.digest(p) for p in self.movie_dir.iterdir()}
        source_hash = movies.digest(self.source)
        destination = tool.build_movies(self.romfs, self.source, self.root / "output", tool.COLLECTION_ID)
        self.assertEqual(destination.parent.name, tool.TITLE_ID)
        self.assertEqual(len(list(destination.rglob("*.mp4"))), 12)
        self.assertEqual(movies.digest(destination / "Switch/juefigs/KH2ReSource/zmovie/en/ena.mp4"),
                         movies.digest(self.media / "german.mp4"))
        self.assertEqual(source_hash, movies.digest(self.source))
        self.assertEqual(hashes, {p.name: movies.digest(p) for p in self.movie_dir.iterdir()})
        report = json.loads((self.root / "output/movie-result.json").read_text())
        self.assertEqual(report["movies_verified"], 12)
        self.assertEqual(report["movies_converted"], 0)
        self.assertTrue(all(i["full_decode_verified"] for i in report["films"].values()))
        self.assertFalse(report["hardware_tested"])
        self.assertFalse((self.root / "output/work").exists())

    def test_conversion_matches_native_video_and_audio_format_and_keeps_german_tone(self):
        self.write_source({"ena": "convert"})
        destination = tool.build_movies(self.romfs, self.source, self.root / "converted")
        film = destination / "Switch/juefigs/KH2ReSource/zmovie/en/ena.mp4"
        metadata = self.tools.probe(film)
        self.assertEqual(metadata["video"]["width"], 160)
        self.assertEqual(metadata["video"]["height"], 90)
        self.assertEqual(metadata["video"]["avg_frame_rate"], "25/1")
        self.assertEqual(metadata["audio"]["sample_rate"], "48000")
        self.assertEqual(metadata["audio"]["channels"], 2)
        report = json.loads((self.root / "converted/movie-result.json").read_text())
        self.assertEqual(report["movies_converted"], 1)
        # A decoded 880 Hz source tone must survive the conversion; native is 440 Hz.
        pcm = subprocess.run([self.tools.ffmpeg, "-v", "error", "-i", str(film), "-vn", "-t", "0.5",
                              "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                             check=True, capture_output=True).stdout
        samples = struct.unpack("<" + "f" * (len(pcm) // 4), pcm)
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        self.assertAlmostEqual(crossings * 48000 / len(samples), 880, delta=8)

    def test_missing_films_block_partial_mod_and_preserve_compact_diagnostic(self):
        self.write_source(omit=("ena",))
        output = self.root / "incomplete"
        with self.assertRaisesRegex(ValueError, "Filmprüfung"):
            tool.build_movies(self.romfs, self.source, output)
        self.assertFalse((output / "atmosphere").exists())
        self.assertFalse((output / "work").exists())
        with zipfile.ZipFile(output / "KH2-Film-Diagnose.zip") as archive:
            report = json.loads(archive.read("movie-result.json"))
            self.assertEqual(report["source"]["missing_films"], ["ena"])
            self.assertFalse(any(n.endswith(".mp4") for n in archive.namelist()))

    def test_audio_only_conversion_preserves_encoded_german_video(self):
        self.write_source({"ena": "audio-only"})
        destination = tool.build_movies(self.romfs, self.source, self.root / "audio-converted")
        film = destination / "Switch/juefigs/KH2ReSource/zmovie/en/ena.mp4"
        report = json.loads((self.root / "audio-converted/movie-result.json").read_text())
        self.assertEqual(report["films"]["ena"]["plan"]["video_changes"], [])
        def video_hash(path):
            return subprocess.run([self.tools.ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                                   "-c:v", "copy", "-f", "hash", "-"], check=True, capture_output=True).stdout
        self.assertEqual(video_hash(film), video_hash(self.media / "audio-only.mp4"))

    def test_decode_failure_cleans_staging_and_never_publishes_partial_atmosphere(self):
        output = self.root / "decode-failed"
        with mock.patch.object(movies.MediaTools, "run", side_effect=ValueError("decode fixture failure")):
            with self.assertRaisesRegex(ValueError, "decode fixture failure"):
                tool.build_movies(self.romfs, self.source, output)
        self.assertFalse((output / "atmosphere").exists())
        self.assertFalse((output / "work").exists())
        with zipfile.ZipFile(output / "KH2-Film-Diagnose.zip") as archive:
            report = json.loads(archive.read("movie-result.json"))
            self.assertTrue(any("decode fixture failure" in e for e in report["errors"]))

    def test_duration_mismatch_stops_without_trimming_or_publishing(self):
        self.write_source({"ena": "long"})
        output = self.root / "duration"
        with self.assertRaisesRegex(ValueError, "Filmprüfung"):
            tool.build_movies(self.romfs, self.source, output)
        self.assertFalse((output / "atmosphere").exists())
        report = json.loads((output / "movie-result.json").read_text())
        self.assertIn("Dauer", report["films"]["ena"]["error"])

    def test_diagnosis_does_not_build_and_retains_actual_native_path_case(self):
        renamed = self.romfs / "sWITCH"
        (self.romfs / "Switch").rename(renamed)
        result = tool.build_movies(self.romfs, self.source, self.root / "diagnose", diagnose_only=True)
        self.assertTrue(result.is_file())
        with zipfile.ZipFile(result) as archive:
            report = json.loads(archive.read("movie-result.json"))
        self.assertTrue(report["films"]["ena"]["target_path"].startswith("sWITCH/"))
        self.assertFalse((self.root / "diagnose/atmosphere").exists())

    def test_unknown_native_folder_is_documented_for_adaptation(self):
        alternate = self.romfs / "Switch/other/KH2Movie/en"
        alternate.parent.mkdir(parents=True)
        self.movie_dir.rename(alternate)
        output = self.root / "unknown-native-path"
        with self.assertRaisesRegex(ValueError, "Filmprüfung"):
            tool.build_movies(self.romfs, self.source, output)
        report = json.loads((output / "movie-result.json").read_text())
        self.assertEqual(len(report["native_mp4_inventory"]), 12)
        self.assertEqual(len(report["unmapped_native_samples"]), 3)
        self.assertIn("media", report["unmapped_native_samples"][0])
        self.assertFalse((output / "atmosphere").exists())

    def test_ambiguous_native_targets_and_missing_native_target_are_rejected(self):
        other = self.romfs / "other/KH2ReSource/zmovie/en"
        other.mkdir(parents=True)
        shutil.copyfile(self.media / "native.mp4", other / "ena.mp4")
        with self.assertRaisesRegex(ValueError, "Mehrere Switch-Ziele"):
            tool.build_movies(self.romfs, self.source, self.root / "ambiguous")
        shutil.rmtree(self.romfs / "other")
        (self.movie_dir / "ena.mp4").unlink()
        with self.assertRaisesRegex(ValueError, "Filmprüfung"):
            tool.build_movies(self.romfs, self.source, self.root / "missing-target")
        report = json.loads((self.root / "missing-target/movie-result.json").read_text())
        self.assertTrue(any("ena" in e for e in report["errors"]))

    def test_folder_source_and_duplicate_and_traversal_rejection(self):
        folder = self.root / "source-folder"
        folder.mkdir()
        with zipfile.ZipFile(self.source) as archive:
            archive.extractall(folder)  # The fixture was created above, not untrusted input.
        with movies.MovieSource(folder) as source:
            self.assertEqual(len(source.records), 12)
        with zipfile.ZipFile(self.source, "a") as archive:
            archive.write(self.media / "german.mp4", "wrapper/EPIC/juefigs/KH2ReSource/zmovie/en/ena.mp4")
        with self.assertRaisesRegex(ValueError, "Doppelter Film"):
            with movies.MovieSource(self.source):
                pass
        with self.assertRaisesRegex(ValueError, "Ungültiger Pfad"):
            movies.film_name("EPIC/../KH2ReSource/zmovie/en/ena.mp4")

    def test_output_and_id_validation_preserve_existing_data(self):
        with self.assertRaisesRegex(ValueError, "außerhalb"):
            tool.build_movies(self.romfs, self.source, self.romfs / "output")
        with self.assertRaises(ValueError):
            tool.build_movies(self.romfs, self.source, self.root / "wrong-id", "010090C022978001")
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "user-file.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(ValueError, "neuen Ausgabeordner"):
            tool.build_movies(self.romfs, self.source, existing)
        self.assertEqual(marker.read_text(), "keep")

    def test_damaged_movie_blocks_all_output(self):
        with zipfile.ZipFile(self.source, "w") as archive:
            for film in movies.FILMS:
                if film == "ena":
                    archive.writestr("EPIC/juefigs/KH2ReSource/zmovie/en/ena.mp4", b"not-a-movie")
                else:
                    archive.write(self.media / "german.mp4", "EPIC/juefigs/KH2ReSource/zmovie/en/" + film + ".mp4")
        output = self.root / "damaged"
        with self.assertRaisesRegex(ValueError, "Filmprüfung"):
            tool.build_movies(self.romfs, self.source, output)
        self.assertFalse((output / "atmosphere").exists())
        self.assertTrue((output / "KH2-Film-Diagnose.zip").is_file())

    def test_unsupported_encoding_and_aspect_ratio_need_manual_adaptation(self):
        native = self.tools.probe(self.media / "native.mp4")
        source = self.tools.probe(self.media / "convert.mp4")
        unsupported = copy.deepcopy(native)
        unsupported["video"]["codec_name"] = "vp9"
        with self.assertRaisesRegex(ValueError, "Videoformat"):
            movies.plan_conversion(unsupported, source)
        source["video"]["width"] = 180
        with self.assertRaisesRegex(ValueError, "Bildseitenverhältnis"):
            movies.plan_conversion(native, source)


if __name__ == "__main__":
    unittest.main()
