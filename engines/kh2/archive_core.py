# SPDX-License-Identifier: Apache-2.0
# Common archive routines from the KH1 Switch v2 workflow. See NOTICE.
import hashlib
import json
import struct
import zlib
from pathlib import Path
from egs_reader import read_audio, encode_data, decode_data, xor_mask
HED_ROW = struct.Struct("<16sqii")
PKG_HEADER = struct.Struct("<iiii")
HD_ROW = struct.Struct("<32siiii")

def md5_name(name):
    return hashlib.md5(name.encode("utf-8")).digest()

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def read_hed(path, pkg_size):
    size = path.stat().st_size
    if not size or size % HED_ROW.size or size > 64 * 1024 * 1024:
        raise ValueError("Keine plausible HED-Datei mit 32-Byte-Einträgen: " + path.name)
    result = {}
    with path.open("rb") as stream:
        while block := stream.read(HED_ROW.size):
            digest, offset, length, actual = HED_ROW.unpack(block)
            if digest in result:
                raise ValueError("Doppelter HED-Hash in " + path.name)
            # Empty entries can carry an uninitialized 0xFDFDFDFD ActualLength.
            # The user's native kh1_fifth has exactly one such entry. Its row
            # is still retained, including that signed placeholder field.
            if offset < 0 or length < 0 or (length > 0 and actual < 0) or offset + length > pkg_size:
                raise ValueError("HED-Eintrag liegt außerhalb der PKG-Datei: " + path.name)
            result[digest] = {"hash": digest.hex(), "offset": offset,
                              "length": length, "actual_length": actual}
    return result

def read_header(stream, entry):
    if entry["length"] < PKG_HEADER.size:
        raise ValueError("PKG-Eintrag enthält keinen vollständigen Asset-Header.")
    stream.seek(entry["offset"])
    seed = stream.read(PKG_HEADER.size)
    original_size, count, compressed_size, created = PKG_HEADER.unpack(seed)
    table_size = count * HD_ROW.size
    if original_size < 0 or not 0 <= count <= 100000 or compressed_size < -2:
        raise ValueError("Unbekanntes PKG-Headerformat; kein ungeprüfter Umbau.")
    if PKG_HEADER.size + table_size > entry["length"]:
        raise ValueError("Remastered-Tabelle liegt außerhalb des PKG-Eintrags.")
    headers = []
    names = set()
    for _ in range(count):
        name_bytes, logical_offset, original_offset, size, packed = HD_ROW.unpack(
            stream.read(HD_ROW.size))
        name = name_bytes.split(b"\0", 1)[0].decode("utf-8")
        if not name or name in names or size < 0 or packed < -2:
            raise ValueError("Ungültiger Remastered-Eintrag im PKG.")
        names.add(name)
        headers.append({"name": name, "offset": logical_offset,
                        "original_offset": original_offset,
                        "size": size, "packed_size": packed})
    original_stored_size = compressed_size if compressed_size >= 0 else original_size
    original_position = entry["offset"] + PKG_HEADER.size + table_size
    end = entry["offset"] + entry["length"]
    if original_position + original_stored_size > end:
        raise ValueError("Originaldaten liegen außerhalb des PKG-Eintrags.")
    cursor = original_position + original_stored_size
    for item in headers:
        cursor = (cursor + 15) & ~15
        stored = item["packed_size"] if item["packed_size"] >= 0 else item["size"]
        item["physical_offset"] = cursor
        item["stored_size"] = (stored + 15) & ~15
        cursor += item["stored_size"]
        if cursor > end:
            raise ValueError("Audiodaten liegen außerhalb des PKG-Eintrags.")
    return {"seed": seed.hex(), "original_size": original_size,
            "original_position": original_position,
            "original_stored_size": original_stored_size,
            "created": created, "headers": headers}

def inspect_archives(archives, expected):
    report = {"compatible_names": True, "packages": {}}
    for package, (hed, pkg) in archives.items():
        entries = read_hed(hed, pkg.stat().st_size)
        result = {"hed_entries": len(entries), "pkg_bytes": pkg.stat().st_size,
                  "missing_assets": [], "assets": {}, "matched_scd": 0}
        with pkg.open("rb") as stream:
            for asset, audio in expected[package].items():
                entry = entries.get(md5_name(asset))
                if not entry or not entry["length"]:
                    result["missing_assets"].append(asset)
                    report["compatible_names"] = False
                    continue
                header = read_header(stream, entry)
                header["entry"] = entry
                present = {x["name"] for x in header["headers"]}
                header["missing_scd"] = sorted(set(audio) - present)
                result["matched_scd"] += len(set(audio) & present)
                if header["missing_scd"]:
                    report["compatible_names"] = False
                result["assets"][asset] = header
        report["packages"][package] = result
    report["matched_scd"] = sum(x["matched_scd"] for x in report["packages"].values())
    report["expected_scd"] = sum(len(audio) for assets in expected.values() for audio in assets.values())
    return report

