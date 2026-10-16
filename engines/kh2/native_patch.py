"""Native KH2 bindings and original-SCD/remastered-resource build workflow."""
from contextlib import ExitStack
import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import zipfile
import zlib

import archive_core as core
from egs_reader import read_audio
from scd_tools import inspect_scd
from scd_audio import AudioConverter

MIB = 1024 ** 2
ASSET_ALIAS = {"obj/B_EX930.a.us": "obj/B_EX390.a.us"}
# Confirmed by the supplied native HD tables. Apply only when the source
# resource is absent and the exact replacement resource exists in the target.
HD_RESOURCE_ALIASES = {
    ("obj/N_CM000_BTL.a.us", "voice/battle/us/minnie"):
        ("obj/N_DC010_BTL.a.us", "voice/battle/us/minnie"),
    ("obj/N_EX650_BTL1.a.us", "voice/battle/us/tt_walla1"):
        ("obj/N_EX650_BTL1.a.us", "voice/battle/jp/tt_walla1"),
    ("obj/N_EX680_BTL10.a.us", "voice/battle/us/tt_walla2"):
        ("obj/N_EX680_BTL10.a.us", "voice/battle/jp/tt_walla2"),
    **{("obj/N_HB040_BTL.a." + language, "voice/battle/us/stitch"):
       ("obj/N_HB040_BTL.a." + language, "voice/battle/us/stusch")
       for language in ("fr", "gr", "it", "sp", "us")},
}


def relevant(source):
    return any(("/original/" in name and name.lower().endswith(".scd")) or
               ("/remastered/" in name and (name.lower().endswith(".png") or "/voice/" in name))
               for name in source.records)


