"""Original SCD and nested HD resource tests with generated Ogg/Vorbis soundbanks."""
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import archive_core as core
import kh2_switch as tool
import native_patch
from egs_reader import encode_data, read_audio
from scd_tools import inspect_scd, compatible_scd
from scd_tools import wave_stream, ogg_stream
from scd_audio import AudioConverter, AudioToolError, ADPCM_BLOCK_SIZE, wave_parts, process_crashed, CHANNEL_LAYOUTS


def png(red=255):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes((0, red, 0, 0, 255)))) + chunk(b'IEND', b''))


def make_bank(ogg, frequency=48000, channels=1, count=1):
    cursor, header_size = 0, 0
    while cursor + 27 <= len(ogg):
        segments = ogg[cursor + 26]
        end = cursor + 27 + segments + sum(ogg[cursor + 27:cursor + 27 + segments])
        if b'\x05vorbis' in ogg[cursor:end]:
            header_size = end
            break
        cursor = end
    if not header_size:
        raise ValueError('Generated Ogg lacks Vorbis setup header')
    data = bytearray(max(0x100, (0x80 + 4 * count + 15) & ~15))
    data[:8] = b'SEDBSSCF'
    struct.pack_into('<I', data, 8, 3)
    data[13] = 4
    struct.pack_into('<H', data, 14, 0x30)
    struct.pack_into('<H', data, 0x34, count)
    struct.pack_into('<I', data, 0x3c, 0x80)
    for index in range(count):
        struct.pack_into('<I', data, 0x80 + 4 * index, len(data))
        data += struct.pack('<8I', len(ogg) - header_size, channels, frequency, 6, 0, 0, 32 + header_size, 0)
        extra = bytearray(32)
        extra[:2] = b'\x02\x20'
        struct.pack_into('<I', extra, 20, header_size)
        data += extra + ogg
        data += b'\xcd' * (-len(data) % 16)
    struct.pack_into('<I', data, 16, len(data))
    return bytes(data)


def native_asset(original, remastered=None, mode=0):
    original += b'\xcd' * (-len(original) % 16)
    remastered = remastered or {}
    # Compression does not depend on the seed; its padded size determines the final seed.
    packed, stored = encode_data(original, bytes(16), mode)
    seed = core.PKG_HEADER.pack(len(original), len(remastered), stored, 123456)
    packed, stored = encode_data(original, seed, mode)
    logical = 16 + 48 * len(remastered) + len(original)
    rows, bodies = [], []
    for name, content in remastered.items():
        body, how = encode_data(content, seed, -1)
        rows.append(core.HD_ROW.pack(name.encode().ljust(32, b'\0'), logical, 0x20000010, len(content), how))
        bodies.append(body)
        logical += len(content)
    return seed + b''.join(rows) + packed + b''.join(bodies)


