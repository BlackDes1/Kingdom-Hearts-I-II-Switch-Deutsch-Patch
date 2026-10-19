"""Run exactly one original engine per process, with a fixed game ID."""
from __future__ import annotations
import contextlib
import datetime
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
import zipfile
import zlib
from patcher_core import EVENT_PREFIX, GAMES, RESOURCE_ROOT, VERSION, JobSpec, clean_path, find_tools


def emit(**event):
    print(EVENT_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)


def run_job(request, engine=None):
    values = {key: request.get(key, field.default) for key, field in JobSpec.__dataclass_fields__.items()}
    spec = JobSpec(**values).validate()
    root = clean_path(request["job_root"])
    output = root / "Berichte"
    game = GAMES[spec.game]
    emit(type="progress", value=3, label="" + game["short"] + " · Checking inputs and tools")
    if engine is None:
        sys.path.insert(0, str(RESOURCE_ROOT / "engines" / spec.game))
        engine = importlib.import_module(spec.game + "_switch")
    tools = {}
    if spec.game == "kh2" and (spec.action == "build" or spec.needs_movies):
        tools = find_tools(spec.tools, need_probe=spec.needs_movies)
        print("FFmpeg: " + tools["ffmpeg"], flush=True)
    print(game["name"] + " | " + game["engine"], flush=True)
    print("Program-ID: " + game["id"], flush=True)
    romfs = clean_path(spec.romfs)
    warnings = []
    if spec.action == "diagnose":
        output.mkdir()
        if spec.game == "kh1":
            report = engine.diagnose(romfs, output / "KH1-Switch-Diagnose.zip", game["id"])
            if report.get("inspection_error"):
                warnings.append(report["inspection_error"])
        else:
            if spec.needs_voices:
                try:
                    with engine.PatchSource(clean_path(spec.mod), voice_language=spec.voice_language) as source:
                        report = engine.diagnose(romfs, source, output / "KH2-Switch-Diagnose.zip", game["id"])
                        warnings += [e["error"] for e in report.get("inspection", {}).get("errors", [])]
                except Exception as error:
                    warnings.append(str(error))
            if spec.needs_movies:
                try:
                    engine.build_movies(romfs, clean_path(spec.movies), output / "Filme", game["id"],
                                        ffmpeg=tools["ffmpeg"], ffprobe=tools["ffprobe"], diagnose_only=True)
                except Exception as error:
                    warnings.append(str(error))
        print("Diagnostics complete." + (" Notes: " + "; ".join(warnings) if warnings else ""), flush=True)
        return {"success": True, "diagnosis": True, "warnings": warnings, "game": spec.game,
                "program_id": game["id"], "output": str(output)}
    if spec.game == "kh1":
        engine.build(romfs, clean_path(spec.mod), output, game["id"],
                     voice_language=spec.voice_language)
    elif spec.mode == "movies":
        engine.build_movies(romfs, clean_path(spec.movies), output, game["id"],
                            ffmpeg=tools["ffmpeg"], ffprobe=tools["ffprobe"])
    else:
        with engine.PatchSource(clean_path(spec.mod), voice_language=spec.voice_language) as source:
            if spec.mode == "all":
                engine.build_all(romfs, source, clean_path(spec.movies), output, game["id"],
                                 ffmpeg=tools["ffmpeg"], ffprobe=tools["ffprobe"])
            else:
                engine.build(romfs, source, output, game["id"], ffmpeg=tools["ffmpeg"])
    if (root / "Abbrechen.flag").exists():
        raise InterruptedError("Job cancelled.")
    source_tree = output / "atmosphere"
    contents = source_tree / "contents"
    ids = sorted(p.name for p in contents.iterdir()) if contents.is_dir() else []
    if ids != [game["id"]] or not (contents / game["id"] / "romfs").is_dir():
        raise ValueError("The patcher did not create output for the fixed Program ID.")
    report_file = output / ("movie-result.json" if spec.mode == "movies" else "build-result.json")
    if not report_file.is_file():
        raise ValueError("The patcher report is missing; no output was published.")
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if spec.needs_voices:
        report["voice_language"] = spec.voice_language
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if (spec.game == "kh2" and spec.mode == "all" and
            not (report.get("voices_completed") and report.get("movies_completed"))):
        raise ValueError("Voices and movies have not both passed verification.")
    emit(type="progress", value=99, label="Publishing verified atmosphere folder")
    source_tree.rename(root / "atmosphere")
    return {"success": True, "diagnosis": False, "game": spec.game, "program_id": game["id"],
            "output": str(root / "atmosphere"), "engine": game["engine"], "app_version": VERSION}


def main(request_path):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    root = clean_path(request["job_root"])
    try:
        result = run_job(request)
        code = 0
    except BaseException as error:
        trace = traceback.format_exc()
        print(trace, flush=True)
        result = {"success": False, "cancelled": isinstance(error, (KeyboardInterrupt, InterruptedError)),
                  "error": str(error) or type(error).__name__, "traceback": trace}
        code = 1
    (root / "Ergebnis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