def copy_slice(source, destination, position, length):
    source.seek(position)
    remaining = length
    while remaining:
        block = source.read(min(remaining, 1024 * 1024))
        if not block:
            raise ValueError("PKG-Datei ist unerwartet abgeschnitten.")
        destination.write(block)
        remaining -= len(block)

def equal_slices(left, right, left_position, right_position, length):
    left.seek(left_position)
    right.seek(right_position)
    remaining = length
    while remaining:
        size = min(remaining, 1024 * 1024)
        a, b = left.read(size), right.read(size)
        if len(a) != size or len(b) != size or a != b:
            return False
        remaining -= size
    return True

def read_original(stream, header):
    stream.seek(header["original_position"])
    raw = stream.read(header["original_stored_size"])
    mode = PKG_HEADER.unpack(bytes.fromhex(header["seed"]))[2]
    return decode_data(raw, bytes.fromhex(header["seed"]), mode, header["original_size"])

def write_original_asset(destination, replacement, header=None):
    """Replace an original-only SCD, or add one as supported by the PC packer."""
    plain = Path(replacement["file"]).read_bytes()
    plain += b"\xcd" * (-len(plain) % 16)
    if header and header["headers"]:
        raise ValueError("Original-SCD mit zusätzlichen HD-Referenzen benötigt eine gesonderte Anpassung.")
    mode = PKG_HEADER.unpack(bytes.fromhex(header["seed"]))[2] if header else -2
    created = header["created"] if header else -1
    raw = zlib.compress(plain, 6) if mode >= 0 else plain
    raw = bytearray(raw + b"\xcd" * (-len(raw) % 16))
    packed_size = len(raw) if mode >= 0 else mode
    seed = PKG_HEADER.pack(len(plain), 0, packed_size, created)
    if mode > -2:
        mask = xor_mask(seed)
        for index in range(min(len(raw), 256)):
            raw[index] ^= mask[index % 16]
    destination.write(seed)
    destination.write(raw)
    return len(plain)

def rewrite_asset(source, destination, entry, staged_asset, audio):
    if entry["offset"] % 16 or destination.tell() % 16:
        raise ValueError("Nicht unterstützte PKG-Asset-Ausrichtung.")
    header = read_header(source, entry)
    source.seek(entry["offset"])
    table = bytearray(source.read(16 + 48 * len(header["headers"])))
    seed = bytes.fromhex(header["seed"])
    logical_offset = 16 + 48 * len(header["headers"]) + header["original_size"]
    payloads = []
    replacements = 0
    original_logical_offset = logical_offset
    for index, row in enumerate(header["headers"]):
        if row["offset"] != original_logical_offset:
            raise ValueError("Unbekannte logische Asset-Offsets; bitte Diagnose hochladen.")
        original_logical_offset += row["size"]
        if row["name"] in audio:
            plain = (staged_asset / row["name"]).read_bytes()
            packed, packed_size = encode_data(plain, seed, row["packed_size"])
            size = len(plain)
            payloads.append(packed)
            replacements += 1
        else:
            size, packed_size = row["size"], row["packed_size"]
            payloads.append(row)
        # Keep the raw name bytes and OriginalAssetOffset untouched.
        row_offset = 16 + index * 48
        struct.pack_into("<i", table, row_offset + 32, logical_offset)
        struct.pack_into("<ii", table, row_offset + 40, size, packed_size)
        logical_offset += size
    if replacements != len(audio):
        raise ValueError("Nicht alle deutschen Audionamen sind im Ziel-Asset vorhanden.")
    destination.write(table)
    # Preserve the compressed/encrypted original asset and its alignment bytes.
    original_end = header["headers"][0]["physical_offset"]
    copy_slice(source, destination, header["original_position"], original_end - header["original_position"])
    for payload in payloads:
        if isinstance(payload, bytes):
            destination.write(payload)
        else:
            copy_slice(source, destination, payload["physical_offset"], payload["stored_size"])
    last = header["headers"][-1]
    tail_start = last["physical_offset"] + last["stored_size"]
    tail_size = entry["offset"] + entry["length"] - tail_start
    copy_slice(source, destination, tail_start, tail_size)
    return replacements