def make_adpcm_bank(wave, count=1):
    fmt, payload = wave_parts(wave)
    channels, rate = struct.unpack_from('<HI', fmt, 2)
    data = bytearray(max(0x100, (0x80 + 4 * count + 15) & ~15))
    data[:8] = b'SEDBSSCF'
    struct.pack_into('<I', data, 8, 4)
    data[13] = 4
    struct.pack_into('<H', data, 14, 0x30)
    struct.pack_into('<H', data, 0x34, count)
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x50:0x68] = b'generated cue table data'
    for index in range(count):
        struct.pack_into('<I', data, 0x80 + 4 * index, len(data))
        data += struct.pack('<8I', len(payload), channels, rate, 12, 0, 0, len(fmt), 0x01000000)
        data += fmt + payload
        data += b'\xcd' * (-len(data) % 16)
    struct.pack_into('<I', data, 16, len(data))
    return bytes(data)


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is needed to generate real Vorbis fixtures')
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_temp = tempfile.TemporaryDirectory()
        root = Path(cls.media_temp.name)
        cls.banks = {}
        for name, rate, channels, tone in (('native', 48000, 1, 440), ('german', 22050, 1, 880),
                                           ('stereo', 48000, 2, 880), ('unusual', 48024, 1, 880),
                                           ('scar_source', 48007, 1, 880)):
            path = root / (name + '.ogg')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            f'sine=frequency={tone}:sample_rate={rate}', '-t', '0.2', '-ac', str(channels),
                            '-c:a', 'libvorbis', str(path)], check=True, capture_output=True)
            cls.banks[name] = make_bank(path.read_bytes(), rate, channels)
            wave = root / (name + '.wav')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            f'sine=frequency={tone}:sample_rate={rate}', '-t', '0.2', '-ac', str(channels),
                            '-c:a', 'adpcm_ms', '-block_size', '32', str(wave)], check=True, capture_output=True)
            cls.banks['adpcm_' + name] = make_adpcm_bank(wave.read_bytes())
            if name == 'native':
                cls.two_sounds = make_bank(path.read_bytes(), rate, channels, 2)
                cls.two_adpcm = make_adpcm_bank(wave.read_bytes(), 2)
            if name == 'german':
                cls.two_german = make_bank(path.read_bytes(), rate, channels, 2)
        path = root / 'quad.ogg'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                        '-t', '0.2', '-af', 'pan=quad|FL=c0|FR=c0|BL=c0|BR=c0',
                        '-c:a', 'libvorbis', str(path)], check=True, capture_output=True)
        quad = bytearray(make_bank(path.read_bytes(), 48000, 4))
        struct.pack_into('<I', quad, 8, 4)
        cls.banks['quad'] = bytes(quad)

    @classmethod
    def tearDownClass(cls):
        cls.media_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.romfs = self.root / 'romfs'
        self.romfs.mkdir()
        self.package = 'kh2_sixth'
        self.asset = 'voice/us/event/sample.win32.scd'
        self.hd_asset = 'obj/P_EX100.a.us'
        self.hd_name = 'voice/battle/us/sora'
        self.title = 'menu/gr/title.2ld'
        self.assets = {
            self.asset: native_asset(self.banks['native']),
            self.hd_asset: native_asset(b'original BAR data', {self.hd_name: self.banks['native']}),
            self.title: native_asset(b'native title data', {f'GR_title_2ld{i}.png': png(0) for i in range(9)}),
            'untouched.bin': native_asset(b'keep this resource')}
        self.write_romfs()
        self.patch = self.root / 'patch.kh2pcpatch'
        self.original = self.package + '/original/' + self.asset
        self.hd = self.package + '/remastered/' + self.hd_asset + '/' + self.hd_name
        self.image = self.package + '/remastered/' + self.title + '/GR_title_2ld0.png'
        self.files = {self.original: self.banks['german']}
        self.write_patch()

    def tearDown(self):
        self.temp.cleanup()

    def write_romfs(self):
        with (self.romfs / (self.package + '.pkg')).open('wb') as pkg, (self.romfs / (self.package + '.hed')).open('wb') as hed:
            hed.write(core.HED_ROW.pack(hashlib.md5(b'empty').digest(), 0, 0, -33686019))
            for asset, raw in self.assets.items():
                hed.write(core.HED_ROW.pack(core.md5_name(asset), pkg.tell(), len(raw), core.PKG_HEADER.unpack(raw[:16])[0]))
                pkg.write(raw)

    def write_patch(self):
        with zipfile.ZipFile(self.patch, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)

    def run_build(self, output=None):
        with tool.PatchSource(self.patch) as source:
            return tool.build(self.romfs, source, output or self.root / 'output')

    def original_bytes(self, destination, asset=None):
        hed, pkg = destination / (self.package + '.hed'), destination / (self.package + '.pkg')
        entry = core.read_hed(hed, pkg.stat().st_size)[core.md5_name(asset or self.asset)]
        with pkg.open('rb') as stream:
            return core.read_original(stream, core.read_header(stream, entry))

    def test_original_scd_and_nested_hd_audio_and_png_are_built_and_verified(self):
        self.files.update({self.hd: self.banks['german'], self.image: png()})
        self.write_patch()
        before = {p.name: core.sha256_file(p) for p in self.romfs.iterdir()}
        dest = self.run_build()
        self.assertEqual(self.original_bytes(dest), self.banks['german'])
        self.assertEqual(before, {p.name: core.sha256_file(p) for p in self.romfs.iterdir()})
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['original_scd_verified'], 1)
        self.assertEqual(report['remastered_audio_verified'], 1)
        self.assertEqual(report['png_files_verified'], 1)
        self.assertFalse((self.root / 'output/work').exists())
        hed, pkg = dest / (self.package + '.hed'), dest / (self.package + '.pkg')
        with pkg.open('rb') as stream:
            header = core.read_header(stream, core.read_hed(hed, pkg.stat().st_size)[core.md5_name(self.hd_asset)])
            self.assertEqual(read_audio(stream, header['headers'][0], bytes.fromhex(header['seed'])), self.banks['german'])

    def test_new_original_scd_is_appended_without_losing_existing_index_order(self):
        extra = 'voice/us/event/new.win32.scd'
        self.files[self.package + '/original/' + extra] = self.banks['german']
        self.write_patch()
        before = list(core.read_hed(self.romfs / (self.package + '.hed'), (self.romfs / (self.package + '.pkg')).stat().st_size))
        dest = self.run_build()
        after = core.read_hed(dest / (self.package + '.hed'), (dest / (self.package + '.pkg')).stat().st_size)
        self.assertEqual(list(after)[:len(before)], before)
        self.assertEqual(self.original_bytes(dest, extra), self.banks['german'])
        self.assertEqual(json.loads((self.root / 'output/build-result.json').read_text())['new_original_scd_added'], 1)

    def test_original_replacement_preserves_both_uncompressed_storage_modes(self):
        for mode in (-2, -1):
            self.assets[self.asset] = native_asset(self.banks['native'], mode=mode)
            self.write_romfs()
            dest = self.run_build(self.root / ('mode-' + str(mode)))
            self.assertEqual(self.original_bytes(dest), self.banks['german'])
            hed, pkg = dest / (self.package + '.hed'), dest / (self.package + '.pkg')
            with pkg.open('rb') as stream:
                header = core.read_header(stream, core.read_hed(hed, pkg.stat().st_size)[core.md5_name(self.asset)])
                self.assertEqual(core.PKG_HEADER.unpack(bytes.fromhex(header['seed']))[2], mode)

    def test_empty_original_index_slot_is_filled_without_duplicate_hash(self):
        extra = 'voice/us/event/new.win32.scd'
        hed = self.romfs / (self.package + '.hed')
        with hed.open('ab') as stream:
            stream.write(core.HED_ROW.pack(core.md5_name(extra), 0, 0, -33686019))
        before_size = hed.stat().st_size
        self.files[self.package + '/original/' + extra] = self.banks['german']
        self.write_patch()
        dest = self.run_build()
        self.assertEqual((dest / hed.name).stat().st_size, before_size)
        self.assertEqual(self.original_bytes(dest, extra), self.banks['german'])

    def test_original_is_relocated_to_actual_native_archive(self):
        self.files = {'kh2_first/original/' + self.asset: self.banks['german']}
        self.write_patch()
        dest = self.run_build()
        self.assertTrue((dest / 'kh2_sixth.pkg').exists())
        self.assertFalse((dest / 'kh2_first.pkg').exists())
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['binding_report']['relocated_source_files'], 1)

    def test_matching_archive_variant_wins_over_conflicting_relocated_pc_variant(self):
        self.files['kh2_first/original/' + self.asset] = self.banks['native']
        self.write_patch()
        dest = self.run_build()
        self.assertEqual(self.original_bytes(dest), self.banks['german'])
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['binding_report']['source_variants'][0]['selected'], self.original)

    def test_roxas_transposed_object_id_is_corrected_only_with_matching_native_hd_name(self):
        target = 'obj/B_EX390.a.us'
        name = 'voice/battle/us/n_roxas'
        self.assets[target] = native_asset(b'Roxas original BAR', {name: self.banks['native']})
        self.write_romfs()
        self.files[self.package + '/remastered/obj/B_EX930.a.us/' + name] = self.banks['german']
        self.write_patch()
        self.run_build()
        report = json.loads((self.root / 'output/build-result.json').read_text())
        bindings = report['binding_report']['bindings']
        self.assertTrue(any(b.get('corrected_asset', {}).get('to') == target for b in bindings))

    def test_roxas_alias_without_expected_native_sound_does_not_guess(self):
        self.assets['obj/B_EX390.a.us'] = native_asset(b'BAR', {'voice/battle/us/other': self.banks['native']})
        self.write_romfs()
        self.files[self.package + '/remastered/obj/B_EX930.a.us/voice/battle/us/n_roxas'] = self.banks['german']
        self.write_patch()
        with self.assertRaisesRegex(ValueError, 'Zuordnung'):
            self.run_build()
        self.assertFalse((self.root / 'output/atmosphere').exists())

    def test_pc_only_title_slots_are_reported_and_native_slots_are_preserved(self):
        self.files.update({self.image: png(), self.package + '/remastered/' + self.title + '/GR_title_2ld9.png': png()})
        self.write_patch()
        self.run_build()
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(len(report['binding_report']['skipped_pc_images']), 1)
        self.assertEqual(report['png_files_verified'], 1)

    def test_stereo_source_is_converted_to_native_mono(self):
        self.files[self.original] = self.banks['stereo']
        self.write_patch()
        dest = self.run_build()
        self.assertEqual(inspect_scd(self.original_bytes(dest))['channels'], [1])
        report = json.loads((self.root / 'output/build-result.json').read_text())
        change = report['scd_checks'][0]['channel_conversions'][0]
        self.assertEqual((change['from_channels'], change['to_channels']), (2, 1))

    def test_missing_sound_slots_are_blocked(self):
        self.assets[self.asset] = native_asset(self.two_sounds)
        self.write_romfs()
        with self.assertRaisesRegex(ValueError, 'Soundplätze'):
            self.run_build()
        self.assertFalse((self.root / 'output/atmosphere').exists())

    def test_corrupt_png_crc_is_rejected(self):
        broken = bytearray(png());broken[25] ^= 1
        self.files[self.image] = bytes(broken)
        self.write_patch()
        with self.assertRaisesRegex(ValueError, 'PNG-Prüfsumme'):
            self.run_build()
        self.assertFalse((self.root / 'output/atmosphere').exists())

    def test_native_diagnosis_reports_original_and_nested_hd_bindings(self):
        self.files[self.hd] = self.banks['german']
        self.write_patch()
        with tool.PatchSource(self.patch) as source:
            report = tool.diagnose(self.romfs, source, self.root / 'diagnosis.zip')
        self.assertTrue(report['inspection']['can_build_supported_patch'])
        self.assertEqual(report['inspection']['original_scd'], 1)
        self.assertEqual(report['inspection']['remastered_audio'], 1)
        self.assertIn(self.package + '/' + self.hd_asset, report['native_headers'])

    def test_bank_checks_accept_valid_vorbis_rates_and_reject_bad_offset(self):
        check = compatible_scd(self.banks['native'], self.banks['german'])
        self.assertEqual(check['replacement_rates'], [22050])
        broken = bytearray(self.banks['german']);struct.pack_into('<I', broken, 0x3c, 0xfffffff0)
        with self.assertRaisesRegex(ValueError, 'Soundtabelle'):
            inspect_scd(bytes(broken))

    def test_reported_eight_hd_name_errors_are_resolved_and_marluxia_is_preserved(self):
        fixture = json.loads((PROJECT / 'tests/fixtures/reported-hd-names.json').read_text())
        for package in {row['package'] for row in fixture['native_assets']}:
            with (self.romfs / (package + '.hed')).open('wb') as hed, \
                 (self.romfs / (package + '.pkg')).open('wb') as pkg:
                for row in fixture['native_assets']:
                    if row['package'] != package:
                        continue
                    raw = native_asset(b'generated native BAR',
                                       {n: self.banks['native'] if n.startswith('voice/') else b'unmodified texture'
                                        for n in row['names']})
                    hed.write(core.HED_ROW.pack(core.md5_name(row['asset']), pkg.tell(), len(raw),
                                               core.PKG_HEADER.unpack(raw[:16])[0]))
                    pkg.write(raw)
        self.files.update({r['path']: self.banks['german'] for r in fixture['source_files']})
        self.write_patch()
        dest = self.run_build()
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['binding_report']['corrected_hd_resources'], 8)
        self.assertEqual(report['binding_report']['errors'], [])
        self.assertEqual(report['remastered_audio_verified'], 8)
        self.assertNotIn('obj/N_CM000_BTL.a.us',
                         {b['asset'] for b in report['binding_report']['bindings'] if not b.get('corrected_resource')})
        asset = 'obj/N_CM000_BTL.a.us'
        old_pkg = self.romfs / 'kh2_fifth.pkg'
        new_pkg = dest / old_pkg.name
        old = core.read_hed(self.romfs / 'kh2_fifth.hed', old_pkg.stat().st_size)[core.md5_name(asset)]
        new = core.read_hed(dest / 'kh2_fifth.hed', new_pkg.stat().st_size)[core.md5_name(asset)]
        with old_pkg.open('rb') as a, new_pkg.open('rb') as b:
            self.assertTrue(core.equal_slices(a, b, old['offset'], new['offset'], old['length']))
        self.assertEqual(sum('Bestätigte doppelte HD-Quelle' == r['reason']
                             for r in report['binding_report']['source_variants']), 3)

    def test_alias_without_exact_native_target_still_blocks_and_lists_names_in_error(self):
        asset = 'obj/N_HB040_BTL.a.us'
        self.assets[asset] = native_asset(b'BAR', {'voice/battle/us/somebody': self.banks['native']})
        self.write_romfs()
        self.files[self.package + '/remastered/' + asset + '/voice/battle/us/stitch'] = self.banks['german']
        self.write_patch()
        with self.assertRaisesRegex(ValueError, 'vorhandene Stimmen: voice/battle/us/somebody'):
            self.run_build()
        self.assertFalse((self.root / 'output/atmosphere').exists())

    def test_conflicting_alias_duplicate_is_not_silently_discarded(self):
        for asset, name in [('obj/N_CM000_BTL.a.us', 'voice/battle/us/n_marluxia'),
                            ('obj/N_DC010_BTL.a.us', 'voice/battle/us/minnie')]:
            self.assets[asset] = native_asset(b'BAR', {name: self.banks['native']})
        self.write_romfs()
        self.files[self.package + '/remastered/obj/N_CM000_BTL.a.us/voice/battle/us/minnie'] = self.banks['native']
        self.files[self.package + '/remastered/obj/N_DC010_BTL.a.us/voice/battle/us/minnie'] = self.banks['german']
        self.write_patch()
        with self.assertRaisesRegex(ValueError, 'Widersprüchliche'):
            self.run_build()
        self.assertFalse((self.root / 'output/atmosphere').exists())

    def test_exact_stitch_resource_has_priority_over_alias(self):
        asset = 'obj/N_HB040_BTL.a.us'
        names = ('voice/battle/us/stitch', 'voice/battle/us/stusch')
        self.assets[asset] = native_asset(b'BAR', {n: self.banks['native'] for n in names})
        self.write_romfs()
        self.files[self.package + '/remastered/' + asset + '/' + names[0]] = self.banks['german']
        self.write_patch()
        self.run_build()
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['binding_report']['corrected_hd_resources'], 0)

    def test_scd4_ms_adpcm_original_and_hd_banks_are_preserved_without_conversion(self):
        self.assets[self.asset] = native_asset(self.banks['adpcm_native'])
        self.assets[self.hd_asset] = native_asset(b'BAR', {self.hd_name: self.banks['adpcm_native']})
        self.write_romfs()
        self.files = {self.original: self.banks['adpcm_german'], self.hd: self.banks['adpcm_german']}
        self.write_patch()
        with mock.patch.object(AudioConverter, 'convert_stream', side_effect=AssertionError('Unexpected conversion')):
            dest = self.run_build()
        self.assertEqual(self.original_bytes(dest), self.banks['adpcm_german'])
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertTrue(all(c['native_version'] == 4 and not c['codec_conversions'] for c in report['scd_checks']))

    def test_codec_conversion_matches_native_ms_adpcm_and_keeps_german_tone_and_cue_data(self):
        source = bytearray(self.two_german)
        source[0x50:0x68] = b'generated cue table data'
        converted, check = AudioConverter().prepare(self.two_adpcm, bytes(source))
        self.assertEqual(converted[0x50:0x68], source[0x50:0x68])
        self.assertEqual(len(check['codec_conversions']), 2)
        self.assertEqual(check['replacement_codecs'], [12])
        self.assertEqual(check['replacement_rates'], [22050])
        info = inspect_scd(converted)
        self.assertEqual(info['entries'], 2)
        path = self.root / 'converted.wav'
        path.write_bytes(wave_stream(converted, info['streams'][0]))
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                              '-f', 's16le', '-acodec', 'pcm_s16le', '-'], check=True, capture_output=True).stdout
        samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
        # Inspect the voiced section; the final standard ADPCM block can be padded.
        samples = samples[100:3300]
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        self.assertAlmostEqual(crossings * 22050 / len(samples), 880, delta=35)

    def test_reverse_codec_conversion_matches_native_vorbis_and_decodes(self):
        converted, check = AudioConverter().prepare(self.banks['native'], self.banks['adpcm_german'])
        self.assertEqual(check['replacement_codecs'], [6])
        path = self.root / 'converted.ogg'
        path.write_bytes(ogg_stream(converted, inspect_scd(converted)['streams'][0]))
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                       check=True, capture_output=True)

    def test_conversion_normalizes_extensible_wave_for_reported_rate_above_48khz(self):
        converted, check = AudioConverter().prepare(self.banks['adpcm_native'], self.banks['unusual'])
        stream = inspect_scd(converted)['streams'][0]
        self.assertEqual(stream['sample_rate'], 48024)
        self.assertEqual(stream['extra_size'], 50)
        self.assertEqual(stream['block_align'], ADPCM_BLOCK_SIZE)
        path = self.root / 'unusual.wav'
        path.write_bytes(wave_stream(converted, stream))
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                       check=True, capture_output=True)

    def test_corrupt_ms_adpcm_format_and_unknown_scd_version_are_rejected(self):
        bank = self.banks['adpcm_german']
        pos = inspect_scd(bank)['streams'][0]['offset']
        for offset, value in ((8, 5), (pos + 32, 1), (pos + 32 + 22, 0)):
            with self.subTest(offset=offset):
                broken = bytearray(bank)
                struct.pack_into('<H', broken, offset, value)
                with self.assertRaises(ValueError):
                    inspect_scd(bytes(broken))

    def test_looped_codec_change_is_blocked(self):
        source = bytearray(self.banks['german'])
        pos = inspect_scd(source)['streams'][0]['offset']
        struct.pack_into('<I', source, pos + 20, 100)
        with self.assertRaisesRegex(ValueError, 'geloopten'):
            AudioConverter().prepare(self.banks['adpcm_native'], bytes(source))

    def test_missing_audio_encoder_blocks_output_and_retains_diagnosis(self):
        self.assets[self.hd_asset] = native_asset(b'BAR', {self.hd_name: self.banks['adpcm_native']})
        self.write_romfs()
        self.files[self.hd] = self.banks['german']
        self.write_patch()
        with mock.patch('scd_audio.MediaTools.find', side_effect=ValueError('ffmpeg fehlt')):
            with self.assertRaisesRegex(ValueError, 'ffmpeg fehlt'):
                self.run_build()
        self.assertFalse((self.root / 'output/atmosphere').exists())
        self.assertTrue((self.root / 'output/KH2-Switch-Diagnose.zip').exists())

    def test_reported_scar_mono_adpcm_is_built_as_quad_vorbis_with_front_dialogue(self):
        fixture = json.loads((PROJECT / 'tests/fixtures/reported-scar-properties.json').read_text())
        self.assets[self.asset] = native_asset(self.banks['quad'])
        self.write_romfs()
        self.files[self.original] = self.banks['adpcm_scar_source']
        self.write_patch()
        dest = self.run_build()
        converted = self.original_bytes(dest)
        info = inspect_scd(converted)
        self.assertEqual(info['channels'], [fixture['native']['channels']])
        self.assertEqual(info['codecs'], [fixture['native']['codec']])
        self.assertEqual(info['sample_rates'], [fixture['german']['sample_rate']])
        path = self.root / 'quad-converted.ogg'
        path.write_bytes(ogg_stream(converted, info['streams'][0]))
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-c:a', 'pcm_f32le',
                              '-f', 'f32le', '-'], check=True, capture_output=True).stdout
        values = struct.unpack('<' + 'f' * (len(pcm) // 4), pcm)
        tracks = [values[i::4] for i in range(4)]
        self.assertLess(max(abs(x) for t in tracks[2:] for x in t), 0.0001)
        self.assertLess(max(abs(a-b) for a, b in zip(tracks[0], tracks[1])), 0.0001)
        crossings = sum(a <= 0 < b for a, b in zip(tracks[0], tracks[0][1:]))
        self.assertAlmostEqual(crossings * 48007 / len(tracks[0]), 880, delta=35)
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['scd_checks'][0]['channel_conversions'][0]['to_channels'], 4)
        self.assertEqual(len(report['scd_checks'][0]['codec_conversions']), 1)

    def test_conversion_cache_distinguishes_mono_and_quad_targets(self):
        converter = AudioConverter()
        mono, _ = converter.prepare(self.banks['native'], self.banks['adpcm_german'])
        quad, _ = converter.prepare(self.banks['quad'], self.banks['adpcm_german'])
        self.assertEqual(inspect_scd(mono)['channels'], [1])
        self.assertEqual(inspect_scd(quad)['channels'], [4])

    def test_preflight_reports_multiple_errors_and_keeps_later_valid_results(self):
        self.files = {self.original: b'broken original SCD', self.hd: b'broken remastered SCD',
                      self.image: png(), self.package + '/original/voice/us/event/zz-good.win32.scd': self.banks['german']}
        self.write_patch()
        with mock.patch.object(core, 'patch_archive') as patch_archive:
            with self.assertRaisesRegex(ValueError, '2 von 4 Patchdateien'):
                self.run_build()
        patch_archive.assert_not_called()
        self.assertFalse((self.root / 'output/atmosphere').exists())
        self.assertFalse((self.root / 'output/work').exists())
        result = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(result['source_files_checked'], 4)
        self.assertEqual(len(result['validation_errors']), 2)
        self.assertEqual(len(result['scd_checks']), 1)
        with zipfile.ZipFile(self.root / 'output/KH2-Switch-Diagnose.zip') as archive:
            report = json.loads(archive.read('diagnose.json'))
            self.assertEqual(len(report['build_failure']['validation_errors']), 2)
            self.assertIn('pc/' + self.original, archive.namelist())
            self.assertIn('pc/' + self.hd, archive.namelist())

    def test_missing_active_slot_cannot_be_filled_by_channel_conversion(self):
        bank = bytearray(self.two_german)
        pos = inspect_scd(bank)['streams'][1]['offset']
        struct.pack_into('<I', bank, pos + 12, 0xffffffff)
        with self.assertRaisesRegex(ValueError, 'keinen Ton.*Soundplatz 1'):
            AudioConverter().prepare(self.two_adpcm, bytes(bank))

    def test_looped_channel_only_change_still_blocks(self):
        source = bytearray(self.banks['german'])
        pos = inspect_scd(source)['streams'][0]['offset']
        struct.pack_into('<I', source, pos + 20, 100)
        with self.assertRaisesRegex(ValueError, 'geloopten'):
            AudioConverter().prepare(self.banks['quad'], bytes(source))

    def test_external_audio_crash_retains_version_phase_stream_and_command(self):
        logs = self.root / 'audio-logs'
        converter = AudioConverter(log_directory=logs)
        converter.context = {'source': 'kh2_fifth/remastered/obj/N_HB570_BTL.a.us/voice/battle/us/tifa'}
        def failed_process(command, **kwargs):
            if '-version' in command:
                return subprocess.CompletedProcess(command, 0, b'ffmpeg test build\n', b'')
            if '-decoders' in command:
                return subprocess.CompletedProcess(command, 0, b' A..... libvorbis libvorbis (codec vorbis)\n', b'')
            return subprocess.CompletedProcess(command, -6, b'', b'*** stack smashing detected ***: terminated')
        with mock.patch('scd_audio.subprocess.run', side_effect=failed_process):
            with self.assertRaisesRegex(AudioToolError, 'PCM-Dekodierung.*Soundplatz 0.*Exit -6'):
                converter.prepare(self.banks['adpcm_native'], self.banks['german'])
        report = json.loads((logs / 'audio-ffmpeg-error-001.json').read_text())
        self.assertEqual(report['context']['stream_index'], 0)
        self.assertIn('tifa', report['context']['source'])
        self.assertIn('ffmpeg test build', report['ffmpeg_version'])
        self.assertIn('stack smashing', report['stderr'])
        self.assertEqual(report['phase'], 'PCM-Dekodierung')
        self.assertIn('pcm_s16le', report['command'])
        self.assertTrue((logs / 'audio-ffmpeg-version.txt').is_file())
        second = json.loads((logs / 'audio-ffmpeg-error-002.json').read_text())
        self.assertEqual(second['attempt'], 2)
        self.assertFalse(second['retry_planned'])
        self.assertFalse(second['recovered'])
        self.assertIn('-cpuflags', second['command'])
        self.assertEqual(len(converter.errors), 2)

    def test_reported_windows_stack_failure_restarts_whole_operation_once(self):
        converter = AudioConverter(log_directory=self.root / 'retry-logs')
        converter.ffmpeg = shutil.which('ffmpeg')
        converter.version = 'reported Windows build'
        converter.context = {'source': 'kh2_fifth/remastered/obj/B_EX160.a.us/voice/battle/us/n_berserker',
                             'stream_index': 5}
        output = self.root / 'temporary-pcm.s16le'
        calls = []
        def process(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                output.write_bytes(b'partial')
                return subprocess.CompletedProcess(command, 3221226505, b'', b'*** stack smashing detected ***: terminated')
            self.assertIn('-y', command)
            self.assertEqual(command[command.index('-cpuflags') + 1], '0')
            output.write_bytes(b'complete PCM result')
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with mock.patch('scd_audio.subprocess.run', side_effect=process):
            converter.run(['-i', 'input.ogg', '-f', 's16le', str(output)], 'PCM-Dekodierung')
        self.assertEqual(len(calls), 2)
        self.assertEqual(output.read_bytes(), b'complete PCM result')
        detail = json.loads((converter.log_directory / 'audio-ffmpeg-error-001.json').read_text())
        self.assertTrue(detail['recovered'])
        self.assertEqual(detail['context']['stream_index'], 5)
        self.assertEqual(detail['returncode'], 3221226505)

    def test_invalid_media_and_timeout_are_never_retried(self):
        converter = AudioConverter()
        converter.ffmpeg = shutil.which('ffmpeg')
        converter.version = 'test build'
        result = subprocess.CompletedProcess([], 1, b'', b'Invalid data found when processing input')
        with mock.patch('scd_audio.subprocess.run', return_value=result) as run:
            with self.assertRaises(AudioToolError):
                converter.run(['-i', 'bad.ogg'], 'PCM-Dekodierung')
        self.assertEqual(run.call_count, 1)
        self.assertFalse(converter.errors[0]['process_crash'])
        self.assertFalse(process_crashed(None, b'stack smashing detected'))

    def test_vorbis_uses_libvorbis_before_input_for_decode_and_result_check(self):
        converter = AudioConverter()
        commands = []
        original_run = converter.run
        def capture(arguments, phase='Audioumwandlung', context=None):
            commands.append((phase, arguments))
            return original_run(arguments, phase, context)
        with mock.patch.object(converter, 'run', side_effect=capture):
            converter.prepare(self.banks['adpcm_native'], self.banks['german'])
            converter.prepare(self.banks['native'], self.banks['adpcm_german'])
        decoder_calls = [(phase, args) for phase, args in commands if 'libvorbis' in args[:args.index('-i')]]
        self.assertEqual([phase for phase, _ in decoder_calls], ['PCM-Dekodierung', 'Ergebnisprüfung'])
        for phase, args in decoder_calls:
            self.assertEqual(args[args.index('-c:a') + 1], 'libvorbis')
            self.assertIn('-guess_layout_max', args[:args.index('-i')])
            if phase == 'PCM-Dekodierung':
                self.assertTrue(args[args.index('-af') + 1].startswith('channelmap='))

    def test_missing_libvorbis_decoder_stops_before_any_conversion(self):
        converter = AudioConverter()
        converter.ffmpeg = shutil.which('ffmpeg')
        converter.version = 'build without external decoder'
        result = subprocess.CompletedProcess([], 0, b' A....D vorbis Vorbis\n', b'')
        with mock.patch('scd_audio.subprocess.run', return_value=result) as run:
            with self.assertRaisesRegex(FileNotFoundError, 'libvorbis-Decoder fehlt'):
                converter.prepare(self.banks['adpcm_native'], self.banks['german'])
        self.assertEqual(run.call_count, 1)
        self.assertIn('-decoders', run.call_args.args[0])

    def test_libvorbis_speaker_order_and_samples_match_independent_native_decoder(self):
        converter = AudioConverter()
        for channels in range(1, 9):
            with self.subTest(channels=channels):
                path = self.root / ('channels-' + str(channels) + '.ogg')
                # Give the LFE a low-frequency tone that survives Vorbis' LFE filter.
                tones = '|'.join('0.08*sin(2*PI*' + str(60 if channels >= 6 and i == 3 else 300 + 173 * i) + '*t)'
                                 for i in range(channels))
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                                'aevalsrc=' + tones + ':s=48000:c=' + CHANNEL_LAYOUTS[channels],
                                '-t', '0.12', '-c:a', 'libvorbis', str(path)], check=True, capture_output=True)
                reference = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-c:a', 'vorbis',
                                            '-i', str(path), '-c:a', 'pcm_f32le', '-f', 'f32le', '-'],
                                           check=True, capture_output=True).stdout
                source = {'channels': channels, 'codec': 6}
                output = self.root / 'mapped.f32le'
                converter.run(converter.decoder_arguments(source) + ['-i', str(path), '-af',
                              converter.input_filter(source, source), '-c:a', 'pcm_f32le', '-f', 'f32le', str(output)])
                decoded = output.read_bytes()
                self.assertEqual(len(reference), len(decoded))
                a = struct.unpack('<' + 'f' * (len(reference) // 4), reference)
                b = struct.unpack('<' + 'f' * (len(decoded) // 4), decoded)
                for i in range(channels):
                    left, right = a[i::channels], b[i::channels]
                    correlation = sum(x*y for x, y in zip(left, right)) / math.sqrt(
                        sum(x*x for x in left) * sum(y*y for y in right))
                    self.assertGreater(correlation, 0.999999)

    def test_recovered_decode_is_fully_rebuilt_and_logged_in_successful_archive(self):
        self.assets[self.hd_asset] = native_asset(b'BAR', {self.hd_name: self.banks['adpcm_native']})
        self.write_romfs()
        self.files[self.hd] = self.banks['german']
        self.write_patch()
        real_run = subprocess.run
        crashed = []
        def crash_first_decode(command, **kwargs):
            if str(command[-1]).endswith('decoded.s16le') and not crashed:
                crashed.append(command)
                Path(command[-1]).write_bytes(b'partial')
                return subprocess.CompletedProcess(command, 3221226505, b'', b'*** stack smashing detected ***: terminated')
            return real_run(command, **kwargs)
        with mock.patch('scd_audio.subprocess.run', side_effect=crash_first_decode):
            dest = self.run_build()
        self.assertEqual(len(crashed), 1)
        report = json.loads((self.root / 'output/build-result.json').read_text())
        self.assertEqual(report['audio_process_crashes_recovered'], 1)
        self.assertTrue(report['audio_ffmpeg_errors'][0]['recovered'])
        self.assertEqual(report['audio_vorbis_decoder'], 'libvorbis')
        hed, pkg = dest / (self.package + '.hed'), dest / (self.package + '.pkg')
        entry = core.read_hed(hed, pkg.stat().st_size)[core.md5_name(self.hd_asset)]
        with pkg.open('rb') as stream:
            header = core.read_header(stream, entry)
            audio = read_audio(stream, header['headers'][0], bytes.fromhex(header['seed']))
        self.assertEqual(inspect_scd(audio)['codecs'], [12])
        path = self.root / 'retry-complete.wav'
        path.write_bytes(wave_stream(audio, inspect_scd(audio)['streams'][0]))
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                       check=True, capture_output=True)

    def test_audio_timeout_is_retained_as_a_conversion_error(self):
        converter = AudioConverter(log_directory=self.root / 'audio-logs')
        converter.ffmpeg = shutil.which('ffmpeg')
        converter.version = 'test version'
        with mock.patch('scd_audio.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', 120, stderr=b'timeout')):
            with self.assertRaisesRegex(AudioToolError, 'Ergebnisprüfung'):
                converter.run(['-i', 'test.wav'], 'Ergebnisprüfung', {'stream_index': 2})
        self.assertIsNone(converter.errors[0]['returncode'])
        self.assertIn('Zeitlimit', converter.errors[0]['stderr'])

    def test_standard_adpcm_blocks_bound_padding_after_pcm_conversion(self):
        converted, check = AudioConverter().prepare(self.banks['adpcm_native'], self.banks['german'])
        item = inspect_scd(converted)['streams'][0]
        self.assertEqual(item['block_align'], 1024)
        self.assertEqual(check['codec_conversions'][0]['adpcm_block_size'], 1024)
        path = self.root / 'padded.wav'
        path.write_bytes(wave_stream(converted, item))
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                              '-f', 's16le', '-acodec', 'pcm_s16le', '-'], check=True, capture_output=True).stdout
        decoded_samples = len(pcm) // 2
        self.assertGreaterEqual(decoded_samples, 4410)
        self.assertLess(decoded_samples - 4410, item['samples_per_block'])


if __name__ == '__main__':
    unittest.main()