def make_plan(archives, source):
    indices = {package: core.read_hed(hed, pkg.stat().st_size) for package, (hed, pkg) in archives.items()}
    headers, bindings, skipped, errors = {}, [], [], []
    report = {"native_workflow": True, "errors": errors, "skipped_pc_images": skipped,
              "bindings": bindings, "source_variants": []}
    candidates = collections.defaultdict(list)
    with ExitStack() as stack:
        streams = {package: stack.enter_context(pkg.open("rb")) for package, (_, pkg) in archives.items()}

        def locations(asset):
            digest = core.md5_name(asset)
            return [p for p, index in indices.items() if digest in index and index[digest]["length"]]

        def header(package, asset):
            key = package, asset
            if key not in headers:
                entry = indices[package][core.md5_name(asset)]
                headers[key] = dict(core.read_header(streams[package], entry), entry=entry)
            return headers[key]

        for canonical, record in sorted(source.records.items()):
            source_package, category, tail = canonical.split("/", 2)
            try:
                added, alias, resource_alias = False, None, None
                if category == "original" and tail.lower().endswith(".scd"):
                    asset, name, kind = tail, "", "original"
                    hits = locations(asset)
                    if not hits:
                        if source_package not in archives:
                            raise ValueError("Zielarchiv für neues Original fehlt")
                        package, added = source_package, True
                    elif source_package in hits:
                        package = source_package
                    elif len(hits) == 1:
                        package = hits[0]
                    else:
                        raise ValueError("Mehrdeutige native Original-Zuordnung")
                    if not added:
                        h = header(package, asset)
                        if h["headers"]:
                            raise ValueError("Original-SCD enthält weitere HD-Referenzen")
                elif category == "remastered":
                    parts, matches = tail.split("/"), []
                    for split in range(1, len(parts)):
                        asset = "/".join(parts[:split])
                        hits = locations(asset)
                        if hits:
                            matches.append((asset, "/".join(parts[split:]), hits))
                    if not matches:
                        for old, new in ASSET_ALIAS.items():
                            if tail.startswith(old + "/") and locations(new):
                                matches.append((new, tail[len(old) + 1:], locations(new)))
                                alias = old
                    if not matches:
                        raise ValueError("Kein natives Original-Asset für die HD-Ressource")
                    asset, name, hits = matches[-1]
                    if source_package in hits:
                        package = source_package
                    elif len(hits) == 1:
                        package = hits[0]
                    else:
                        raise ValueError("Mehrdeutige native HD-Zuordnung")
                    present = {row["name"] for row in header(package, asset)["headers"]}
                    if name not in present and (asset, name) in HD_RESOURCE_ALIASES:
                        replacement_asset, replacement_name = HD_RESOURCE_ALIASES[asset, name]
                        replacement_hits = locations(replacement_asset)
                        replacement_package = (source_package if source_package in replacement_hits else
                                               replacement_hits[0] if len(replacement_hits) == 1 else None)
                        if (replacement_package is not None and any(
                                row["name"] == replacement_name for row in
                                header(replacement_package, replacement_asset)["headers"])):
                            resource_alias = {"from": asset + "/" + name,
                                              "to": replacement_asset + "/" + replacement_name}
                            package, asset, name = replacement_package, replacement_asset, replacement_name
                            present = {row["name"] for row in header(package, asset)["headers"]}
                    if name not in present:
                        # The native title has slots 0..8; PC title images 9..11 have no native reference.
                        pc_image = re.fullmatch(r"(?:FR|GR|IT|SP|US)_title_2ld(9|10|11)\.png", name)
                        native_numbers = [int(m.group(1)) for n in present
                                          if (m := re.search(r"_title_2ld(\d+)\.png$", n))]
                        if (asset.startswith("menu/") and asset.endswith("/title.2ld") and pc_image
                                and native_numbers and max(native_numbers) == 8):
                            skipped.append({"path": canonical, "reason": "PC-Titelbild ohne nativen Slot; Switch verwendet 0..8"})
                            continue
                        voices = sorted(n for n in present if n.startswith("voice/"))
                        raise ValueError("Native HD-Ressource fehlt: " + name +
                                         ("; vorhandene Stimmen: " + ", ".join(voices[:8]) if voices else ""))
                    kind = "image" if name.lower().endswith(".png") else "audio"
                    if kind == "audio" and not (name.lower().endswith(".scd") or name.startswith("voice/")):
                        raise ValueError("Unbekannte HD-Dateiart")
                else:
                    raise ValueError("Nicht unterstützte Patchdateiart")
                binding = {"source": canonical, "source_package": source_package, "package": package,
                           "asset": asset, "name": name, "kind": kind, "added": added,
                           "relocated": source_package != package}
                if alias:
                    binding["corrected_asset"] = {"from": alias, "to": asset}
                if resource_alias:
                    binding["corrected_resource"] = resource_alias
                bindings.append(binding)
                candidates[(package, asset, name)].append(binding)
            except (ValueError, OSError, struct.error, UnicodeError) as error:
                errors.append({"path": canonical, "error": str(error)})
        for name in source.outside_payload:
            if PurePosixPath(name.replace("\\", "/")).suffix.lower() in (".lua", ".dll", ".exe", ".ips", ".pchtxt"):
                errors.append({"path": name, "error": "Erforderlicher PC-Code wird nicht auf Switch ausgeführt"})
    targets = collections.defaultdict(dict)
    for (package, asset, name), choices in candidates.items():
        choices.sort(key=lambda row: (row["source_package"] != package,
                                      bool(row.get("corrected_resource") or row.get("corrected_asset")),
                                      row["source"]))
        selected = choices[0]
        for other in choices[1:]:
            exact = selected["source_package"] == package
            a, b = source.records[selected["source"]], source.records[other["source"]]
            same = a["size"] == b["size"] and a["zip_crc32"] is not None and a["zip_crc32"] == b["zip_crc32"]
            alias_merge = bool(selected.get("corrected_resource") or other.get("corrected_resource"))
            if (not exact or alias_merge) and not same and source.read(selected["source"]) != source.read(other["source"]):
                errors.append({"path": other["source"], "error": "Widersprüchliche verlagerte Patchvarianten"})
            report["source_variants"].append({"selected": selected["source"], "other": other["source"],
                                               "reason": "Bestätigte doppelte HD-Quelle" if alias_merge else
                                               "Passende native Archivvariante" if exact and not same else "Doppelte Quelldatei"})
        item = targets[package].setdefault(asset, {"original": None, "hd": {}, "added": selected["added"]})
        if selected["kind"] == "original":
            item["original"] = selected
        else:
            item["hd"][name] = selected
    selected = [b for assets in targets.values() for item in assets.values()
                for b in ([item["original"]] if item["original"] else []) + list(item["hd"].values())]
    report.update(source_files=len(source.records), selected_files=len(selected),
                  original_scd=sum(b["kind"] == "original" for b in selected),
                  original_scd_added=sum(b["kind"] == "original" and b["added"] for b in selected),
                  remastered_audio=sum(b["kind"] == "audio" for b in selected),
                  png_images=sum(b["kind"] == "image" for b in selected),
                  relocated_source_files=sum(b["relocated"] for b in bindings),
                  corrected_hd_resources=sum(bool(b.get("corrected_resource")) for b in bindings),
                  can_build_supported_patch=bool(selected) and not errors,
                  compatible_names=not errors,
                  expected_scd=sum(b["kind"] != "image" for b in selected),
                  matched_scd=sum(b["kind"] != "image" and not b["added"] for b in selected),
                  can_build_audio_only=False, source_extra_files=[e["path"] for e in errors])
    return {"archives": archives, "indices": indices, "headers": headers, "targets": dict(targets), "report": report}