def patch_archive(hed, pkg, staged_package, destination, expected, log, original_replacements=None):
    original_replacements = original_replacements or {}
    pkg_size = pkg.stat().st_size
    entries = read_hed(hed, pkg_size)
    targets = {md5_name(asset): (asset, audio) for asset, audio in expected.items()}
    destination.mkdir(parents=True, exist_ok=True)
    new_hed, new_pkg = destination / hed.name, destination / pkg.name
    new_entries = {}
    anchors = {}
    unchanged_segments = []
    physical = sorted(((digest, row) for digest, row in entries.items() if row["length"]),
                      key=lambda item: item[1]["offset"])
    replaced = 0
    copied_assets = 0
    cursor = 0
    with pkg.open("rb") as source, new_pkg.open("xb") as output:
        for digest, row in physical:
            if row["offset"] < cursor:
                raise ValueError("Überlappende PKG-Einträge; bitte Diagnose hochladen.")
            gap = row["offset"] - cursor
            if gap:
                unchanged_segments.append((cursor, row["offset"], output.tell()))
                copy_slice(source, output, cursor, gap)
            start = output.tell()
            anchors[row["offset"]] = start
            if digest in targets:
                asset, audio = targets[digest]
                if asset in original_replacements:
                    actual = write_original_asset(output, original_replacements[asset], read_header(source, row))
                    replaced += 1
                else:
                    actual = row["actual_length"]
                    replaced += rewrite_asset(source, output, row, staged_package / "remastered" / asset, audio)
            else:
                actual = row["actual_length"]
                unchanged_segments.append((row["offset"], row["offset"] + row["length"], start))
                copy_slice(source, output, row["offset"], row["length"])
                copied_assets += 1
            new_entries[digest] = dict(row, offset=start, length=output.tell() - start, actual_length=actual)
            cursor = row["offset"] + row["length"]
            anchors[cursor] = output.tell()
        if cursor < pkg_size:
            unchanged_segments.append((cursor, pkg_size, output.tell()))
            copy_slice(source, output, cursor, pkg_size - cursor)
        anchors[pkg_size] = output.tell()
        for asset, replacement in sorted(original_replacements.items()):
            digest = md5_name(asset)
            if digest in new_entries:
                continue
            output.write(b"\xcd" * (-output.tell() % 16))
            start = output.tell()
            actual = write_original_asset(output, replacement)
            new_entries[digest] = {"hash": digest.hex(), "offset": start,
                                   "length": output.tell() - start, "actual_length": actual}
            replaced += 1
    empty_count = 0
    for digest, row in entries.items():
        if row["length"] or digest in new_entries:
            continue
        empty_count += 1
        new_offset = anchors.get(row["offset"])
        if new_offset is None:
            for old_start, old_end, new_start in unchanged_segments:
                if old_start <= row["offset"] <= old_end:
                    new_offset = new_start + row["offset"] - old_start
                    break
        if new_offset is None:
            raise ValueError("Leerer PKG-Eintrag liegt innerhalb veränderter Daten; bitte Diagnose hochladen.")
        new_entries[digest] = dict(row, offset=new_offset)
    with new_hed.open("xb") as output:
        order = list(entries) + [digest for digest in new_entries if digest not in entries]
        for digest in order:  # Existing rows retain their order; new originals are appended.
            row = new_entries[digest]
            output.write(HED_ROW.pack(digest, row["offset"], row["length"], row["actual_length"]))
    expected_count = sum(len(audio) for audio in expected.values()) + len(original_replacements)
    if replaced != expected_count:
        raise ValueError(f"Nur {replaced}/{expected_count} Stimmen in {pkg.stem} eingebaut.")
    log.write_text(json.dumps({"archive": pkg.name, "audio_replacements": replaced,
                              "original_replacements": len(original_replacements),
                              "original_assets_added": len(set(new_entries) - set(entries)),
                              "unmodified_assets_copied": copied_assets, "empty_entries_preserved": empty_count,
                              "source_bytes": pkg_size, "output_bytes": new_pkg.stat().st_size}, indent=2), encoding="utf-8")
    return new_hed, new_pkg

