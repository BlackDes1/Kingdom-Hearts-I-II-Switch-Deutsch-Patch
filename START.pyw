"""Double-click entry, and the optional frozen application's worker entry."""
import sys
import traceback
from pathlib import Path


def main():
    if len(sys.argv)==3 and sys.argv[1]=="--worker":
        from worker import main as worker_main
        return worker_main(sys.argv[2])
    try:
        if sys.version_info<(3,10):raise RuntimeError("Python 3.10 oder neuer wird benötigt.")
        from kh_gui import main as gui_main
        gui_main()
        return 0
    except Exception:
        text=traceback.format_exc()
        try:
            import os
            folder=Path(os.environ.get("LOCALAPPDATA",str(Path.home()))) / "KingdomHeartsSwitchPatcher"
            folder.mkdir(parents=True,exist_ok=True)
            path=folder / "Startfehler.log"
            path.write_text(text,encoding="utf-8")
            text="Die Oberfläche konnte nicht gestartet werden.\n\n"+text+"\nProtokoll: "+str(path)
        except OSError:pass
        if sys.platform=="win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,text,"Kingdom Hearts Patcher",16)
        elif sys.stderr is not None:sys.stderr.write(text)
        return 1


if __name__=="__main__":raise SystemExit(main())