def write_diagnosis(root, source, target, program_id, version, plan, failure=None):
    report = {"tool_version": "KH2-9.0", "hardware_tested": False, "kh2_program_id": program_id,
              "game_version_user_supplied": version, "patch": source.summary(), "inspection": plan["report"],
              "inventory": [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size}
                            for p in sorted(root.rglob("*")) if p.is_file()],
              "native_headers": {package + "/" + asset: header
                                 for (package, asset), header in plan["headers"].items()}, "samples": []}
    if failure:
        report["build_failure"] = failure
    target.parent.mkdir(parents=True, exist_ok=True)
    budget = 32 * MIB
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output, ExitStack() as stack:
        streams = {p: stack.enter_context(pkg.open("rb")) for p, (_, pkg) in plan["archives"].items()}
        def add(name, content):
            nonlocal budget
            if len(content) > min(8 * MIB, budget):
                return False
            output.writestr(name, content)
            budget -= len(content)
            return True
        for package, (hed, _) in plan["archives"].items():
            output.write(hed, "hed/" + hed.name)
            items = list(plan["targets"].get(package, {}).items())
            originals = [(asset, item) for asset, item in items if item["original"] and not item["added"]][:2]
            hd = [(asset, item) for asset, item in items if item["hd"]]
            hd.sort(key=lambda pair: (not any(b.get("corrected_asset") for b in pair[1]["hd"].values()),
                                      not pair[0].startswith("obj/"), pair[0]))
            focused = []
            if failure:
                focus_bindings = [f["binding"] for f in failure.get("validation_errors", [])]
                if failure.get("binding"):
                    focus_bindings.insert(0, failure["binding"])
                focus_assets = list(dict.fromkeys(b["asset"] for b in focus_bindings
                                                 if b.get("package") == package))[:3]
                focused = [(asset, item) for asset, item in items if asset in focus_assets]
            chosen_assets = dict(focused + originals + hd[:3])
            for asset, item in chosen_assets.items():
                h = plan["headers"].get((package, asset))
                if not h:
                    binding = item["original"]
                    if binding and source.records[binding["source"]]["size"] <= 8 * MIB:
                        name = "pc/" + binding["source"]
                        if add(name, source.read(binding["source"])):
                            report["samples"].append({"package": package, "asset": asset, "files": [name], "new_original": True})
                    continue
                sample = {"package": package, "asset": asset, "header": h, "files": []}
                prefix = "native/" + package + "/" + core.md5_name(asset).hex()
                if h["entry"]["length"] <= 2 * MIB:
                    stream = streams[package]
                    stream.seek(h["entry"]["offset"])
                    if add(prefix + ".raw", stream.read(h["entry"]["length"])):
                        sample["files"].append(prefix + ".raw")
                elif h["original_size"] <= 8 * MIB and h["original_stored_size"] <= 8 * MIB:
                    if add(prefix + ".original", core.read_original(streams[package], h)):
                        sample["files"].append(prefix + ".original")
                chosen = ([item["original"]] if item["original"] else []) + list(item["hd"].values())[:2]
                for b in chosen:
                    if source.records[b["source"]]["size"] <= (8 if b["kind"] == "original" else 2) * MIB:
                        name = "pc/" + b["source"]
                        if add(name, source.read(b["source"])):
                            sample["files"].append(name)
                    if b["name"]:
                        row = next(r for r in h["headers"] if r["name"] == b["name"])
                        if row["size"] <= 2 * MIB:
                            name = prefix + "-hd-" + str(h["headers"].index(row)) + ".bin"
                            if add(name, read_audio(streams[package], row, bytes.fromhex(h["seed"]))):
                                sample["files"].append(name)
                report["samples"].append(sample)
        for error in plan["report"]["errors"][:4]:
            name = error["path"]
            if name in source.records and source.records[name]["size"] <= 2 * MIB:
                add("unmapped-pc/" + name, source.read(name))
        report["sample_bytes"] = 32 * MIB - budget
        output.writestr("diagnose.json", json.dumps(report, ensure_ascii=False, indent=2))
        output.writestr("LESEN.txt", "KH2 v9: native Archivzuordnung, Original-SCDs und HD-Ressourcen.\n"
                          "Eingaben bleiben erhalten. Diese ZIP bei einem Fehler im Chat hochladen.\n")
    return report


