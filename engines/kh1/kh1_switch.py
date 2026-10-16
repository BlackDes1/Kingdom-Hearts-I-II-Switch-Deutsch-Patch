#!/usr/bin/env python3
"""Build a KH1 LayeredFS test mod from a user's archives and Rikux3's assets.

The package itself contains no game audio. Only the specified remastered SCD
payloads are replaced; all other archive bytes and empty index rows are retained.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import shutil
import struct
import sys
import traceback
import zipfile
from pathlib import Path, PurePosixPath

from egs_reader import read_audio, encode_data

HERE = Path(__file__).resolve().parent
DEFAULT_TITLE_ID = "010090C022978001"  # Documented KH1 Program 1 example.
COLLECTION_TITLE_ID = "010090C022978000"
HED_ROW = struct.Struct("<16sqii")
PKG_HEADER = struct.Struct("<iiii")
HD_ROW = struct.Struct("<32siiii")
SAMPLE_LIMIT = 2 * 1024 * 1024
PACKAGES = ("kh1_first", "kh1_third", "kh1_fourth", "kh1_fifth")


def manifest():
    return json.loads((HERE / "source-manifest.json").read_text(encoding="utf-8"))


def groups(data):
    result = collections.defaultdict(lambda: collections.defaultdict(dict))
    for item in data["files"]:
        path = PurePosixPath(item["path"])
        package, category, *tail = path.parts
        if package not in PACKAGES or category != "remastered" or len(tail) < 2:
            raise ValueError("Unerwarteter Pfad im Quellenmanifest: " + str(path))
        result[package]["/".join(tail[:-1])][tail[-1]] = item
    return result


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


def find_archives(root):
    if not root.is_dir():
        raise ValueError("Bitte einen extrahierten ROMFS-Ordner auswählen.")
    candidates = collections.defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".hed" and path.stem.lower() in PACKAGES:
            candidates[path.stem.lower()].append(path)
    result = {}
    for package in PACKAGES:
        paths = candidates.get(package, [])
        if len(paths) != 1:
            raise ValueError(f"{package}.hed: {len(paths)} Treffer. Bitte nur Program 1 / KH1 auswählen.")
        hed = paths[0]
        pkgs = [p for p in hed.parent.iterdir() if p.is_file()
                and p.name.lower() == package + ".pkg"]
        if len(pkgs) != 1:
            raise ValueError("Zugehörige PKG-Datei fehlt: " + package + ".pkg")
        if hed.name != package + ".hed" or pkgs[0].name != package + ".pkg":
            raise ValueError("Archivnamen müssen kleingeschrieben sein: " + package)
        result[package] = (hed, pkgs[0])
    parents = {hed.parent.resolve() for hed, _ in result.values()}
    if len(parents) != 1:
        raise ValueError("Die vier KH1-Archivpaare müssen im selben ROMFS-Ordner liegen.")
    return result


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


def inventory(root):
    return sorted(({"path": p.relative_to(root).as_posix(), "size": p.stat().st_size}
                   for p in root.rglob("*") if p.is_file()), key=lambda x: x["path"])


def diagnose(root, target, title_id="", version="", include_samples=True):
    data = manifest()
    expected = groups(data)
    archives = find_archives(root)
    report = {"tool_version": "2.0", "source_commit": data["commit"],
              "title_id_user_supplied": title_id, "game_version_user_supplied": version,
              "hardware_tested": False, "inventory": inventory(root)}
    try:
        report["inspection"] = inspect_archives(archives, expected)
    except (ValueError, UnicodeError, struct.error) as error:
        report["inspection_error"] = str(error)
    report["samples"] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("Diagnosedatei existiert bereits: " + str(target))
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for package, (hed, pkg) in archives.items():
            archive.write(hed, "hed/" + hed.name)
            if not include_samples:
                continue
            try:
                entries = read_hed(hed, pkg.stat().st_size)
            except ValueError as error:
                report["samples"].append({"package": package, "error": str(error)})
                continue
            candidates = sorted(expected[package], key=lambda p: (
                p != "voice/di_sora.vsb", p != "voice/event/di/di_001.vset", p))
            samples = 0
            with pkg.open("rb") as stream:
                for asset in candidates:
                    entry = entries.get(md5_name(asset))
                    if not entry or not 0 < entry["length"] <= SAMPLE_LIMIT:
                        continue
                    stream.seek(entry["offset"])
                    content = stream.read(entry["length"])
                    name = f"samples/{package}/{entry['hash']}.raw"
                    archive.writestr(name, content)
                    report["samples"].append({"package": package, "asset": asset,
                                              "path": name, "entry": entry})
                    samples += 1
                    if samples == 2:
                        break
        archive.writestr("diagnose.json", json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr("LESEN.txt", "HED-Dateien, Dateiliste und höchstens acht kleine PKG-Proben.\n"
                         "Kein fertiger Mod. Keine Schlüssel oder Tickets erforderlich.\n")
    return report


def stage_mod(source, destination, data):
    """Extract only known files, verifying all hashes; never unpack arbitrary ZIP paths."""
    destination.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        roots = [source] if (source / "kh1_first").is_dir() else [
            p for p in source.iterdir() if p.is_dir() and (p / "kh1_first").is_dir()]
        if len(roots) != 1:
            raise ValueError("Bitte den Rikux3-Modordner mit kh1_first / kh1_third / kh1_fourth / kh1_fifth wählen.")
        root = roots[0]
        for item in data["files"]:
            path = root / item["path"]
            if not path.is_file() or path.is_symlink():
                raise ValueError("Audio fehlt: " + item["path"])
            if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
                raise ValueError("Audio entspricht nicht dem geprüften Quellenstand: " + item["path"])
            target = destination / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    else:
        with zipfile.ZipFile(source) as archive:
            filenames = archive.namelist()
            if len(filenames) != len(set(filenames)):
                raise ValueError("Doppelte ZIP-Einträge; bitte den unveränderten GitHub-Download verwenden.")
            marker = data["files"][0]["path"]
            prefixes = [n[:-len(marker)] for n in filenames if n.endswith(marker)]
            if len(prefixes) != 1:
                raise ValueError("Das ZIP enthält nicht genau einen vollständigen Rikux3-Mod.")
            prefix = prefixes[0]
            for item in data["files"]:
                content = archive.read(prefix + item["path"])
                if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError("Audio entspricht nicht dem geprüften Quellenstand: " + item["path"])
                target = destination / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
    return destination


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


def patch_archive(hed, pkg, staged_package, destination, expected, log):
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
                replaced += rewrite_asset(source, output, row, staged_package / "remastered" / asset, audio)
            else:
                unchanged_segments.append((row["offset"], row["offset"] + row["length"], start))
                copy_slice(source, output, row["offset"], row["length"])
                copied_assets += 1
            new_entries[digest] = dict(row, offset=start, length=output.tell() - start)
            cursor = row["offset"] + row["length"]
            anchors[cursor] = output.tell()
        if cursor < pkg_size:
            unchanged_segments.append((cursor, pkg_size, output.tell()))
            copy_slice(source, output, cursor, pkg_size - cursor)
        anchors[pkg_size] = output.tell()
    empty_count = 0
    for digest, row in entries.items():
        if row["length"]:
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
        for digest in entries:  # Preserve the original HED row order.
            row = new_entries[digest]
            output.write(HED_ROW.pack(digest, row["offset"], row["length"], row["actual_length"]))
    expected_count = sum(len(audio) for audio in expected.values())
    if replaced != expected_count:
        raise ValueError(f"Nur {replaced}/{expected_count} Stimmen in {pkg.stem} eingebaut.")
    log.write_text(json.dumps({"archive": pkg.name, "audio_replacements": replaced,
                              "unmodified_assets_copied": copied_assets, "empty_entries_preserved": empty_count,
                              "source_bytes": pkg_size, "output_bytes": new_pkg.stat().st_size}, indent=2), encoding="utf-8")
    return new_hed, new_pkg


def verify_output(originals, patched, expected, before):
    after = inspect_archives(patched, expected)
    if not after["compatible_names"]:
        raise ValueError("Im erzeugten Archiv fehlen Audionamen.")
    for package, (old_hed, old_pkg) in originals.items():
        new_hed, new_pkg = patched[package]
        old_index = read_hed(old_hed, old_pkg.stat().st_size)
        new_index = read_hed(new_hed, new_pkg.stat().st_size)
        if set(old_index) != set(new_index):
            raise ValueError("Der Patcher hat die Liste der Original-Assets verändert: " + package)
        with old_pkg.open("rb") as old_stream, new_pkg.open("rb") as new_stream:
            expected_hashes = {md5_name(asset) for asset in expected[package]}
            if list(old_index) != list(new_index):
                raise ValueError("HED-Reihenfolge wurde verändert: " + package)
            for digest, old_entry in old_index.items():
                new_entry = new_index[digest]
                if old_entry["actual_length"] != new_entry["actual_length"]:
                    raise ValueError("Original-Längenfeld wurde verändert: " + package)
                if digest not in expected_hashes:
                    if old_entry["length"] != new_entry["length"] or not equal_slices(
                        old_stream, new_stream, old_entry["offset"], new_entry["offset"], old_entry["length"]):
                        raise ValueError("Eine andere PKG-Ressource wurde verändert: " + package)
            for asset, audio in expected[package].items():
                old = before["packages"][package]["assets"][asset]
                new = after["packages"][package]["assets"][asset]
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


def resolve_title_id(title_id):
    if not re.fullmatch(r"0100[0-9A-Fa-f]{12}", title_id):
        raise ValueError("Bitte die 16-stellige Program-ID von KH1 / Program 1 angeben.")
    value = title_id.upper()
    return DEFAULT_TITLE_ID if value == COLLECTION_TITLE_ID else value


def build(root, mod, output, title_id, version=""):
    resolved_title_id = resolve_title_id(title_id)
    if resolved_title_id != title_id.upper():
        print(f"KH1-Program-ID: {resolved_title_id} (Sammlung: {title_id.upper()})", flush=True)
    if output.exists():
        raise ValueError("Bitte einen neuen Ausgabeordner wählen; vorhandene Builds werden nicht überschrieben.")
    data = manifest()
    expected = groups(data)
    originals = find_archives(root)
    before = inspect_archives(originals, expected)
    if not before["compatible_names"]:
        raise ValueError(f"Nur {before['matched_scd']}/{before['expected_scd']} Audionamen passen. "
                         "Bitte zuerst die Diagnose erstellen und hier hochladen.")
    existing_parent = output.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    needed = sum(pkg.stat().st_size for _, pkg in originals.values()) + sum(
        item["size"] for item in data["files"]) + 512 * 1024 * 1024
    if shutil.disk_usage(existing_parent).free < needed:
        raise ValueError(f"Zu wenig Platz: mindestens {needed / 1024**3:.1f} GiB benötigt.")
    output.mkdir(parents=True)
    work = output / "work"
    logs = output / "logs"
    work.mkdir()
    logs.mkdir()
    stage = stage_mod(mod, work / "mod", data)
    patched = {}
    for number, package in enumerate(PACKAGES, 1):
        print(f"[{number}/4] {package}: Switch-Archiv bauen ...", flush=True)
        hed, pkg = originals[package]
        patched[package] = patch_archive(hed, pkg, stage / package, work / "patched" / package,
                                         expected[package], logs / (package + ".json"))
        if not all(p.is_file() and p.stat().st_size for p in patched[package]):
            raise ValueError("Der Patcher hat kein vollständiges Archivpaar erzeugt: " + package)
    print("Archive, Audionamen, Größen und Originaldaten prüfen ...", flush=True)
    after = verify_output(originals, patched, expected, before)
    destination = output / "atmosphere" / "contents" / resolved_title_id / "romfs"
    destination.mkdir(parents=True)
    for paths in patched.values():
        for path in paths:
            shutil.move(str(path), str(destination / path.name))
    shutil.rmtree(work)
    result = {"source_repository": data["repository"], "source_commit": data["commit"],
              "title_id_user_supplied": title_id.upper(), "kh1_program_id": resolved_title_id,
              "version_user_supplied": version,
              "audio_replacements": after["matched_scd"], "fmv_replaced": False,
              "hardware_tested": False, "status": "Archive geprüft; Switch-Praxistest steht aus",
              "checks": ["Quellen-SHA256", "Alle 2326 Namen", "Alle 2326 Ersetzungen",
                         "SHA256 aller eingebauten Audiodateien", "Audio-Dateigrößen",
                         "Original-Asset-Hashes erhalten", "Originaldaten der betroffenen Assets erhalten",
                         "Andere Remastered-Ressourcen der betroffenen Assets erhalten",
                         "Alle anderen PKG-Ressourcen bytegleich erhalten", "Leere HED-Einträge erhalten",
                         "Reihenfolge erhalten"]}
    (output / "build-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def ask_path(prompt, folder=True):
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        value = filedialog.askdirectory(title=prompt) if folder else filedialog.askopenfilename(title=prompt)
        window.destroy()
        if not value:
            raise KeyboardInterrupt()
    except ImportError:
        value = input(prompt + " (Pfad): ").strip().strip('"')
    return Path(value).resolve()


def interactive():
    print("KH1 Switch - Deutsche Stimmen | Baupaket v2\n")
    print("1 = Kleine Diagnose erstellen (für die erste Prüfung empfohlen)")
    print("2 = Atmosphère-Testmod aus deinem ROMFS und dem Rikux3-Mod bauen\n")
    choice = input("Auswahl [1]: ").strip() or "1"
    root = ask_path("Extrahierter ROMFS von KH1 / Program 1")
    title_id = input(f"Program-ID aus DBI [{DEFAULT_TITLE_ID}]: ").strip() or DEFAULT_TITLE_ID
    version = input("Spielversion (optional): ").strip()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if choice == "1":
        target = HERE / ("KH1-Switch-Diagnose-" + stamp + ".zip")
        report = diagnose(root, target, title_id, version)
        print("\nDiagnose erstellt:", target)
        inspection = report.get("inspection", {})
        print("Passende Audionamen:", inspection.get("matched_scd", "unbekannt"), "/ 2326")
        print("Lade diese Diagnose-ZIP zur Prüfung hier hoch.")
    elif choice == "2":
        mod = ask_path("Rikux3-Mod: heruntergeladene ZIP oder .kh1pcpatch", folder=False)
        parent = ask_path("Ausgabeordner mit ausreichend freiem Platz")
        output = parent / ("KH1-Switch-Build-" + stamp)
        destination = build(root, mod, output, title_id, version)
        print("\nTestmod gebaut:", destination)
        print("Den erzeugten atmosphere-Ordner auf die SD-Karte kopieren.")
        print("Die Archive sind geprüft. Ein Test auf der Switch steht noch aus.")
    else:
        raise ValueError("Bitte 1 oder 2 auswählen.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--diagnose", action="store_true")
    mode.add_argument("--build", action="store_true")
    parser.add_argument("--romfs", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mod", type=Path)
    parser.add_argument("--title-id", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--no-samples", action="store_true")
    args = parser.parse_args()
    try:
        if not (args.diagnose or args.build):
            interactive()
        else:
            if not args.romfs or not args.output:
                parser.error("--romfs und --output sind erforderlich.")
            root, output = args.romfs.resolve(), args.output.resolve()
            if args.diagnose:
                diagnose(root, output, args.title_id, args.version, not args.no_samples)
                print(output)
            else:
                if not args.mod or not args.title_id:
                    parser.error("Zum Bauen sind --mod und --title-id erforderlich.")
                print(build(root, args.mod.resolve(), output, args.title_id, args.version))
        return 0
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except Exception as error:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        log = HERE / ("Fehler-" + stamp + ".log")
        try:
            log.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        print("\nFEHLER:", error, file=sys.stderr)
        print("Fehlerbericht:", log, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
