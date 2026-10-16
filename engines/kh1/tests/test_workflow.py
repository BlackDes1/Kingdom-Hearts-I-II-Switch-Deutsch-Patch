"""Synthetic archive tests; these do not replace a native Switch game test."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import kh1_switch as tool
from egs_reader import decode_data, xor_mask


def asset_bytes(audio, texture=b"unchanged texture", packed=-2, original=b"O" * 16):
    seed = struct.pack("<iiii", len(original), 2, -2, 1234)
    blocks = []
    rows = []
    logical = 16 + 2 * 48 + len(original)
    for name, content, codec in (("a.win32.scd", audio, packed), ("-0.dds", texture, -2)):
        raw = zlib.compress(content) if codec >= 0 else content
        if codec > -2:
            mask = xor_mask(seed)
            raw = bytes(value ^ mask[index % 16] if index < 256 else value
                        for index, value in enumerate(raw))
        stored_size = len(raw) if codec >= 0 else codec
        rows.append(struct.pack("<32siiii", name.encode().ljust(32, b"\0"), logical,
                                100, len(content), stored_size))
        blocks.append(raw + b"\xcd" * (-len(raw) % 16))
        logical += len(content)
    return seed + b"".join(rows) + original + b"".join(blocks)


def write_pair(directory, package, audio, packed=-2, texture=b"unchanged texture", original=b"O" * 16):
    blob = asset_bytes(audio, texture, packed, original)
    hed = directory / (package + ".hed")
    pkg = directory / (package + ".pkg")
    hed.write_bytes(struct.pack("<16sqii", tool.md5_name("voice/sample.vsb"), 0, len(blob), 16))
    pkg.write_bytes(blob)
    return hed, pkg


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.romfs = self.root / "romfs"
        self.mod = self.root / "mod"
        self.romfs.mkdir()
        self.mod.mkdir()
        self.audio = b"SEDBSSCF" + bytes(range(256)) * 2
        self.data = {"commit": "fixture", "repository": "fixture", "files": []}
        for package in tool.PACKAGES:
            write_pair(self.romfs, package, b"old audio")
            relative = package + "/remastered/voice/sample.vsb/a.win32.scd"
            target = self.mod / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(self.audio)
            self.data["files"].append({"path": relative, "size": len(self.audio),
                                       "sha256": hashlib.sha256(self.audio).hexdigest()})
        self.expected = tool.groups(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def test_diagnose_contains_indices_and_bounded_samples(self):
        output = self.root / "diagnose.zip"
        with mock.patch.object(tool, "manifest", return_value=self.data):
            result = tool.diagnose(self.romfs, output, tool.DEFAULT_TITLE_ID, "1.0")
        self.assertEqual(result["inspection"]["matched_scd"], 4)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(len([n for n in archive.namelist() if n.startswith("hed/")]), 4)
            self.assertEqual(len([n for n in archive.namelist() if n.endswith(".raw")]), 4)
            self.assertNotIn(str(self.romfs), archive.read("diagnose.json").decode())
        self.assertEqual((self.romfs / "kh1_first.pkg").read_bytes(), asset_bytes(b"old audio"))

    def test_wrong_names_are_detected_before_build(self):
        path = self.romfs / "kh1_first.pkg"
        path.write_bytes(path.read_bytes().replace(b"a.win32.scd", b"a.switchscd"))
        report = tool.inspect_archives(tool.find_archives(self.romfs), self.expected)
        self.assertFalse(report["compatible_names"])
        self.assertEqual(report["matched_scd"], 3)

    def test_hed_outside_pkg_is_rejected(self):
        path = self.romfs / "kh1_first.hed"
        path.write_bytes(struct.pack("<16sqii", b"0" * 16, 999999, 100, 16))
        with self.assertRaisesRegex(ValueError, "außerhalb"):
            tool.read_hed(path, 1)

    def test_zip_source_prefix_hashes_and_unrelated_paths(self):
        source = self.root / "source.zip"
        with zipfile.ZipFile(source, "w") as archive:
            for item in self.data["files"]:
                archive.write(self.mod / item["path"], "repo/" + item["path"])
            archive.writestr("../../not-extracted.txt", "unused")
        stage = tool.stage_mod(source, self.root / "stage", self.data)
        self.assertEqual(len(list(stage.rglob("*.scd"))), 4)
        self.assertFalse((self.root / "not-extracted.txt").exists())
        bad = copy.deepcopy(self.data)
        bad["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Quellenstand"):
            tool.stage_mod(source, self.root / "bad-stage", bad)

    def test_compressed_and_encrypted_audio_validation(self):
        originals = tool.find_archives(self.romfs)
        before = tool.inspect_archives(originals, self.expected)
        out = self.root / "patched"
        out.mkdir()
        patched = {package: write_pair(out, package, self.audio, packed=0) for package in tool.PACKAGES}
        result = tool.verify_output(originals, patched, self.expected, before)
        self.assertEqual(result["matched_scd"], 4)
        # The right name and length alone must not pass if audio bytes are wrong.
        write_pair(out, "kh1_first", self.audio[:-1] + b"X", packed=0)
        with self.assertRaisesRegex(ValueError, "Audiodaten"):
            tool.verify_output(originals, patched, self.expected, before)

    def test_cipher_matches_independent_upstream_reference_vector(self):
        # Generated separately from the original C# algorithm, including its
        # 176-byte expansion and eleven descending XOR passes.
        seed = bytes.fromhex("1000000002000000feffffffd2040000")
        cipher = bytes.fromhex("2d6f8173f5bbc74a603cf0b381937cc6")
        self.assertEqual(decode_data(cipher, seed, -1, 16), bytes(range(16)))

    def test_other_resources_must_be_preserved(self):
        originals = tool.find_archives(self.romfs)
        before = tool.inspect_archives(originals, self.expected)
        out = self.root / "bad-patched"
        out.mkdir()
        patched = {p: write_pair(out, p, self.audio) for p in tool.PACKAGES}
        write_pair(out, "kh1_first", self.audio, texture=b"CHANGED!! texture")
        with self.assertRaisesRegex(ValueError, "Remastered"):
            tool.verify_output(originals, patched, self.expected, before)

    def test_build_writes_checked_archives_and_leaves_source_untouched(self):
        original_hashes = {p.name: tool.sha256_file(p) for p in self.romfs.iterdir()}
        output = self.root / "build"
        with mock.patch.object(tool, "manifest", return_value=self.data):
            destination = tool.build(self.romfs, self.mod, output, tool.COLLECTION_TITLE_ID)
        self.assertEqual(len(list(destination.iterdir())), 8)
        self.assertEqual(destination.parent.name, tool.DEFAULT_TITLE_ID)
        self.assertFalse((output / "work").exists())
        self.assertEqual(original_hashes, {p.name: tool.sha256_file(p) for p in self.romfs.iterdir()})
        report = json.loads((output / "build-result.json").read_text())
        self.assertFalse(report["hardware_tested"])
        self.assertEqual(report["audio_replacements"], 4)
        self.assertEqual(report["title_id_user_supplied"], tool.COLLECTION_TITLE_ID)
        self.assertEqual(report["kh1_program_id"], tool.DEFAULT_TITLE_ID)

    def test_native_empty_placeholder_row_is_retained_and_relocated(self):
        package = "kh1_fifth"
        voice = asset_bytes(b"old audio")
        other = struct.pack("<iiii", 16, 0, -2, 5678) + b"untouched other!"
        empty_hash = bytes.fromhex("dfb842f8406cb40d4f059a2e1a680496")
        other_hash = tool.md5_name("other/sample.bin")
        pkg = self.romfs / (package + ".pkg")
        hed = self.romfs / (package + ".hed")
        pkg.write_bytes(voice + other + b"footer!")
        rows = ((tool.md5_name("voice/sample.vsb"), 0, len(voice), 16),
                (empty_hash, len(voice), 0, -33686019),
                (other_hash, len(voice), len(other), 16))
        hed.write_bytes(b"".join(tool.HED_ROW.pack(*r) for r in rows))
        originals = tool.find_archives(self.romfs)
        before = tool.inspect_archives(originals, self.expected)
        output = self.root / "placeholder-patched"
        result = tool.patch_archive(hed, pkg, self.mod / package, output,
                                    self.expected[package], self.root / "patch.json")
        new_index = tool.read_hed(result[0], result[1].stat().st_size)
        self.assertEqual(new_index[empty_hash]["actual_length"], -33686019)
        self.assertEqual(new_index[empty_hash]["length"], 0)
        self.assertEqual(new_index[empty_hash]["offset"], new_index[other_hash]["offset"])
        self.assertNotEqual(new_index[empty_hash]["offset"], len(voice))
        self.assertTrue(result[1].read_bytes().endswith(b"footer!"))
        patched = dict(originals, **{package: result})
        # Patch the other three fixture archives too for the full verifier.
        for name in tool.PACKAGES:
            if name == package:
                continue
            h, p = originals[name]
            patched[name] = tool.patch_archive(h, p, self.mod / name, output / name,
                                               self.expected[name], self.root / (name + ".json"))
        tool.verify_output(originals, patched, self.expected, before)

    def test_positive_length_with_negative_actual_size_is_rejected(self):
        hed = self.romfs / "kh1_fifth.hed"
        row = list(tool.HED_ROW.unpack(hed.read_bytes()))
        row[-1] = -33686019
        hed.write_bytes(tool.HED_ROW.pack(*row))
        with self.assertRaises(ValueError):
            tool.read_hed(hed, (self.romfs / "kh1_fifth.pkg").stat().st_size)


if __name__ == "__main__":
    unittest.main()
