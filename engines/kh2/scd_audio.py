"""Preserve supported SCD banks, matching native stream codecs when they differ."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from movie_import import MediaTools
from scd_tools import compatible_scd, inspect_scd, ogg_stream, wave_stream, wave_file

ADPCM_BLOCK_SIZE = 1024
CHANNEL_LAYOUTS = {1: "mono", 2: "stereo", 3: "3.0", 4: "quad", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}
# Vorbis channel order -> FFmpeg's standard layout order, per vorbis_data.c.
VORBIS_CHANNEL_MAPS = {1: (0,), 2: (0, 1), 3: (0, 2, 1), 4: (0, 1, 2, 3),
                       5: (0, 2, 1, 3, 4), 6: (0, 2, 1, 5, 3, 4),
                       7: (0, 2, 1, 6, 5, 3, 4), 8: (0, 2, 1, 7, 5, 6, 3, 4)}


def process_crashed(code, stderr):
    """Only retry an abnormal process termination, never an ordinary media error."""
    if code is None:
        return False
    # subprocess reports signals as negative values on POSIX and NTSTATUS on Windows.
    return (code in (-4, -6, -7, -8, -11) or
            (code & 0xffffffff) in (0xc0000005, 0xc000001d, 0xc0000409) or
            b"stack smashing detected" in stderr.lower())


class AudioToolError(ValueError):
    """An external audio process failed; retain its phase and diagnostic detail."""
    def __init__(self, detail):
        self.detail = detail
        context = detail.get("context", {})
        code = detail.get("returncode")
        super().__init__(f"SCD-Audioumwandlung fehlgeschlagen ({detail['phase']}, "
                         f"Soundplatz {context.get('stream_index', '?')}, Exit {code}): " + detail["stderr"][-1500:])


def wave_parts(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Die Audioumwandlung lieferte keine RIFF/WAVE-Datei.")
    result, cursor = {}, 12
    while cursor + 8 <= len(data):
        size = struct.unpack_from("<I", data, cursor + 4)[0]
        end = cursor + 8 + size
        if end > len(data):
            raise ValueError("Abgeschnittene WAVE-Datei nach Audioumwandlung.")
        kind = data[cursor:cursor + 4]
        if kind in (b"fmt ", b"data"):
            if kind in result:
                raise ValueError("Doppelter WAVE-Abschnitt.")
            result[kind] = data[cursor + 8:end]
        cursor = end + size % 2
    if b"fmt " not in result or not result.get(b"data"):
        raise ValueError("Unvollständige WAVE-Datei nach Audioumwandlung.")
    fmt = result[b"fmt "]
    # FFmpeg writes WAVEFORMATEXTENSIBLE above 48 kHz, including fractional
    # playback rates such as 48024 Hz in the supplied Sephiroth bank. The SCD
    # codec stores the classic MS-ADPCM WAVEFORMATEX used by the native game.
    if (len(fmt) >= 40 and struct.unpack_from("<H", fmt)[0] == 0xfffe and
            fmt[24:40] == bytes.fromhex("0200000000001000800000aa00389b71")):
        fmt = (struct.pack("<H", 2) + fmt[2:16] + struct.pack("<H", len(fmt) - 40) + fmt[40:])
    return fmt, result[b"data"]


def vorbis_parts(data):
    cursor = 0
    while cursor + 27 <= len(data) and data[cursor:cursor + 4] == b"OggS":
        segments = data[cursor + 26]
        table_end = cursor + 27 + segments
        end = table_end + sum(data[cursor + 27:table_end])
        if end > len(data):
            break
        if b"\x05vorbis" in data[table_end:end]:
            extra = bytearray(32)
            extra[:2] = b"\x02\x20"
            struct.pack_into("<I", extra, 20, end)
            return bytes(extra) + data[:end], data[end:]
        cursor = end
    raise ValueError("Die Audioumwandlung lieferte keinen vollständigen Vorbis-Kopf.")


class AudioConverter:
    def __init__(self, ffmpeg=None, log_directory=None):
        self.selected = ffmpeg
        self.ffmpeg = None
        self.cache = {}
        self.cache_size = 0
        self.log_directory = Path(log_directory) if log_directory is not None else None
        self.context = {}
        self.version = None
        self.errors = []
        self.vorbis_decoder_checked = False

    def ensure_tool(self):
        if self.ffmpeg is None:
            try:
                self.ffmpeg = MediaTools.find("ffmpeg", self.selected)
            except ValueError as error:
                raise FileNotFoundError(str(error)) from error
            try:
                probe = subprocess.run([self.ffmpeg, "-version"], capture_output=True, timeout=10)
                self.version = (probe.stdout + probe.stderr)[:32768].decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                self.version = "FFmpeg-Versionsabfrage hat das Zeitlimit überschritten."
            if self.log_directory:
                self.log_directory.mkdir(parents=True, exist_ok=True)
                (self.log_directory / "audio-ffmpeg-version.txt").write_text(self.version, encoding="utf-8")

    def write_error(self, index, detail):
        if self.log_directory:
            self.log_directory.mkdir(parents=True, exist_ok=True)
            path = self.log_directory / (f"audio-ffmpeg-error-{index:03d}.json")
            path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")

    def decoder_arguments(self, stream):
        if stream["codec"] != 6:
            return []
        self.ensure_tool()
        if not self.vorbis_decoder_checked:
            try:
                result = subprocess.run([self.ffmpeg, "-hide_banner", "-decoders"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired as error:
                raise FileNotFoundError("FFmpeg-Decoderabfrage hat das Zeitlimit überschritten.") from error
            decoders = {line.split()[1] for line in result.stdout.decode("utf-8", "replace").splitlines()
                        if len(line.split()) >= 2 and line.split()[0].startswith("A")}
            if result.returncode or "libvorbis" not in decoders:
                raise FileNotFoundError("FFmpeg mit libvorbis-Decoder fehlt. Bitte ein vollständiges FFmpeg-Paket "
                                        "mit libvorbis in bin ablegen; siehe bin/HIER-FFMPEG.txt.")
            self.vorbis_decoder_checked = True
        # libvorbis returns channels in Vorbis order without a speaker layout.
        # Do not guess one before decoding; channelmap labels/reorders the frames.
        return ["-c:a", "libvorbis", "-guess_layout_max", "0"]

    def run(self, arguments, phase="Audioumwandlung", context=None):
        self.ensure_tool()
        recorded = []
        for attempt in (1, 2):
            # All converter outputs are private temporary files. Overwrite a partial
            # file after a crash and run the entire operation again in a fresh process.
            command = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-xerror"]
            if attempt == 2:
                command += ["-cpuflags", "0"]
            command += arguments
            try:
                result = subprocess.run(command, capture_output=True, timeout=120)
                if not result.returncode:
                    for index, detail in recorded:
                        detail["recovered"] = True
                        self.write_error(index, detail)
                    return
                code, stderr = result.returncode, result.stderr
            except subprocess.TimeoutExpired as error:
                code, stderr = None, (error.stderr or b"") + b"\nFFmpeg-Zeitlimit (120 Sekunden)."
            crash = process_crashed(code, stderr)
            detail = {"phase": phase, "context": dict(self.context if context is None else context),
                      "command": command, "returncode": code, "attempt": attempt,
                      "process_crash": crash, "retry_planned": crash and attempt == 1, "recovered": False,
                      "stderr": stderr[-32768:].decode("utf-8", "replace"), "ffmpeg_version": self.version}
            self.errors.append(detail)
            index = len(self.errors)
            recorded.append((index, detail))
            self.write_error(index, detail)
            if not detail["retry_planned"]:
                raise AudioToolError(detail)
            print(f"FFmpeg-Prozessabsturz ({phase}, Soundplatz "
                  f"{detail['context'].get('stream_index', '?')}); einmalige Wiederholung mit CPU-Basispfad.", flush=True)

    @staticmethod
    def channel_filter(source_channels, target_channels):
        layouts = {1: ("mono", ("FC",)), 2: ("stereo", ("FL", "FR")),
                   3: ("3.0", ("FL", "FR", "FC")), 4: ("quad", ("FL", "FR", "BL", "BR")),
                   5: ("5.0", ("FL", "FR", "FC", "BL", "BR")),
                   6: ("5.1", ("FL", "FR", "FC", "LFE", "BL", "BR")),
                   7: ("6.1", ("FL", "FR", "FC", "LFE", "BC", "SL", "SR")),
                   8: ("7.1", ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"))}
        layout, names = layouts[target_channels]
        if source_channels == target_channels:
            return "asetpts=N/SR/TB,aformat=channel_layouts=" + layout
        if source_channels == 1:
            # Keep a mono dialogue in the front centre. Quad has no centre
            # channel, so use an equal-power front L/R pair with silent rears.
            gains = {"FC": "c0"} if "FC" in names else {"FL": "0.70710678*c0", "FR": "0.70710678*c0"}
            return "asetpts=N/SR/TB,pan=" + layout + "|" + "|".join(
                n + "=" + gains.get(n, "0*c0") for n in names)
        if source_channels == 2 and target_channels > 2:
            return "asetpts=N/SR/TB,pan=" + layout + "|" + "|".join(
                n + "=" + {"FL": "c0", "FR": "c1"}.get(n, "0*c0") for n in names)
        # Let FFmpeg downmix a supported multichannel layout, including its LFE policy.
        return "asetpts=N/SR/TB,aformat=channel_layouts=" + layout

    @classmethod
    def input_filter(cls, source, target):
        remix = cls.channel_filter(source["channels"], target["channels"])
        if source["codec"] == 6:
            mapping = "|".join(map(str, VORBIS_CHANNEL_MAPS[source["channels"]]))
            remix = ("channelmap=map=" + mapping + ":channel_layout=" +
                     CHANNEL_LAYOUTS[source["channels"]] + "," + remix)
        return remix

    def convert_stream(self, data, source, target):
        if any(s[key] for s in (source, target) for key in ("loop_start", "loop_end")):
            raise ValueError("SCD-Codec- oder Kanalwechsel einer geloopten Spur benötigt eine gesonderte Anpassung.")
        if target["codec"] == 12 and target["channels"] > 2:
            raise ValueError("MS-ADPCM-Umwandlung unterstützt nur Mono oder Stereo als Ziel.")
        with tempfile.TemporaryDirectory(prefix="kh2-audio-") as folder:
            folder = Path(folder)
            input_path = folder / ("input.ogg" if source["codec"] == 6 else "input.wav")
            input_path.write_bytes(ogg_stream(data, source) if source["codec"] == 6 else wave_stream(data, source))
            pcm = folder / "decoded.s16le"
            output = folder / ("output.wav" if target["codec"] == 12 else "output.ogg")
            encoder = (["-c:a", "adpcm_ms", "-block_size", str(ADPCM_BLOCK_SIZE), "-f", "wav"] if target["codec"] == 12 else
                       ["-c:a", "libvorbis", "-q:a", "5", "-f", "ogg"])
            context = dict(self.context, stream_index=source["index"], from_codec=source["codec"],
                           to_codec=target["codec"], from_channels=source["channels"],
                           to_channels=target["channels"], sample_rate=source["sample_rate"])
            # Decode/remix once to timestamp-free PCM. Keep the compressed
            # decoder and the target encoder in separate FFmpeg processes.
            decoder = self.decoder_arguments(source)
            remix = self.input_filter(source, target)
            self.run(["-protocol_whitelist", "file", "-threads", "1"] + decoder + ["-i", str(input_path),
                      "-map", "0:a:0", "-vn",
                      "-af", remix,
                      "-ar", str(source["sample_rate"]), "-ac", str(target["channels"]),
                      "-c:a", "pcm_s16le", "-f", "s16le", str(pcm)], "PCM-Dekodierung", context)
            if not pcm.stat().st_size or pcm.stat().st_size % (2 * target["channels"]):
                raise ValueError("Die SCD-Dekodierung lieferte keine vollständigen PCM-Samples.")
            layout = CHANNEL_LAYOUTS[target["channels"]]
            self.run(["-protocol_whitelist", "file", "-f", "s16le", "-ar", str(source["sample_rate"]),
                      "-ac", str(target["channels"]), "-channel_layout", layout, "-i", str(pcm),
                      "-map", "0:a:0", "-threads", "1", "-ar", str(source["sample_rate"]),
                      "-ac", str(target["channels"]), "-channel_layout", layout] + encoder + [str(output)],
                     "MS-ADPCM-Kodierung" if target["codec"] == 12 else "Vorbis-Kodierung", context)
            if target["codec"] == 12:
                # Normalize extensible WAVE before the decode check as well as
                # before embedding. The native SCD carries a classic fmt chunk.
                extra, payload = wave_parts(output.read_bytes())
                output.write_bytes(wave_file(extra, payload))
            # Decode the complete encoded result before putting it back into the bank.
            output_decoder = self.decoder_arguments(target)
            self.run(["-protocol_whitelist", "file", "-threads", "1"] + output_decoder +
                     ["-i", str(output), "-f", "null", "-"],
                     "Ergebnisprüfung", context)
            encoded = output.read_bytes()
        extra, payload = wave_parts(encoded) if target["codec"] == 12 else vorbis_parts(encoded)
        return (struct.pack("<8I", len(payload), target["channels"], source["sample_rate"], target["codec"],
                            0, 0, len(extra), target["auxiliary"]) + extra + payload)

    def prepare(self, native, replacement):
        compatible_scd(native, replacement, allow_codec_change=True, allow_channel_change=True)
        old, new = inspect_scd(native), inspect_scd(replacement)
        changes = [i for i, item in enumerate(old["streams"])
                   if item and (item["codec"], item["channels"]) !=
                   (new["streams"][i]["codec"], new["streams"][i]["channels"])]
        conversions = [{"index": i, "from_codec": new["streams"][i]["codec"],
                        "to_codec": old["streams"][i]["codec"],
                        "from_channels": new["streams"][i]["channels"],
                        "to_channels": old["streams"][i]["channels"],
                        "adpcm_block_size": ADPCM_BLOCK_SIZE if old["streams"][i]["codec"] == 12 else None}
                       for i in changes]
        if changes:
            key = hashlib.sha256(replacement).digest(), tuple((i, old["streams"][i]["codec"],
                                                              old["streams"][i]["channels"],
                                                              old["streams"][i]["loop_start"],
                                                              old["streams"][i]["loop_end"],
                                                              old["streams"][i]["auxiliary"]) for i in changes)
            prepared = self.cache.get(key)
            if prepared is None:
                if self.context.get("source"):
                    print(f"Sprachformat anpassen: {self.context['source']} ({len(changes)} Spuren)", flush=True)
                # Keep every cue/info table and all unconverted stream offsets untouched.
                # Redirect only converted sound-table entries to newly appended streams.
                prepared = bytearray(replacement)
                tables = struct.unpack_from("<H", replacement, 14)[0]
                offsets = struct.unpack_from("<I", replacement, tables + 12)[0]
                for i in changes:
                    prepared += b"\xcd" * (-len(prepared) % 16)
                    struct.pack_into("<I", prepared, offsets + 4 * i, len(prepared))
                    prepared += self.convert_stream(replacement, new["streams"][i], old["streams"][i])
                prepared += b"\xcd" * (-len(prepared) % 16)
                struct.pack_into("<I", prepared, 16, len(prepared))
                prepared = bytes(prepared)
                if len(prepared) <= 32 * 1024 ** 2:
                    if self.cache_size + len(prepared) > 32 * 1024 ** 2:
                        self.cache.clear()
                        self.cache_size = 0
                    self.cache[key] = prepared
                    self.cache_size += len(prepared)
            replacement = prepared
        check = compatible_scd(native, replacement)
        check["codec_conversions"] = [c for c in conversions if c["from_codec"] != c["to_codec"]]
        check["channel_conversions"] = [c for c in conversions if c["from_channels"] != c["to_channels"]]
        check["audio_conversions"] = conversions
        return replacement, check
