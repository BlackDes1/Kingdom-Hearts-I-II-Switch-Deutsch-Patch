#!/usr/bin/env python3
"""Import local Nexus KH2 voices or movies for the native Switch release."""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import shutil
import stat
import struct
import tempfile
import traceback
import zipfile
from pathlib import Path, PurePosixPath

import archive_core as core
import movie_import
import native_patch
from egs_reader import decode_data, read_audio

HERE = Path(__file__).resolve().parent
TITLE_ID = "010090C022978003"
COLLECTION_ID = "010090C022978000"
PACKAGES = tuple("kh2_" + word for word in ("first", "second", "third", "fourth", "fifth", "sixth"))
SOURCE_URL = "https://www.nexusmods.com/kingdomhearts2finalmix/mods/42"
MEMBER = re.compile(r"(?:^|/)(kh2_(?:first|second|third|fourth|fifth|sixth))/(original|remastered|raw)/(.+)$", re.I)
SAMPLE_LIMIT = 2 * 1024 * 1024
SAMPLE_BUDGET = 32 * 1024 * 1024


class PatchSource:
    """Read a patch ZIP, Nexus wrapper ZIP or extracted directory without executing it."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.archive = None
        self.temp = None
        self.records = {}
        self.nested_name = None
        self.outside_payload = []

    def __enter__(self):
        try:
            self._open()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.archive:
            self.archive.close()
        if self.temp:
            self.temp.cleanup()

    @staticmethod
    def canonical(name):
        name = name.replace("\\", "/")
        match = MEMBER.search(name)
        if not match:
            return None
        package, category, tail = match.groups()
        parts = PurePosixPath(tail).parts
        if not parts or any(p in ("..", ".") or ":" in p or "\0" in p for p in parts):
            raise ValueError("Ungültiger Pfad im Modarchiv.")
        return package.lower() + "/" + category.lower() + "/" + "/".join(parts)

    def _collect(self):
        self.records = {}
        self.outside_payload = []
        if self.archive:
            candidates = ((item.filename, item) for item in self.archive.infolist() if not item.is_dir())
        else:
            candidates = ((p.relative_to(self.path).as_posix(), p) for p in self.path.rglob("*") if p.is_file())
        for name, handle in candidates:
            canonical = self.canonical(name)
            if not canonical:
                self.outside_payload.append(name)
                continue
            if canonical in self.records:
                raise ValueError("Doppelter Modpfad: " + canonical)
            if isinstance(handle, Path):
                if handle.is_symlink():
                    raise ValueError("Verknüpfungen bitte vor dem Import auflösen: " + canonical)
                size, crc = handle.stat().st_size, None
            else:
                if stat.S_ISLNK(handle.external_attr >> 16):
                    raise ValueError("ZIP-Verknüpfung wird nicht importiert: " + canonical)
                size, crc = handle.file_size, handle.CRC
            self.records[canonical] = {"path": canonical, "size": size, "zip_crc32": crc,
                                       "handle": handle}

    def _open(self):
        if self.path.is_dir():
            self._collect()
            if not self.records:
                inner = list(self.path.rglob("*.kh2pcpatch"))
                if len(inner) == 1:
                    self.nested_name = inner[0].name
                    self.archive = zipfile.ZipFile(inner[0])
                    self._collect()
        elif zipfile.is_zipfile(self.path):
            self.archive = zipfile.ZipFile(self.path)
            self._collect()
            if not self.records:
                inner = [i for i in self.archive.infolist() if i.filename.lower().endswith(".kh2pcpatch")]
                if len(inner) != 1:
                    raise ValueError("Bitte die Hauptdatei 'German voices 2.1' oder genau eine .kh2pcpatch auswählen.")
                self.nested_name = PurePosixPath(inner[0].filename).name
                self.temp = tempfile.TemporaryDirectory(prefix="kh2-patch-")
                if shutil.disk_usage(self.temp.name).free < inner[0].file_size + 64 * 1024 * 1024:
                    raise ValueError("Zu wenig temporärer Platz zum Öffnen der Nexus-ZIP.")
                target = Path(self.temp.name) / "source.kh2pcpatch"
                print("Nexus-ZIP öffnen: " + self.nested_name, flush=True)
                with self.archive.open(inner[0]) as stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                self.archive.close()
                self.archive = zipfile.ZipFile(target)
                self._collect()
        else:
            raise ValueError("Bitte eine ZIP, .kh2pcpatch oder einen entpackten Modordner auswählen.")
        if not self.records:
            raise ValueError("Keine KH2-Patchdateien gefunden. Benötigt wird die Hauptdatei 'German voices 2.1'.")

    def read(self, canonical):
        record = self.records[canonical]
        if self.archive:
            return self.archive.read(record["handle"])
        return record["handle"].read_bytes()

    def summary(self):
        return {"selected_name": self.path.name, "nested_patch_name": self.nested_name,
                "intended_mod_page": SOURCE_URL, "nexus_main_file_id": 811,
                "files_outside_standard_payload": self.outside_payload,
                "files": [{k: v for k, v in item.items() if k != "handle"}
                          for _, item in sorted(self.records.items())]}

    def targets(self):
        result = collections.defaultdict(dict)
        for canonical, record in self.records.items():
            package, category, tail = canonical.split("/", 2)
            if category == "remastered":
                asset, separator, name = tail.rpartition("/")
                if not separator:
                    asset, name = tail, ""
            else:
                asset, name = tail, ""
            item = result[package].setdefault(asset, {"scd": {}, "other": []})
            if category == "remastered" and name.lower().endswith(".scd"):
                item["scd"][name] = dict(record, handle=None)
            else:
                item["other"].append(canonical)
        return dict(result)


def resolve_title_id(value):
    if not re.fullmatch(r"0100[0-9a-fA-F]{12}", value):
        raise ValueError("Bitte eine 16-stellige Program-ID angeben.")
    value = value.upper()
    if value == COLLECTION_ID:
        return TITLE_ID
    if value.startswith("010090C02297800") and value != TITLE_ID:
        raise ValueError("Für KH2 wird Program 3 benötigt: " + TITLE_ID)
    return value


def find_archives(root):
    if not root.is_dir():
        raise ValueError("Bitte den extrahierten KH2-ROMFS-Ordner / Program 3 auswählen.")
    found = collections.defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".hed" and path.stem.lower() in PACKAGES:
            found[path.stem.lower()].append(path)
    if not found:
        raise ValueError("Keine kh2_*.hed gefunden. KH2 ist Program 3; bitte diesen ROMFS auswählen.")
    result = {}
    for package, values in found.items():
        if len(values) != 1:
            raise ValueError("Mehrere Fassungen von " + package + ".hed gefunden; bitte nur einen ROMFS auswählen.")
        hed = values[0]
        pkg = hed.with_suffix(".pkg")
        if not pkg.is_file():
            raise ValueError("Zugehörige PKG fehlt: " + pkg.name)
        result[package] = (hed, pkg)
    if len({h.parent.resolve() for h, _ in result.values()}) != 1:
        raise ValueError("Die KH2-Archivpaare müssen aus demselben ROMFS-Ordner stammen.")
    return dict(sorted(result.items()))


def inspect(archives, targets):
    report = {"compatible_names": True, "packages": {}, "source_extra_files": [],
              "missing_packages": sorted(set(targets) - set(archives))}
    headers = {}
    if report["missing_packages"]:
        report["compatible_names"] = False
    for package, assets in targets.items():
        for item in assets.values():
            report["source_extra_files"].extend(item["other"])
    for package, (hed, pkg) in archives.items():
        row = {"pkg_bytes": pkg.stat().st_size, "assets": {}, "errors": [], "matched_scd": 0}
        report["packages"][package] = row
        try:
            index = core.read_hed(hed, pkg.stat().st_size)
            row["hed_entries"] = len(index)
            row["empty_entries"] = sum(e["length"] == 0 for e in index.values())
            with pkg.open("rb") as stream:
                for asset, expected in targets.get(package, {}).items():
                    entry = index.get(core.md5_name(asset))
                    if not entry or not entry["length"]:
                        row["assets"][asset] = {"missing_asset": True}
                        report["compatible_names"] = False
                        continue
                    try:
                        header = core.read_header(stream, entry)
                        header["entry"] = entry
                        headers[(package, asset)] = header
                        present = {h["name"] for h in header["headers"]}
                        missing = sorted(set(expected["scd"]) - present)
                        row["matched_scd"] += len(set(expected["scd"]) & present)
                        row["assets"][asset] = dict(header, missing_scd=missing)
                        if missing:
                            report["compatible_names"] = False
                    except (ValueError, UnicodeError, struct.error) as error:
                        row["assets"][asset] = {"error": str(error)}
                        report["compatible_names"] = False
        except (ValueError, OSError, struct.error) as error:
            row["errors"].append(str(error))
            report["compatible_names"] = False
    report["matched_scd"] = sum(p["matched_scd"] for p in report["packages"].values())
    report["expected_scd"] = sum(len(i["scd"]) for a in targets.values() for i in a.values())
    report["requires_original_or_other_file_support"] = bool(report["source_extra_files"])
    report["can_build_audio_only"] = bool(report["expected_scd"]) and report["compatible_names"] and not report["source_extra_files"]
    return report, headers


def diagnose(root, source, target, title_id=TITLE_ID, version=""):
    if native_patch.relevant(source):
        return native_patch.diagnose(root, source, target, resolve_title_id(title_id), version, find_archives(root))
    archives = find_archives(root)
    targets = source.targets()
    inspection, headers = inspect(archives, targets)
    external_code = [name for name in source.outside_payload
                     if PurePosixPath(name.replace("\\", "/")).suffix.lower()
                     in (".lua", ".dll", ".exe", ".ips", ".pchtxt")]
    if external_code:
        inspection["source_extra_files"].extend(external_code)
        inspection["requires_original_or_other_file_support"] = True
        inspection["can_build_audio_only"] = False
    report = {"tool_version": "KH2-9.0", "hardware_tested": False,
              "title_id_user_supplied": title_id, "kh2_program_id": resolve_title_id(title_id),
              "game_version_user_supplied": version, "patch": source.summary(),
              "inventory": sorted([{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size}
                                   for p in root.rglob("*") if p.is_file()], key=lambda x: x["path"]),
              "inspection": inspection, "samples": []}
    target.parent.mkdir(parents=True, exist_ok=True)
    budget = SAMPLE_BUDGET
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as output:
        def add(name, content):
            nonlocal budget
            if len(content) > budget:
                return False
            output.writestr(name, content)
            budget -= len(content)
            return True
        for package, (hed, pkg) in archives.items():
            output.write(hed, "hed/" + hed.name)
            candidates = [(asset, data) for (pkg_name, asset), data in headers.items() if pkg_name == package]
            candidates.sort(key=lambda pair: (not bool(targets[package][pair[0]]["other"]),
                                              not any(term in pair[0].lower() for term in ("p_ex100", "sora", "roxas", "axel", "sephiroth")),
                                              not ("battle" in pair[0] or pair[0].startswith("obj/")), pair[0]))
            with pkg.open("rb") as stream:
                for asset, header in candidates[:2]:
                    entry = header["entry"]
                    sample = {"package": package, "asset": asset, "entry": entry, "files": []}
                    root_name = "native/" + package + "/" + entry["hash"]
                    if entry["length"] <= SAMPLE_LIMIT:
                        stream.seek(entry["offset"])
                        raw = stream.read(entry["length"])
                        if add(root_name + ".raw", raw):
                            sample["files"].append(root_name + ".raw")
                    elif header["original_size"] <= 8 * 1024 * 1024 and header["original_stored_size"] <= 8 * 1024 * 1024:
                        stream.seek(header["original_position"])
                        raw = stream.read(header["original_stored_size"])
                        packed_size = struct.unpack_from("<i", bytes.fromhex(header["seed"]), 8)[0]
                        original = decode_data(raw, bytes.fromhex(header["seed"]), packed_size, header["original_size"])
                        if add(root_name + ".original", original):
                            sample["files"].append(root_name + ".original")
                    desired = targets[package][asset]
                    pc_names = desired["other"] + [i["path"] for _, i in sorted(desired["scd"].items())[:2]]
                    for name in pc_names:
                        limit = 8 * 1024 * 1024 if "/original/" in name or "/raw/" in name else SAMPLE_LIMIT
                        if source.records[name]["size"] <= limit:
                            content = source.read(name)
                            if add("pc/" + name, content):
                                sample["files"].append("pc/" + name)
                    sample["header"] = header
                    report["samples"].append(sample)
        # Include small configuration files even if their native asset is absent.
        for canonical, record in source.records.items():
            if canonical.lower().endswith(("assets.config", ".txt")) and record["size"] <= 128 * 1024:
                name = "config/" + canonical
                add(name, source.read(canonical))
        report["sample_bytes"] = SAMPLE_BUDGET - budget
        output.writestr("diagnose.json", json.dumps(report, ensure_ascii=False, indent=2))
        output.writestr("LESEN.txt", "KH2-ROMFS und lokal ausgewählter Nexus-Patch.\n"
                         "Die Patchdatei selbst ist nicht vollständig enthalten.\n"
                         "Die Diagnose-ZIP im Chat hochladen; Originaldateien bleiben erhalten.\n")
    return report


def build(root, source, output, title_id=TITLE_ID, version="", ffmpeg=None):
    if native_patch.relevant(source):
        return native_patch.build(root, source, output, resolve_title_id(title_id), version, find_archives(root), ffmpeg)
    if output.exists():
        raise ValueError("Bitte einen neuen Ausgabeordner wählen.")
    program_id = resolve_title_id(title_id)
    output.mkdir(parents=True)
    print("KH2-Program-ID: " + program_id, flush=True)
    print("Patch und Switch-Dateien prüfen ...", flush=True)
    diagnostic = output / "KH2-Switch-Diagnose.zip"
    report = diagnose(root, source, diagnostic, title_id, version)
    inspection = report["inspection"]
    if not inspection["can_build_audio_only"]:
        raise ValueError("Dieser Patch benötigt weitere Dateianpassungen oder abweichende Namen. "
                         "Bitte die erzeugte KH2-Switch-Diagnose.zip zur Anpassung hochladen: " + str(diagnostic))
    targets = source.targets()
    all_archives = find_archives(root)
    originals = {p: all_archives[p] for p in targets}
    needed = sum(p.stat().st_size for _, p in originals.values()) + 2 * sum(
        r["size"] for r in source.records.values()) + 512 * 1024 * 1024
    if shutil.disk_usage(output).free < needed:
        raise ValueError(f"Zu wenig freier Platz: mindestens {needed / 1024**3:.1f} GiB erforderlich.")
    work, logs = output / "work", output / "logs"
    work.mkdir()
    logs.mkdir()
    expected = collections.defaultdict(lambda: collections.defaultdict(dict))
    for number, (canonical, record) in enumerate(source.records.items(), 1):
        content = source.read(canonical)
        if content[:8] != b"SEDBSSCF":
            raise ValueError("Keine SCD-Audiodatei: " + canonical)
        path = work / "mod" / canonical
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        package, _, rest = canonical.split("/", 2)
        asset, _, name = rest.rpartition("/")
        expected[package][asset][name] = {"path": canonical, "size": len(content),
                                        "sha256": hashlib.sha256(content).hexdigest()}
        if number % 250 == 0:
            print(f"Moddateien geprüft: {number}/{len(source.records)}", flush=True)
    expected = dict(expected)
    before = core.inspect_archives(originals, expected)
    patched = {}
    for number, (package, (hed, pkg)) in enumerate(originals.items(), 1):
        print(f"[{number}/{len(originals)}] {package} bauen ...", flush=True)
        patched[package] = core.patch_archive(hed, pkg, work / "mod" / package,
                                             work / "patched" / package, expected[package], logs / (package + ".json"))
    print("Eingebaute Audiodateien und übrige Ressourcen prüfen ...", flush=True)
    after = core.verify_output(originals, patched, expected, before)
    destination = output / "atmosphere" / "contents" / program_id / "romfs"
    destination.mkdir(parents=True)
    for paths in patched.values():
        for path in paths:
            shutil.move(str(path), str(destination / path.name))
    shutil.rmtree(work)
    result = {"tool_version": "KH2-9.0", "source": source.summary(), "kh2_program_id": program_id,
              "audio_files_verified": after["matched_scd"], "movies_replaced": False,
              "original_files_modified": False, "hardware_tested": False,
              "game_version_user_supplied": version, "status": "Archive geprüft; Test im Spiel steht aus"}
    (output / "build-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def ask_path(title, folder):
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        value = filedialog.askdirectory(title=title) if folder else filedialog.askopenfilename(title=title)
        window.destroy()
        if not value:
            raise KeyboardInterrupt()
    except ImportError:
        value = input(title + " (Pfad): ").strip().strip('"')
    return Path(value).resolve()


def build_movies(root, movies, output, title_id=TITLE_ID, version="", ffmpeg=None, ffprobe=None, diagnose_only=False):
    return movie_import.build(root, movies, output, resolve_title_id(title_id), version,
                              ffmpeg=ffmpeg, ffprobe=ffprobe, diagnose_only=diagnose_only)


def merge_outputs(trees, destination):
    """Move verified stage outputs into one tree, rejecting Windows path collisions."""
    planned = {}
    for tree in trees:
        if not tree.is_dir():
            raise ValueError("Geprüfter Ausgabeordner fehlt: " + tree.name)
        for path in sorted(tree.rglob("*")):
            if path.is_symlink():
                raise ValueError("Verknüpfung in der erzeugten Ausgabe: " + path.name)
            if not path.is_file():
                continue
            relative = path.relative_to(tree)
            key = relative.as_posix().casefold()
            if key in planned:
                raise ValueError("Doppeltes Ausgabeziel für Stimmen und Filme: " + relative.as_posix())
            planned[key] = (path, relative)
    # Also reject file/directory conflicts before moving anything.
    for _, relative in planned.values():
        if any(parent.as_posix().casefold() in planned for parent in relative.parents if parent != Path(".")):
            raise ValueError("Datei- und Ordnerziele überschneiden sich: " + relative.as_posix())
    if destination.exists():
        raise ValueError("Der gemeinsame Arbeitsordner muss neu sein.")
    destination.mkdir(parents=True)
    for path, relative in planned.values():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
    return len(planned)


def save_combined_reports(output, work, report):
    """Keep one uploadable diagnosis when a stage fails; never include whole films/PKGs."""
    for stage, name in (("voices", "build-result.json"), ("movies", "movie-result.json")):
        source = work / stage / name
        if source.is_file():
            target = output / ("voice-result.json" if stage == "voices" else name)
            shutil.copyfile(source, target)
        logs = work / stage / "logs"
        if logs.is_dir():
            shutil.copytree(logs, output / "logs" / stage, dirs_exist_ok=True)
    (output / "build-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(output / "KH2-Gesamt-Diagnose.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("build-result.json", "voice-result.json", "movie-result.json"):
            path = output / name
            if path.is_file():
                archive.write(path, name)
        for stage, name in (("voices", "KH2-Switch-Diagnose.zip"), ("movies", "KH2-Film-Diagnose.zip")):
            path = work / stage / name
            if path.is_file():
                archive.write(path, name, compress_type=zipfile.ZIP_STORED)
        for path in sorted((output / "logs").rglob("*")):
            if path.is_file():
                with path.open("rb") as stream:
                    archive.writestr(path.relative_to(output).as_posix(), stream.read(128 * 1024))
        archive.writestr("LESEN.txt", "Gemeinsamer KH2-Bau: Stimmen und deutsche Filme.\n"
                          "Bei Fehlern diese KH2-Gesamt-Diagnose.zip im Chat hochladen.\n"
                          "Enthält kleine Stimmen-Diagnoseproben, falls erzeugt; keine vollständigen Filme oder PKGs.\n")


def build_all(root, source, movies, output, title_id=TITLE_ID, version="", ffmpeg=None, ffprobe=None):
    """Build voices and movies once each, exposing one complete Atmosphere directory."""
    root, output, movies = Path(root).resolve(), Path(output).resolve(), Path(movies).resolve()
    program_id = resolve_title_id(title_id)
    if output.exists():
        raise ValueError("Bitte einen neuen Ausgabeordner wählen.")
    for path in (root, source.path, movies):
        if path.is_dir() and output.is_relative_to(path):
            raise ValueError("Der Ausgabeordner muss außerhalb von ROMFS und Modquellen liegen.")
    output.mkdir(parents=True)
    work = output / "work"
    work.mkdir()
    report = {"tool_version": "KH2-9.0", "mode": "voices_and_movies", "kh2_program_id": program_id,
              "game_version_user_supplied": version, "original_files_modified": False,
              "hardware_tested": False, "status": "Prüfung läuft", "phase": "Voraussetzungen",
              "voices_completed": False, "movies_completed": False, "errors": []}
    try:
        tools = movie_import.MediaTools(ffmpeg, ffprobe)
        archives = find_archives(root)
        with movie_import.MovieSource(movies) as movie_source:
            report["movies_source"] = movie_source.summary()
            # Both stages share a disk. Reserve enough for the archived voices and movie work.
            affected = set(source.targets())
            needed = (sum(p.stat().st_size for package, (_, p) in archives.items() if package in affected)
                      + 2 * sum(r["size"] for r in source.records.values())
                      + 2 * sum(r["size"] for r in movie_source.records.values())
                      + 2 * sum(p.stat().st_size for p in movie_import.native_movies(root).values())
                      + 1024 * 1024 * 1024)
            report["free_space_required_bytes"] = needed
            if shutil.disk_usage(output).free < needed:
                raise ValueError(f"Zu wenig Platz für Stimmen und Filme zusammen: mindestens {needed / 1024**3:.1f} GiB.")
        print("\n[1/3] Deutsche Stimmen prüfen und bauen ...", flush=True)
        report["phase"] = "Stimmen"
        build(root, source, work / "voices", program_id, version, tools.ffmpeg)
        voice_report = json.loads((work / "voices/build-result.json").read_text(encoding="utf-8"))
        if voice_report.get("audio_files_verified", 0) <= 0:
            raise ValueError("Der Stimmen-Bau hat keine geprüften Audiodateien geliefert.")
        report["voices_completed"] = True
        report["audio_files_verified"] = voice_report["audio_files_verified"]
        report["audio_vorbis_decoder"] = voice_report.get("audio_vorbis_decoder")
        report["audio_process_crashes_recovered"] = voice_report.get("audio_process_crashes_recovered", 0)
        print("\n[2/3] Deutsche Filme prüfen und bauen ...", flush=True)
        report["phase"] = "Filme"
        build_movies(root, movies, work / "movies", program_id, version, tools.ffmpeg, tools.ffprobe)
        movie_report = json.loads((work / "movies/movie-result.json").read_text(encoding="utf-8"))
        if movie_report.get("movies_verified") != len(movie_import.FILMS):
            raise ValueError("Der Film-Bau hat nicht alle zwölf Filme geprüft.")
        report["movies_completed"] = True
        report["movies_verified"] = movie_report["movies_verified"]
        report["movies_converted"] = movie_report["movies_converted"]
        print("\n[3/3] Gemeinsamen atmosphere-Ordner bereitstellen ...", flush=True)
        report["phase"] = "Gemeinsame Ausgabe"
        ready = work / "ready/atmosphere"
        report["output_files"] = merge_outputs([work / "voices/atmosphere", work / "movies/atmosphere"], ready)
        report["movies_replaced"] = True
        report["status"] = "Stimmen und Filme geprüft; Test im Spiel steht aus"
        save_combined_reports(output, work, report)
        ready.rename(output / "atmosphere")
        return output / "atmosphere" / "contents" / program_id / "romfs"
    except BaseException as error:
        report["status"] = "Abgebrochen; kein gemeinsamer Mod ausgegeben"
        report["errors"].append(str(error) or type(error).__name__)
        save_combined_reports(output, work, report)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise ValueError("Gemeinsamer Bau gestoppt (" + report["phase"] + "): " + str(error)
                         + "\nBitte diese Diagnose hochladen: " + str(output / "KH2-Gesamt-Diagnose.zip")) from error
    finally:
        shutil.rmtree(work, ignore_errors=True)


def interactive():
    print("KH2 Final Mix – Deutsche Stimmen und Filme | v9 – libvorbis-Audiodekodierung\n")
    print("1 = Nur Diagnose mit Switch-ROMFS und Nexus-Patch erstellen")
    print("2 = Stimmen-Mod prüfen und bauen")
    print("3 = Deutsche Filme prüfen und bauen")
    print("4 = Nur Film-Diagnose erstellen")
    print("5 = Stimmen UND Filme zusammen patchen (Standard)\n")
    choice = input("Auswahl [5]: ").strip() or "5"
    if choice not in ("1", "2", "3", "4", "5"):
        raise ValueError("Bitte 1, 2, 3, 4 oder 5 wählen.")
    root = ask_path("KH2-ROMFS / Program 3 auswählen", True)
    if choice in ("3", "4"):
        mod = ask_path("Nexus-Datei German movies (STEAM): ZIP auswählen (EPIC ebenfalls möglich)", False)
    else:
        mod = ask_path("Nexus-Hauptdatei German voices 2.1 mit Steam-Unterstützung: ZIP oder .kh2pcpatch", False)
    movies = ask_path("Nexus-Datei German movies (STEAM): ZIP auswählen (EPIC ebenfalls möglich)", False) if choice == "5" else None
    title_id = input(f"KH2-Program-ID [{TITLE_ID}]: ").strip() or TITLE_ID
    version = input("Spielversion (optional): ").strip()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if choice in ("3", "4"):
        parent = ask_path("Ausgabeordner mit ausreichend freiem Platz", True)
        target = build_movies(root, mod, parent / ("KH2-Switch-Filme-" + stamp), title_id,
                              version, diagnose_only=(choice == "4"))
        print("\nErgebnis:", target)
        if choice == "3":
            print("Den erzeugten atmosphere-Ordner auf die SD-Karte kopieren und mit dem Stimmen-Mod zusammenführen.")
            print("Ein Test im Spiel steht aus.")
        else:
            print("Bitte die KH2-Film-Diagnose.zip zur Prüfung im Chat hochladen.")
        return
    with PatchSource(mod) as source:
        if choice == "1":
            target = HERE / ("KH2-Switch-Diagnose-" + stamp + ".zip")
            report = diagnose(root, source, target, title_id, version)
            print("\nDiagnose erstellt:", target)
            print("Passende Audionamen:", report["inspection"]["matched_scd"], "/", report["inspection"]["expected_scd"])
            print("Bitte diese Diagnose-ZIP zur konkreten Anpassung im Chat hochladen.")
        elif choice == "5":
            parent = ask_path("Ausgabeordner für Stimmen UND Filme", True)
            target = build_all(root, source, movies, parent / ("KH2-Switch-Komplett-" + stamp), title_id, version)
            print("\nStimmen und Filme zusammen gebaut:", target)
            print("Den einen erzeugten atmosphere-Ordner auf die SD-Karte kopieren. Ein Test im Spiel steht aus.")
        else:
            parent = ask_path("Ausgabeordner mit ausreichend freiem Platz", True)
            target = build(root, source, parent / ("KH2-Switch-Build-" + stamp), title_id, version)
            print("\nAudiomod gebaut:", target)
            print("Den erzeugten atmosphere-Ordner auf die SD-Karte kopieren. Ein Test im Spiel steht aus.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--diagnose", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--build-movies", action="store_true")
    mode.add_argument("--diagnose-movies", action="store_true")
    mode.add_argument("--build-all", action="store_true", help="Stimmen und Filme in einen gemeinsamen Mod bauen")
    parser.add_argument("--romfs", type=Path)
    parser.add_argument("--mod", type=Path)
    parser.add_argument("--movies", type=Path, help="Lokale Steam-/Epic-Film-ZIP oder entpackter Ordner")
    parser.add_argument("--ffmpeg", help="Optionaler Pfad zu ffmpeg.exe")
    parser.add_argument("--ffprobe", help="Optionaler Pfad zu ffprobe.exe")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--title-id", default=TITLE_ID)
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    try:
        if not (args.diagnose or args.build or args.build_movies or args.diagnose_movies or args.build_all):
            interactive()
        elif args.build_all:
            if not args.romfs or not args.mod or not args.movies or not args.output:
                parser.error("--romfs, --mod, --movies und --output sind für den gemeinsamen Bau erforderlich.")
            with PatchSource(args.mod) as source:
                print(build_all(args.romfs, source, args.movies, args.output, args.title_id, args.version,
                                args.ffmpeg, args.ffprobe))
        elif args.build_movies or args.diagnose_movies:
            if not args.romfs or not args.movies or not args.output:
                parser.error("--romfs, --movies und --output sind für Filme erforderlich.")
            print(build_movies(args.romfs, args.movies, args.output, args.title_id, args.version,
                               args.ffmpeg, args.ffprobe, args.diagnose_movies))
        else:
            if not args.romfs or not args.mod or not args.output:
                parser.error("--romfs, --mod und --output sind erforderlich.")
            with PatchSource(args.mod) as source:
                if args.diagnose:
                    diagnose(args.romfs.resolve(), source, args.output.resolve(), args.title_id, args.version)
                    print(args.output.resolve())
                else:
                    print(build(args.romfs.resolve(), source, args.output.resolve(), args.title_id, args.version, args.ffmpeg))
        return 0
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except Exception as error:
        log = HERE / ("Fehler-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".log")
        try:
            log.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        print("\nFEHLER:", error)
        print("Fehlerprotokoll:", log)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
