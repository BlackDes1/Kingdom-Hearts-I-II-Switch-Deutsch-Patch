"""Local KH2 movie import. Media is supplied by the user, never downloaded here."""
from __future__ import annotations

from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import stat
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
FILMS = frozenset(("ena", "lop", "me2", "me3", "me6", "opn", "sec", "hcb",
                   "mea", "me4", "hca", "sec2"))
MOVIE_PATH = re.compile(r"(?:^|/)KH2ReSource/zmovie/en/([^/]+)\.mp4$", re.I)
SOURCE_URL = "https://www.nexusmods.com/kingdomhearts2finalmix/mods/42?tab=files"
DOWNLOADS = {"STEAM": {"name": "German movies (STEAM)", "nexus_file_id": 816},
             "EPIC": {"name": "German movies (EPIC)", "nexus_file_id": 542}}
MIB = 1024 ** 2


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            result.update(chunk)
    return result.hexdigest()


def film_name(name):
    name = name.replace("\\", "/")
    parts = name.split("/")
    if name.startswith("/") or any(p in ("..", ".") or ":" in p or "\0" in p for p in parts):
        raise ValueError("Ungültiger Pfad im Filmpaket: " + name)
    match = MOVIE_PATH.search(name)
    return match.group(1).lower() if match else None


def film_platform(name, folder=None):
    """Infer the payload platform from folders, including an extracted STEAM root."""
    parts = (Path(folder).parts if folder is not None else ()) + tuple(name.replace("\\", "/").split("/"))
    # The last four parts are KH2ReSource/zmovie/en/<film>.mp4.
    return next((p.upper() for p in reversed(parts[:-4]) if p.upper() in DOWNLOADS), None)


class MovieSource:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.archive = None
        self.records = {}
        self.platform = None

    def __enter__(self):
        try:
            if self.path.is_dir():
                entries = []
                for path in self.path.rglob("*"):
                    if path.is_symlink():
                        raise ValueError("Verknüpfung im Filmordner: " + path.name)
                    if path.is_file():
                        entries.append((path.relative_to(self.path).as_posix(), path))
            elif zipfile.is_zipfile(self.path):
                self.archive = zipfile.ZipFile(self.path)
                entries = [(i.filename, i) for i in self.archive.infolist() if not i.is_dir()]
            else:
                raise ValueError("Bitte die ZIP 'German movies (STEAM)' oder ihren entpackten Ordner wählen. EPIC wird ebenfalls unterstützt.")
            for name, handle in entries:
                film = film_name(name)
                if not film:
                    continue
                if film not in FILMS:
                    raise ValueError("Unbekannter KH2-Film im Paket: " + name)
                if film in self.records:
                    raise ValueError("Doppelter Film im Paket: " + film)
                if isinstance(handle, Path):
                    size = handle.stat().st_size
                else:
                    if stat.S_ISLNK(handle.external_attr >> 16) or handle.flag_bits & 1:
                        raise ValueError("Verknüpfte oder verschlüsselte ZIP-Datei: " + name)
                    size = handle.file_size
                platform = film_platform(name, self.path if not self.archive else None)
                self.records[film] = {"path": name, "size": size, "handle": handle, "platform": platform}
            if not self.records:
                raise ValueError("Keine KH2-Filme unter KH2ReSource/zmovie/en gefunden. "
                                 "Bitte 'German movies (STEAM)' wählen und die Ordnerstruktur beibehalten.")
            platforms = {r["platform"] for r in self.records.values() if r["platform"]}
            if len(platforms) > 1:
                raise ValueError("Steam- und Epic-Filme sind im Quellpaket gemischt. Bitte genau ein vollständiges Filmpaket wählen.")
            self.platform = next(iter(platforms), None)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.archive:
            self.archive.close()

    @contextmanager
    def open(self, film):
        handle = self.records[film]["handle"]
        stream = self.archive.open(handle) if self.archive else handle.open("rb")
        with stream:
            yield stream

    def summary(self):
        download = DOWNLOADS.get(self.platform, {})
        return {"selected_name": self.path.name, "mod_page": SOURCE_URL,
                "recommended_download": "German movies (STEAM)", "source_platform": self.platform,
                "detected_download": download.get("name"), "nexus_file_id": download.get("nexus_file_id"),
                "download_inferred_from_paths": True,
                "files": [{k: v for k, v in value.items() if k != "handle"}
                          for _, value in sorted(self.records.items())],
                "missing_films": sorted(FILMS - self.records.keys())}


