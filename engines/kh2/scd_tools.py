"""SCD structure checks; format references: OpenKh docs and vgmstream sqex_scd.c."""
import struct


def inspect_scd(data):
    if len(data) < 0x50 or data[:8] != b"SEDBSSCF" or data[12] != 0:
        raise ValueError("Keine unterstützte Little-Endian-SCD-Datei.")
    version = struct.unpack_from("<I", data, 8)[0]
    tables = struct.unpack_from("<H", data, 14)[0]
    declared = struct.unpack_from("<I", data, 16)[0]
    if version not in (3, 4) or data[13] != 4 or tables < 0x20 or tables + 0x20 > len(data) or not 0 < declared <= len(data):
        raise ValueError("Ungültiger SCD-Kopf.")
    count = struct.unpack_from("<H", data, tables + 4)[0]
    offsets = struct.unpack_from("<I", data, tables + 12)[0]
    if not 0 < count <= 4096 or offsets < tables + 0x20 or offsets + 4 * count > len(data):
        raise ValueError("Ungültige SCD-Soundtabelle.")
    streams = []
    for index in range(count):
        pos = struct.unpack_from("<I", data, offsets + 4 * index)[0]
        if not pos:
            streams.append(None)
            continue
        if pos + 32 > len(data):
            raise ValueError("SCD-Soundkopf liegt außerhalb der Datei.")
        size, channels, frequency, codec, loop_start, loop_end, extra, auxiliary = struct.unpack_from("<8I", data, pos)
        if codec == 0xffffffff:
            streams.append(None)
            continue
        if not 1 <= channels <= 8 or not 8000 <= frequency <= 192000 or pos + 32 + extra + size > len(data):
            raise ValueError("Ungültige SCD-Sounddaten oder abgeschnittene Audiospur.")
        item = {"index": index, "offset": pos, "size": size, "channels": channels,
                "sample_rate": frequency, "codec": codec, "extra_size": extra,
                "loop_start": loop_start, "loop_end": loop_end, "auxiliary": auxiliary,
                "audio_offset": pos + 32 + extra}
        if codec == 6:
            base = pos + 32
            if auxiliary or extra < 32 or data[base] != 2:
                raise ValueError("Diese SCD-Vorbis-Variante benötigt eine gesonderte Anpassung.")
            seek, vorbis = struct.unpack_from("<II", data, base + 16)
            start = base + 32 + seek
            if extra != 32 + seek + vorbis or start + vorbis + size > len(data):
                raise ValueError("Ungültige SCD-Vorbis-Längen.")
            key = data[base + 2]
            if bytes(b ^ key for b in data[start:start + 4]) != b"OggS":
                raise ValueError("Die SCD enthält keinen passenden Ogg-Vorbis-Kopf.")
            item.update(ogg_offset=start, ogg_size=vorbis + size, ogg_header_size=vorbis, ogg_header_xor=key)
        elif codec == 12:
            base = pos + 32
            if extra < 22 or auxiliary not in (0, 0x01000000):
                raise ValueError("Unbekannte SCD-MS-ADPCM-Zusatzdaten.")
            tag, fmt_channels, fmt_rate, average, block, bits, cb_size, samples, coefficients = struct.unpack_from(
                "<HHIIHHHHH", data, base)
            if (tag != 2 or fmt_channels != channels or fmt_rate != frequency or bits != 4
                    or extra != 18 + cb_size or extra != 22 + 4 * coefficients
                    or not 7 * channels < block <= 8192
                    or samples != (block - 7 * channels) * 2 // channels + 2 or size < 7 * channels):
                raise ValueError("Ungültiger SCD-MS-ADPCM-Formatkopf.")
            standard = ((256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232))
            if coefficients != 7 or tuple(struct.unpack_from("<hh", data, base + 22 + 4 * i)
                                           for i in range(coefficients)) != standard:
                raise ValueError("Unbekannte SCD-MS-ADPCM-Koeffizienten.")
            item.update(block_align=block, samples_per_block=samples)
        else:
            raise ValueError("Nicht unterstützter SCD-Codec: " + str(codec))
        streams.append(item)
    if not any(streams):
        raise ValueError("SCD enthält keine abspielbaren Sounddaten.")
    return {"version": version, "declared_size": declared, "entries": count,
            "active_streams": sum(s is not None for s in streams), "streams": streams,
            "codecs": sorted({s["codec"] for s in streams if s}),
            "sample_rates": sorted({s["sample_rate"] for s in streams if s}),
            "channels": sorted({s["channels"] for s in streams if s})}


def compatible_scd(native, replacement, allow_codec_change=False, allow_channel_change=False):
    old, new = inspect_scd(native), inspect_scd(replacement)
    if new["entries"] < old["entries"]:
        raise ValueError("Der deutsche SCD-Soundbank fehlen native Soundplätze.")
    for index, original in enumerate(old["streams"]):
        if original:
            other = new["streams"][index]
            if other is None:
                raise ValueError("Deutsche SCD enthält keinen Ton für nativen Soundplatz " + str(index))
            if not allow_channel_change and original["channels"] != other["channels"]:
                raise ValueError(f"SCD-Kanalzahl passt nicht bei Soundplatz {index}: "
                                 f"nativ {original['channels']}, deutsch {other['channels']}")
            if not allow_codec_change and original["codec"] != other["codec"]:
                raise ValueError(f"SCD-Codec passt nicht bei Soundplatz {index}: "
                                 f"nativ {original['codec']}, deutsch {other['codec']}")
    return {"native_entries": old["entries"], "replacement_entries": new["entries"],
            "native_rates": old["sample_rates"], "replacement_rates": new["sample_rates"],
            "native_version": old["version"], "replacement_version": new["version"],
            "native_codecs": old["codecs"], "replacement_codecs": new["codecs"],
            "native_channels": old["channels"], "replacement_channels": new["channels"],
            "active_streams": new["active_streams"]}


def ogg_stream(data, item):
    if item["codec"] != 6:
        raise ValueError("Keine Vorbis-Spur.")
    start = item["ogg_offset"]
    result = bytearray(data[start:start + item["ogg_size"]])
    if item["ogg_header_xor"]:
        for index in range(item["ogg_header_size"]):
            result[index] ^= item["ogg_header_xor"]
    return bytes(result)


def wave_stream(data, item):
    """Expose an MS-ADPCM SCD stream as RIFF/WAVE, without decoding or re-encoding."""
    if item["codec"] != 12:
        raise ValueError("Keine MS-ADPCM-Spur.")
    start = item["offset"] + 32
    fmt = data[start:start + item["extra_size"]]
    start = item["audio_offset"]
    return wave_file(fmt, data[start:start + item["size"]])


def wave_file(fmt, payload):
    def chunk(kind, body):
        return kind + struct.pack("<I", len(body)) + body + b"\0" * (len(body) % 2)
    body = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"data", payload)
    return b"RIFF" + struct.pack("<I", len(body)) + body