def verify_output(originals, patched, expected, before, original_replacements=None):
    original_replacements = original_replacements or {}
    after = inspect_archives(patched, expected)
    if not after["compatible_names"]:
        raise ValueError("Im erzeugten Archiv fehlen Audionamen.")
    for package, (old_hed, old_pkg) in originals.items():
        new_hed, new_pkg = patched[package]
        old_index = read_hed(old_hed, old_pkg.stat().st_size)
        new_index = read_hed(new_hed, new_pkg.stat().st_size)
        original_changes = original_replacements.get(package, {})
        original_hashes = {md5_name(asset): value for asset, value in original_changes.items()}
        if set(old_index) | set(original_hashes) != set(new_index):
            raise ValueError("Der Patcher hat die Liste der Original-Assets verändert: " + package)
        with old_pkg.open("rb") as old_stream, new_pkg.open("rb") as new_stream:
            expected_hashes = {md5_name(asset) for asset in expected[package]}
            if list(old_index) != list(new_index)[:len(old_index)]:
                raise ValueError("HED-Reihenfolge wurde verändert: " + package)
            for digest, old_entry in old_index.items():
                new_entry = new_index[digest]
                if digest not in original_hashes and old_entry["actual_length"] != new_entry["actual_length"]:
                    raise ValueError("Original-Längenfeld wurde verändert: " + package)
                if digest not in expected_hashes:
                    if old_entry["length"] != new_entry["length"] or not equal_slices(
                        old_stream, new_stream, old_entry["offset"], new_entry["offset"], old_entry["length"]):
                        raise ValueError("Eine andere PKG-Ressource wurde verändert: " + package)
            for asset, audio in expected[package].items():
                new = after["packages"][package]["assets"][asset]
                if asset in original_changes:
                    replacement = original_changes[asset]
                    content = read_original(new_stream, new)
                    size = replacement["size"]
                    if (new["headers"] or new["original_size"] != ((size + 15) & ~15)
                            or new["entry"]["actual_length"] != new["original_size"]
                            or hashlib.sha256(content[:size]).hexdigest() != replacement["sha256"]
                            or content[size:] != b"\xcd" * (-size % 16)):
                        raise ValueError("Original-SCD wurde nicht korrekt eingebaut: " + asset)
                    old_entry = old_index.get(md5_name(asset))
                    mode = PKG_HEADER.unpack(bytes.fromhex(new["seed"]))[2]
                    if old_entry and old_entry["length"]:
                        old_header = read_header(old_stream, old_entry)
                        old_mode = PKG_HEADER.unpack(bytes.fromhex(old_header["seed"]))[2]
                        if new["created"] != old_header["created"] or (mode >= 0) != (old_mode >= 0) or (old_mode < 0 and mode != old_mode):
                            raise ValueError("Der native Original-Kompressionsmodus wurde verändert: " + asset)
                    elif new["created"] != -1 or mode != -2:
                        raise ValueError("Neues Original-Asset hat keinen gültigen Header: " + asset)
                    continue
                old = before["packages"][package]["assets"][asset]
                if old["seed"] != new["seed"]:
                    raise ValueError("Original-Asset-Header wurde verändert: " + asset)
                if [h["name"] for h in old["headers"]] != [h["name"] for h in new["headers"]]:
                    raise ValueError("Audio-/Texturreihenfolge wurde verändert: " + asset)
                logical_position = 16 + len(new["headers"]) * 48 + new["original_size"]
                for item in new["headers"]:
                    if item["offset"] != logical_position:
                        raise ValueError("Logischer Remastered-Offset stimmt nicht: " + asset)
                    logical_position += item["size"]
                old_stream.seek(old["original_position"])
                new_stream.seek(new["original_position"])
                length = old["original_stored_size"]
                remaining = length
                while remaining:
                    chunk = min(remaining, 1024 * 1024)
                    if old_stream.read(chunk) != new_stream.read(chunk):
                        raise ValueError("Originaldaten wurden verändert: " + asset)
                    remaining -= chunk
                for old_h, new_h in zip(old["headers"], new["headers"]):
                    if old_h["original_offset"] != new_h["original_offset"]:
                        raise ValueError("Referenz im Original-Asset wurde verändert: " + asset)
                    if new_h["name"] in audio and new_h["size"] != audio[new_h["name"]]["size"]:
                        raise ValueError("Die erwartete Audio-Dateigröße stimmt nicht: " + asset)
                    if new_h["name"] in audio:
                        content = read_audio(new_stream, new_h, bytes.fromhex(new["seed"]))
                        expected_hash = audio[new_h["name"]]["sha256"]
                        if hashlib.sha256(content).hexdigest() != expected_hash:
                            raise ValueError("Die Audiodaten wurden nicht korrekt ersetzt: " + asset + "/" + new_h["name"])
                    else:
                        if old_h["size"] != new_h["size"] or old_h["stored_size"] != new_h["stored_size"]:
                            raise ValueError("Eine andere Remastered-Ressource wurde verändert: " + asset)
                        old_stream.seek(old_h["physical_offset"])
                        new_stream.seek(new_h["physical_offset"])
                        remaining = old_h["stored_size"]
                        while remaining:
                            chunk = min(remaining, 1024 * 1024)
                            if old_stream.read(chunk) != new_stream.read(chunk):
                                raise ValueError("Andere Remastered-Daten wurden verändert: " + asset)
                            remaining -= chunk
    return after