def native_movies(root):
    if not root.is_dir() or root.name.lower() in ("switch", "kh2resource", "zmovie", "en"):
        raise ValueError("Bitte den ROMFS-Hauptordner von KH2 / Program 3 wählen; darin liegt der Ordner Switch.")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Verknüpfungen im ROMFS bitte vor dem Import auflösen.")
        if not path.is_file() or path.suffix.lower() != ".mp4":
            continue
        relative = path.relative_to(root).as_posix()
        film = film_name(relative)
        if film in FILMS:
            if film in result:
                raise ValueError("Mehrere Switch-Ziele für " + film + ".mp4 gefunden; nur einen ROMFS auswählen.")
            result[film] = path
    return result


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def rate(value):
    try:
        result = Fraction(str(value))
        if result > 0:
            return result
    except (ValueError, ZeroDivisionError):
        pass
    raise ValueError("Keine gültige Bildrate im Film.")


class MediaTools:
    def __init__(self, ffmpeg=None, ffprobe=None):
        self.ffmpeg = self.find("ffmpeg", ffmpeg)
        sibling = Path(self.ffmpeg).parent / ("ffprobe.exe" if self.ffmpeg.lower().endswith(".exe") else "ffprobe")
        self.ffprobe = self.find("ffprobe", ffprobe or (sibling if sibling.is_file() else None))

    @staticmethod
    def find(name, selected):
        if selected:
            candidate = shutil.which(str(selected)) or str(Path(selected).resolve())
            if Path(candidate).is_file():
                return candidate
        else:
            for folder in (HERE, HERE / "bin", HERE / "ffmpeg" / "bin"):
                for suffix in (".exe", ""):
                    candidate = folder / (name + suffix)
                    if candidate.is_file():
                        return str(candidate)
            found = shutil.which(name)
            if found:
                return found
        raise ValueError(name + " fehlt. FFmpeg mit ffprobe installieren oder beide Programme in den Ordner bin legen. "
                         "Download und Anleitung: https://ffmpeg.org/download.html (siehe README-DE.md).")

    def probe(self, path):
        command = [self.ffprobe, "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
                   "-show_streams", "-show_format", "-of", "json", str(path.resolve())]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
        if result.returncode:
            raise ValueError("MP4 kann nicht gelesen werden: " + path.name + ": " + result.stderr[-1500:])
        raw = json.loads(result.stdout)
        streams = raw.get("streams", [])
        video = [s for s in streams if s.get("codec_type") == "video"]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if len(video) != 1 or len(audio) != 1 or len(streams) != 2:
            raise ValueError("Erwartet werden genau eine Bild- und eine Tonspur: " + path.name)
        keys = ("codec_name", "codec_tag_string", "profile", "level", "width", "height", "pix_fmt",
                "avg_frame_rate", "r_frame_rate", "sample_aspect_ratio", "sample_rate", "channels",
                "channel_layout", "start_time", "duration", "bit_rate", "field_order", "color_range",
                "color_space", "color_transfer", "color_primaries", "has_b_frames")
        for track in (video[0], audio[0]):
            if any(abs(number(s.get("rotation")) or 0) > 0 for s in track.get("side_data_list", [])):
                raise ValueError("Rotierte Filmspuren benötigen eine gesonderte Anpassung: " + path.name)
        media = {"video": {k: video[0].get(k) for k in keys if k in video[0]},
                 "audio": {k: audio[0].get(k) for k in keys if k in audio[0]},
                 "duration": raw.get("format", {}).get("duration"),
                 "major_brand": raw.get("format", {}).get("tags", {}).get("major_brand")}
        for kind in ("video", "audio"):
            if not number(media[kind].get("duration")) or number(media[kind]["duration"]) <= 0:
                raise ValueError("Keine verlässliche Spurdauer: " + path.name)
        rate(media["video"].get("avg_frame_rate"))
        return media

    def run(self, arguments, log):
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen([self.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror"]
                                       + arguments, stdout=subprocess.DEVNULL, stderr=stream)
            try:
                while True:
                    try:
                        code = process.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        print("  FFmpeg arbeitet weiter: " + log.stem, flush=True)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        if code:
            raise ValueError("FFmpeg fehlgeschlagen; siehe " + log.name + ": "
                             + log.read_text(encoding="utf-8", errors="replace")[-1500:])


def profile(value):
    return {"Constrained Baseline": "baseline", "Baseline": "baseline", "Main": "main", "High": "high"}.get(value)


def effective_rate(video):
    average = rate(video["avg_frame_rate"])
    try:
        nominal = rate(video.get("r_frame_rate"))
    except ValueError:
        return average
    duration = number(video.get("duration"))
    # These KH2 MP4s often have a shortened last frame/edit list. Do not re-encode
    # a whole film because its reported average differs by up to two frames over its runtime.
    if duration and abs(float(average - nominal)) * duration <= 2.1:
        return nominal
    return average


def video_changes(native, source):
    fields = ("codec_name", "codec_tag_string", "width", "height", "pix_fmt", "sample_aspect_ratio")
    changes = [key for key in fields if native.get(key) != source.get(key)]
    if native.get("codec_name") == "h264":
        if profile(native.get("profile")) != profile(source.get("profile")):
            changes.append("profile")
    elif native.get("profile") != source.get("profile"):
        changes.append("profile")
    if native.get("level") != source.get("level"):
        changes.append("level")
    if abs(float(effective_rate(native) - effective_rate(source))) > 0.00001:
        changes.append("frame_rate")
    if native.get("has_b_frames") == 0 and source.get("has_b_frames", 0) > 0:
        changes.append("has_b_frames")
    nbit, sbit = number(native.get("bit_rate")), number(source.get("bit_rate"))
    if nbit and sbit and sbit > nbit * 1.3:
        changes.append("bit_rate")
    return changes


def audio_changes(native, source):
    return [key for key in ("codec_name", "codec_tag_string", "profile", "sample_rate", "channels", "channel_layout")
            if native.get(key) != source.get(key)]


def plan_conversion(native, source):
    nv, sv, na, sa = native["video"], source["video"], native["audio"], source["audio"]
    tolerance = max(0.25, 2 / float(rate(nv["avg_frame_rate"])))
    for kind in ("video", "audio"):
        if abs(float(native[kind]["duration"]) - float(source[kind]["duration"])) > tolerance:
            raise ValueError("Abweichende " + kind + "-Dauer; automatische Synchronisation wäre nicht verlässlich.")
    for media in (native, source):
        vstart = number(media["video"].get("start_time"))
        astart = number(media["audio"].get("start_time"))
        if vstart is None or astart is None or abs(vstart - astart) > 0.1:
            raise ValueError("Ungewöhnlicher Startzeitversatz zwischen Bild und Ton.")
    vc, ac = video_changes(nv, sv), audio_changes(na, sa)
    if vc:
        if nv.get("codec_name") != "h264" or nv.get("pix_fmt") != "yuv420p" or not profile(nv.get("profile")):
            raise ValueError("Für dieses native Videoformat ist keine automatische Umwandlung vorbereitet.")
        if nv.get("sample_aspect_ratio") not in ("1:1", None) or nv.get("field_order") not in ("progressive", "unknown", None):
            raise ValueError("Die native Bildgeometrie benötigt eine gesonderte Anpassung.")
        if not isinstance(nv.get("level"), int) or not 10 <= nv["level"] <= 62:
            raise ValueError("Unbekanntes natives H.264-Level.")
        nsar = Fraction((nv.get("sample_aspect_ratio") or "1:1").replace(":", "/"))
        ssar = Fraction((sv.get("sample_aspect_ratio") or "1:1").replace(":", "/"))
        nr = Fraction(nv["width"], nv["height"]) * nsar
        sr = Fraction(sv["width"], sv["height"]) * ssar
        if abs(float(sr / nr) - 1) > 0.01:
            raise ValueError("Abweichendes Bildseitenverhältnis; kein automatisches Strecken oder Beschneiden.")
    if ac and (na.get("codec_name") != "aac" or na.get("profile") != "LC"
               or na.get("channels") not in (1, 2) or not number(na.get("sample_rate"))):
        raise ValueError("Für dieses native Audioformat ist keine automatische Umwandlung vorbereitet.")
    return {"video_changes": vc, "audio_changes": ac,
            "action": "convert" if vc or ac else "copy", "duration_tolerance_seconds": tolerance}


def convert(tools, source, target, native, plan, log):
    nv, na = native["video"], native["audio"]
    args = ["-protocol_whitelist", "file", "-f", "mov", "-i", str(source.resolve()),
            "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "-1", "-map_chapters", "-1"]
    if plan["video_changes"]:
        level = nv["level"]
        maxrate = max(64_000, int((number(nv.get("bit_rate")) or 6_000_000) * 1.25))
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", profile(nv["profile"]),
                 "-level:v", f"{level // 10}.{level % 10}", "-pix_fmt", "yuv420p",
                 "-maxrate", str(maxrate), "-bufsize", str(maxrate * 2),
                 "-bf", "0" if nv.get("has_b_frames") == 0 else "2",
                 "-vf", f"scale={nv['width']}:{nv['height']}:flags=lanczos,setsar=1,fps={effective_rate(nv)}"]
        for field, option in (("color_range", "-color_range"), ("color_space", "-colorspace"),
                              ("color_transfer", "-color_trc"), ("color_primaries", "-color_primaries")):
            if nv.get(field) not in (None, "unknown", "unspecified"):
                args += [option, str(nv[field])]
    else:
        args += ["-c:v", "copy"]
    if plan["audio_changes"]:
        bitrate = min(512_000, max(64_000, int(number(na.get("bit_rate")) or 192_000)))
        args += ["-c:a", "aac", "-profile:a", "aac_low", "-ar", str(na["sample_rate"]),
                 "-ac", str(na["channels"]), "-b:a", str(bitrate)]
    else:
        args += ["-c:a", "copy"]
    if nv.get("codec_tag_string"):
        args += ["-tag:v", nv["codec_tag_string"]]
    brand = native.get("major_brand")
    if brand and len(brand) == 4 and brand.isascii():
        args += ["-brand", brand]
    args += ["-metadata:s:a:0", "language=deu", "-movflags", "+faststart", "-n", str(target.resolve())]
    tools.run(args, log)


def verify_media(native, actual):
    vc = video_changes(native["video"], actual["video"])
    # Average bitrate is an estimate, not an output format property.
    vc = [key for key in vc if key != "bit_rate"]
    ac = audio_changes(native["audio"], actual["audio"])
    if vc or ac:
        raise ValueError("Ausgabeformat weicht von der nativen Vorlage ab: " + ", ".join(vc + ac))
    plan_conversion(native, actual)


def write_diagnostic(output, report):
    (output / "movie-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    target = output / "KH2-Film-Diagnose.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("movie-result.json", json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr("LESEN.txt", "KH2-Filmprüfung: nur Metadaten und begrenzte Protokolle; keine Filme.\n")
        for log in sorted((output / "logs").glob("*.log")):
            if log.stat().st_size:
                with log.open("rb") as stream:
                    archive.writestr("logs/" + log.name, stream.read(128 * 1024))
    return target


def build(root, movie_path, output, title_id, version="", ffmpeg=None, ffprobe=None, diagnose_only=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Bitte einen neuen Ausgabeordner wählen.")
    # Reject output inside the ROMFS or source directory: inputs stay untouched.
    source_path = Path(movie_path).resolve()
    if output.is_relative_to(root) or (source_path.is_dir() and output.is_relative_to(source_path)):
        raise ValueError("Der Ausgabeordner muss außerhalb von ROMFS und Filmquelle liegen.")
    output.mkdir(parents=True)
    work = output / "work"
    work.mkdir()
    logs = output / "logs"
    logs.mkdir()
    report = {"tool_version": "KH2-9.0", "kh2_program_id": title_id,
              "game_version_user_supplied": version, "hardware_tested": False,
              "original_files_modified": False, "status": "Prüfung läuft", "films": {}, "errors": []}
    try:
        tools = MediaTools(ffmpeg, ffprobe)
        with MovieSource(source_path) as source:
            report["source"] = source.summary()
            print("Filmquelle: " + (report["source"]["detected_download"] or "KH2-Filme ohne Plattformordner"), flush=True)
            native_inventory = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".mp4")
            report["native_mp4_inventory"] = [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size}
                                              for p in native_inventory]
            natives = native_movies(root)
            if report["source"]["missing_films"]:
                report["errors"].append("Filmpaket unvollständig: " + ", ".join(report["source"]["missing_films"]))
            missing = set(source.records) - natives.keys()
            if missing:
                report["errors"].append("Kein eindeutiges natives KH2-Ziel: " + ", ".join(sorted(missing)))
                report["unmapped_native_samples"] = []
                for path in [p for p in native_inventory if p.stem.lower() in missing][:3]:
                    sample = {"path": path.relative_to(root).as_posix()}
                    try:
                        sample["media"] = tools.probe(path)
                    except (ValueError, OSError, subprocess.SubprocessError) as error:
                        sample["error"] = str(error)
                    report["unmapped_native_samples"].append(sample)
            needed = 2 * sum(r["size"] for r in source.records.values()) + 2 * sum(
                p.stat().st_size for p in natives.values()) + 512 * MIB
            if shutil.disk_usage(output).free < needed:
                raise ValueError(f"Zu wenig Platz; mindestens {needed / 1024**3:.1f} GiB frei halten.")
            inputs = work / "input"
            inputs.mkdir()
            for number_, film in enumerate(sorted(source.records), 1):
                item = report["films"][film] = {"source_path": source.records[film]["path"]}
                if film not in natives:
                    continue
                native = natives[film]
                item["target_path"] = native.relative_to(root).as_posix()
                source_file = inputs / (film + ".mp4")
                print(f"[{number_}/{len(source.records)}] {film}.mp4 prüfen ...", flush=True)
                try:
                    hasher = hashlib.sha256()
                    with source.open(film) as stream, source_file.open("xb") as dest:
                        for chunk in iter(lambda: stream.read(MIB), b""):
                            hasher.update(chunk)
                            dest.write(chunk)
                    item["source_sha256"] = hasher.hexdigest()
                    item["native"] = tools.probe(native)
                    item["german"] = tools.probe(source_file)
                    item["plan"] = plan_conversion(item["native"], item["german"])
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    item["error"] = str(error)
                    report["errors"].append(film + ": " + str(error))
            if report["errors"]:
                raise ValueError("Filmprüfung fehlgeschlagen. Bitte KH2-Film-Diagnose.zip zur Anpassung hochladen.")
            if diagnose_only:
                report["status"] = "Metadaten geprüft; kein Mod gebaut"
                return output / "KH2-Film-Diagnose.zip"
            staged = work / "atmosphere" / "contents" / title_id / "romfs"
            for number_, (film, item) in enumerate(sorted(report["films"].items()), 1):
                source_file = inputs / (film + ".mp4")
                target = staged / item["target_path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                print(f"[{number_}/{len(report['films'])}] {film}.mp4: {item['plan']['action']} ...", flush=True)
                if item["plan"]["action"] == "copy":
                    shutil.copyfile(source_file, target)
                    if digest(target) != item["source_sha256"]:
                        raise ValueError("Prüfsummenfehler bei " + film)
                else:
                    convert(tools, source_file, target, item["native"], item["plan"], logs / (film + "-convert.log"))
                item["output"] = tools.probe(target)
                verify_media(item["native"], item["output"])
                tools.run(["-protocol_whitelist", "file", "-f", "mov", "-i", str(target.resolve()),
                           "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], logs / (film + "-decode.log"))
                item["output_sha256"] = digest(target)
                item["output_bytes"] = target.stat().st_size
                item["full_decode_verified"] = True
            report["movies_verified"] = len(report["films"])
            report["movies_converted"] = sum(i["plan"]["action"] == "convert" for i in report["films"].values())
            report["status"] = "Dateien und vollständige Dekodierung geprüft; Test im Spiel steht aus"
            (work / "atmosphere").rename(output / "atmosphere")
            return output / "atmosphere" / "contents" / title_id / "romfs"
    except BaseException as error:
        report["status"] = "Abgebrochen; kein Film-Mod veröffentlicht"
        if str(error) and str(error) not in report["errors"]:
            report["errors"].append(str(error))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        diagnostic = write_diagnostic(output, report)
        print("Film-Diagnose: " + str(diagnostic), flush=True)