def diagnose(root, source, target, program_id, version, archives):
    if target.exists():
        raise ValueError("Die Diagnose-ZIP existiert bereits.")
    plan = make_plan(archives, source)
    return write_diagnosis(root, source, target, program_id, version, plan)


def check_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Keine PNG-Datei")
    cursor, seen_idat = 8, False
    while cursor + 12 <= len(data):
        size = struct.unpack_from(">I", data, cursor)[0]
        end = cursor + 12 + size
        if end > len(data):
            break
        kind = data[cursor + 4:cursor + 8]
        if zlib.crc32(data[cursor + 4:end - 4]) != struct.unpack_from(">I", data, end - 4)[0]:
            raise ValueError("PNG-Prüfsumme stimmt nicht")
        if cursor == 8:
            if kind != b"IHDR" or size != 13:
                raise ValueError("PNG ohne IHDR")
            width, height = struct.unpack_from(">II", data, cursor + 8)
            if not 1 <= width <= 16384 or not 1 <= height <= 16384:
                raise ValueError("Ungültige PNG-Größe")
        seen_idat |= kind == b"IDAT"
        if kind == b"IEND" and seen_idat:
            return
        cursor = end
    raise ValueError("PNG ist abgeschnitten")


def build(root, source, output, program_id, version, archives, ffmpeg=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Bitte einen neuen Ausgabeordner wählen.")
    if output.is_relative_to(root) or (source.path.is_dir() and output.is_relative_to(source.path)):
        raise ValueError("Der Ausgabeordner muss außerhalb der Eingaben liegen.")
    output.mkdir(parents=True)
    plan = make_plan(archives, source)
    diagnostic = output / "KH2-Switch-Diagnose.zip"
    write_diagnosis(root, source, diagnostic, program_id, version, plan)
    if not plan["report"]["can_build_supported_patch"]:
        errors = plan["report"]["errors"]
        details = "\n".join(row["path"] + ": " + row["error"] for row in errors[:8])
        raise ValueError(f"Native Zuordnung fehlgeschlagen ({len(errors)} Fehler).\n" + details +
                         "\nBitte Diagnose hochladen: " + str(diagnostic))
    selected = [(package, asset, item, b) for package, assets in plan["targets"].items()
                for asset, item in assets.items()
                for b in ([item["original"]] if item["original"] else []) + list(item["hd"].values())]
    needed = (sum(archives[p][1].stat().st_size for p in plan["targets"])
              + 2 * sum(source.records[b["source"]]["size"] for _, _, _, b in selected) + 512 * MIB)
    if shutil.disk_usage(output).free < needed:
        raise ValueError(f"Zu wenig Platz: mindestens {needed / 1024**3:.1f} GiB nötig.")
    work = output / "work"
    work.mkdir()
    logs = output / "logs"
    logs.mkdir()
    expected = collections.defaultdict(dict)
    original_changes = collections.defaultdict(dict)
    validations, current, failures, checked_sources = [], None, [], 0
    converter = AudioConverter(ffmpeg, logs)
    try:
        with ExitStack() as stack:
            streams = {p: stack.enter_context(archives[p][1].open("rb")) for p in plan["targets"]}
            for number, (package, asset, item, b) in enumerate(selected, 1):
                current = b
                converter.context = {"source": b["source"]}
                data = source.read(b["source"])
                checked_sources += 1
                h = plan["headers"].get((package, asset))
                try:
                    if b["kind"] == "original":
                        if h:
                            data, check = converter.prepare(core.read_original(streams[package], h), data)
                        else:
                            check = inspect_scd(data)
                            check = {k: v for k, v in check.items() if k != "streams"}
                        validations.append(dict(source=b["source"], **check))
                    elif b["kind"] == "audio":
                        row = next(r for r in h["headers"] if r["name"] == b["name"])
                        data, check = converter.prepare(read_audio(streams[package], row, bytes.fromhex(h["seed"])), data)
                        validations.append(dict(source=b["source"], **check))
                    else:
                        check_png(data)
                except ValueError as error:
                    failures.append({"error": str(error), "binding": b})
                    if number % 250 == 0 or number == len(selected):
                        print(f"Vorprüfung: {number}/{len(selected)}, Fehler: {len(failures)}", flush=True)
                    continue
                sha = hashlib.sha256(data).hexdigest()
                blob = work / "blobs" / sha
                if not blob.exists():
                    blob.parent.mkdir(exist_ok=True)
                    blob.write_bytes(data)
                record = {"file": str(blob), "size": len(data), "sha256": sha, "source": b["source"]}
                expected[package].setdefault(asset, {})
                if b["kind"] == "original":
                    original_changes[package][asset] = record
                else:
                    stage = work / "mod" / package / "remastered" / asset / b["name"]
                    stage.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        stage.hardlink_to(blob)
                    except OSError:
                        shutil.copyfile(blob, stage)
                    expected[package][asset][b["name"]] = record
                if number % 250 == 0 or number == len(selected):
                    print(f"Native Patchdateien geprüft: {number}/{len(selected)}", flush=True)
        if failures:
            current = failures[0]["binding"]
            details = "\n".join(f["binding"]["source"] + ": " + f["error"] for f in failures[:8])
            raise ValueError(f"Vorprüfung: {len(failures)} von {checked_sources} Patchdateien benötigen eine Anpassung.\n" + details)
        originals = {p: archives[p] for p in plan["targets"]}
        before_expected = {p: {a: hd for a, hd in assets.items() if not plan["targets"][p][a]["added"]}
                           for p, assets in expected.items()}
        before = core.inspect_archives(originals, before_expected)
        patched = {}
        for package, (hed, pkg) in originals.items():
            print("Native Stimmen und Ressourcen bauen: " + package, flush=True)
            patched[package] = core.patch_archive(hed, pkg, work / "mod" / package, work / "patched" / package,
                                                 expected[package], logs / (package + ".json"), original_changes[package])
        print("Alle eingebauten Dateien und unveränderten Ressourcen prüfen ...", flush=True)
        core.verify_output(originals, patched, expected, before, original_changes)
        result = {"tool_version": "KH2-9.0", "kh2_program_id": program_id, "source": source.summary(),
                  "audio_files_verified": plan["report"]["expected_scd"],
                  "original_scd_verified": plan["report"]["original_scd"],
                  "new_original_scd_added": plan["report"]["original_scd_added"],
                  "remastered_audio_verified": plan["report"]["remastered_audio"],
                  "png_files_verified": plan["report"]["png_images"],
                  "binding_report": plan["report"], "scd_checks": validations,
                  "audio_ffmpeg_version": converter.version,
                  "audio_vorbis_decoder": "libvorbis" if converter.vorbis_decoder_checked else None,
                  "audio_ffmpeg_errors": converter.errors,
                  "audio_process_crashes_recovered": sum(e["recovered"] for e in converter.errors),
                  "movies_replaced": False, "original_files_modified": False, "hardware_tested": False,
                  "game_version_user_supplied": version, "status": "Original-SCDs und HD-Ressourcen geprüft; Test im Spiel steht aus"}
        (output / "build-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        ready = work / "ready/atmosphere/contents" / program_id / "romfs"
        ready.mkdir(parents=True)
        for paths in patched.values():
            for path in paths:
                path.rename(ready / path.name)
        (work / "ready/atmosphere").rename(output / "atmosphere")
        return output / "atmosphere/contents" / program_id / "romfs"
    except BaseException as error:
        message = str(error)
        if current and current["source"] not in message:
            message = current["source"] + ": " + message
        (output / "build-result.json").write_text(json.dumps({"tool_version": "KH2-9.0", "status": "Abgebrochen",
            "audio_files_verified": 0, "hardware_tested": False, "error": message, "binding": current,
            "source_files_checked": checked_sources, "validation_errors": failures, "scd_checks": validations,
            "audio_ffmpeg_errors": converter.errors, "audio_ffmpeg_version": converter.version,
            "audio_vorbis_decoder": "libvorbis" if converter.vorbis_decoder_checked else None},
            ensure_ascii=False, indent=2), encoding="utf-8")
        write_diagnosis(root, source, diagnostic, program_id, version, plan,
                        {"error": message, "binding": current, "source_files_checked": checked_sources,
                         "validation_errors": failures, "audio_ffmpeg_errors": converter.errors})
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise ValueError(message + "\nBitte Diagnose hochladen: " + str(diagnostic)) from error
    finally:
        shutil.rmtree(work, ignore_errors=True)
