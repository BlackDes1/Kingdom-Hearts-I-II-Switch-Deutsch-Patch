"""Synthetic KH2 fixtures; no game or mod audio is distributed here."""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import kh2_switch as tool
import archive_core as core
from egs_reader import encode_data, decode_data


def asset_blob(audio, mode=0):
    original = b"original-data!!!"  # 16 bytes
    seed = struct.pack("<iiii", len(original), 2, -2, 12345)
    texture = b"native-texture!"
    rows, bodies = [], []
    logical = 16 + 2 * 48 + len(original)
    for name, content, packed in (("battle.win32.scd", audio, mode), ("-0.dds", texture, -2)):
        data, stored_mode = encode_data(content, seed, packed)
        rows.append(core.HD_ROW.pack(name.encode().ljust(32, b"\0"), logical, -1, len(content), stored_mode))
        bodies.append(data)
        logical += len(content)
    return seed + b"".join(rows) + original + b"".join(bodies)


class KH2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.romfs = self.root / "romfs"
        self.romfs.mkdir()
        self.audio = b"SEDBSSCF" + bytes(range(256)) * 4
        self.inner = self.root / "German voices KH2 2.1.kh2pcpatch"
        self.canonical = "kh2_sixth/remastered/obj/P_EX100.a.us/battle.win32.scd"
        with zipfile.ZipFile(self.inner, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(self.canonical, self.audio)
        blob = asset_blob(b"SEDBSSCF" + b"old!" * 200)
        digest = core.md5_name("obj/P_EX100.a.us")
        (self.romfs / "kh2_sixth.pkg").write_bytes(blob)
        (self.romfs / "kh2_sixth.hed").write_bytes(core.HED_ROW.pack(digest, 0, len(blob), 16))

    def tearDown(self):
        self.temp.cleanup()

    def test_nexus_outer_zip_with_single_nested_patch(self):
        wrapper = self.root / "German voices-42-2-1-1718316309.zip"
        with zipfile.ZipFile(wrapper, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(self.inner, self.inner.name)
        with tool.PatchSource(wrapper) as source:
            self.assertEqual(source.nested_name, self.inner.name)
            self.assertEqual(source.read(self.canonical), self.audio)
            temporary = source.temp.name
        self.assertFalse(Path(temporary).exists())

    def test_direct_extracted_mod_directory(self):
        folder = self.root / "mod"
        path = folder / self.canonical
        path.parent.mkdir(parents=True)
        path.write_bytes(self.audio)
        with tool.PatchSource(folder) as source:
            self.assertEqual(source.read(self.canonical), self.audio)

    def test_diagnosis_collects_source_and_native_samples(self):
        target = self.root / "diagnose.zip"
        with tool.PatchSource(self.inner) as source:
            report = tool.diagnose(self.romfs, source, target, tool.COLLECTION_ID, "1.0")
        self.assertEqual(report["kh2_program_id"], tool.TITLE_ID)
        self.assertEqual(report["inspection"]["matched_scd"], 1)
        self.assertTrue(report["inspection"]["can_build_audio_only"])
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read("pc/" + self.canonical), self.audio)
            self.assertTrue(any(n.startswith("native/") for n in archive.namelist()))
            self.assertNotIn(str(self.romfs), archive.read("diagnose.json").decode())

    def test_audio_only_build_checks_content_and_preserves_originals(self):
        hashes = {p.name: core.sha256_file(p) for p in self.romfs.iterdir()}
        with tool.PatchSource(self.inner) as source:
            destination = tool.build(self.romfs, source, self.root / "build", tool.COLLECTION_ID)
        self.assertEqual(destination.parent.name, "010090C022978003")
        self.assertEqual(len(list(destination.iterdir())), 2)
        self.assertEqual(hashes, {p.name: core.sha256_file(p) for p in self.romfs.iterdir()})
        report = json.loads((self.root / "build/build-result.json").read_text())
        self.assertEqual(report["audio_files_verified"], 1)
        self.assertFalse(report["hardware_tested"])
        self.assertFalse((self.root / "build/work").exists())

    def test_original_changes_require_review_and_are_not_silently_skipped(self):
        original = "kh2_sixth/original/obj/P_EX100.a.us"
        with zipfile.ZipFile(self.inner, "a") as archive:
            archive.writestr(original, b"German battle-cry metadata")
        with tool.PatchSource(self.inner) as source:
            with self.assertRaisesRegex(ValueError, "weitere Dateianpassungen"):
                tool.build(self.romfs, source, self.root / "needs-review")
        self.assertFalse((self.root / "needs-review/atmosphere").exists())
        with zipfile.ZipFile(self.root / "needs-review/KH2-Switch-Diagnose.zip") as archive:
            self.assertEqual(archive.read("pc/" + original), b"German battle-cry metadata")
            report = json.loads(archive.read("diagnose.json"))
            self.assertIn(original, report["inspection"]["source_extra_files"])

    def test_wrong_native_audio_names_block_build_and_preserve_diagnosis(self):
        pkg = self.romfs / "kh2_sixth.pkg"
        pkg.write_bytes(pkg.read_bytes().replace(b"battle.win32.scd", b"battle.other.scd"))
        with tool.PatchSource(self.inner) as source:
            with self.assertRaises(ValueError):
                tool.build(self.romfs, source, self.root / "wrong-names")
        self.assertTrue((self.root / "wrong-names/KH2-Switch-Diagnose.zip").exists())
        self.assertFalse((self.root / "wrong-names/atmosphere").exists())

    def test_duplicate_patch_paths_are_rejected(self):
        with zipfile.ZipFile(self.inner, "a") as archive:
            archive.writestr("wrapper/" + self.canonical, self.audio)
        with self.assertRaisesRegex(ValueError, "Doppelter Modpfad"):
            with tool.PatchSource(self.inner):
                pass

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            tool.PatchSource.canonical("kh2_sixth/remastered/../../escape.scd")

    def test_program_id_mapping_keeps_kh1_and_kh2_separate(self):
        self.assertEqual(tool.resolve_title_id(tool.COLLECTION_ID), tool.TITLE_ID)
        with self.assertRaises(ValueError):
            tool.resolve_title_id("010090C022978001")

    def test_empty_negative_placeholder_is_preserved(self):
        hed = self.romfs / "kh2_sixth.hed"
        before = hed.read_bytes()
        marker = bytes.fromhex("dfb842f8406cb40d4f059a2e1a680496")
        hed.write_bytes(core.HED_ROW.pack(marker, 0, 0, -33686019) + before)
        with tool.PatchSource(self.inner) as source:
            destination = tool.build(self.romfs, source, self.root / "empty-row")
        index = core.read_hed(destination / hed.name, (destination / "kh2_sixth.pkg").stat().st_size)
        self.assertEqual(index[marker]["actual_length"], -33686019)
        self.assertEqual(index[marker]["length"], 0)

    def test_archive_cipher_reference_vector(self):
        seed = bytes.fromhex("1000000002000000feffffffd2040000")
        cipher = bytes.fromhex("2d6f8173f5bbc74a603cf0b381937cc6")
        self.assertEqual(decode_data(cipher, seed, -1, 16), bytes(range(16)))

    def test_external_pc_script_is_reported_and_blocks_partial_success(self):
        with zipfile.ZipFile(self.inner, "a") as archive:
            archive.writestr("required-pc-hook.lua", "-- fixture")
        with tool.PatchSource(self.inner) as source:
            report = tool.diagnose(self.romfs, source, self.root / "script-diagnose.zip")
        self.assertFalse(report["inspection"]["can_build_audio_only"])
        self.assertIn("required-pc-hook.lua", report["inspection"]["source_extra_files"])


if __name__ == "__main__":
    unittest.main()
