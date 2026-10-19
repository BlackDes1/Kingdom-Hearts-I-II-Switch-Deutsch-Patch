"""English desktop interface, using only the Python standard library."""
from __future__ import annotations
import os
import re
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import webbrowser

from patcher_core import VERSION, APP_ROOT, RESOURCE_ROOT, GAMES, JobRunner, JobSpec, load_settings, save_settings, find_tools
from tool_setup import ToolSetup, validate_tools

BG = "#0a111c"
SIDE = "#0e1725"
PANEL = "#131f30"
FIELD = "#0d1725"
LINE = "#29394d"
TEXT = "#ecf1f8"
MUTED = "#91a2b9"
GOLD = "#dfbf85"
MINT = "#87d8bd"
RED = "#f29b9b"
FONT = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
MONO = "Consolas" if os.name == "nt" else "DejaVu Sans Mono"


LOG_TRANSLATIONS = (
    ("[1/3] Stimmen prüfen und bauen", "[1/3] Checking and building voices"),
    ("[2/3] Deutsche Filme prüfen und bauen", "[2/3] Checking and building German movies"),
    ("[3/3] Gemeinsamen atmosphere-Ordner bereitstellen", "[3/3] Publishing combined atmosphere folder"),
    ("Sprachformat anpassen:", "Adjusting voice format:"),
    ("Native Patchdateien geprüft:", "Native patch files checked:"),
    ("Vorprüfung:", "Preflight:"),
    ("Moddateien geprüft:", "Mod files checked:"),
    ("Native Stimmen und Ressourcen bauen:", "Building native voices and resources:"),
    ("Alle eingebauten Dateien und unveränderten Ressourcen prüfen", "Checking patched files and unchanged resources"),
    ("Eingebaute Audiodateien und übrige Ressourcen prüfen", "Checking patched audio and remaining resources"),
    ("Archive, Audionamen, Größen und Originaldaten prüfen", "Checking archives, audio names, sizes, and original data"),
    ("Patch und Switch-Dateien prüfen", "Checking patch and Switch files"),
    ("Switch-Archiv bauen", "Building Switch archive"),
    ("KH1-Program-ID:", "KH1 Program ID:"),
    ("KH2-Program-ID:", "KH2 Program ID:"),
    ("Filmquelle:", "Movie source:"),
    ("Film-Diagnose:", "Movie diagnostics:"),
    ("Nexus-ZIP öffnen:", "Opening Nexus ZIP:"),
    ("FFmpeg-Prozessabsturz", "FFmpeg process crash"),
    ("FFmpeg arbeitet weiter", "FFmpeg is still working"),
    ("einmalige Wiederholung mit CPU-Basispfad", "retrying once with the CPU baseline"),
    ("PCM-Dekodierung", "PCM decoding"),
    ("Soundplatz", "sound slot"),
    (" Spuren)", " tracks)"),
    (" bauen ...", " building ..."),
    ("KH1 Switch - Deutsche oder japanische Stimmen", "KH1 Switch - German or Japanese voices"),
    ("KH2 Final Mix – Deutsche Stimmen und Filme", "KH2 Final Mix – German voices and movies"),
    ("Den erzeugten atmosphere-Ordner auf die SD-Karte kopieren.", "Copy the generated atmosphere folder to the SD card."),
    ("Ein Test im Spiel steht noch aus.", "An in-game test is still pending."),
    ("FERTIG:", "DONE:"),
    ("FEHLER:", "ERROR:"),
    ("Fehlerprotokoll:", "Error log:"),
)


def translate_log_line(text):
    """Translate engine progress messages before they appear in the English GUI."""
    for source, target in LOG_TRANSLATIONS:
        text = text.replace(source, target)
    movie_check = re.match(r"^(\[\d+/\d+\])\s+(.+?) prüfen \.\.\.$", text)
    if movie_check:
        return movie_check.group(1) + " Checking " + movie_check.group(2) + " ..."
    return text


def label(parent, text="", size=10, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                    bg=parent.cget("bg"), fg=color, anchor="w", **kwargs)


def button(parent, text, command, primary=False, **kwargs):
    return tk.Button(parent, text=text, command=command, font=(FONT, 10, "bold" if primary else "normal"),
                     bg=GOLD if primary else "#213147", fg=BG if primary else TEXT,
                     activebackground="#f0d39f" if primary else "#2c4059", activeforeground=BG if primary else TEXT,
                     disabledforeground="#67788c", relief="flat", bd=0, cursor="hand2", padx=13, pady=8,
                     highlightthickness=0, **kwargs)


def rounded(canvas, x1, y1, x2, y2, radius=14, **kwargs):
    points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,
              x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class GameCard(tk.Canvas):
    def __init__(self, parent, game, command):
        super().__init__(parent, bg=BG, height=112, highlightthickness=0, cursor="hand2")
        self.game, self.command, self.selected, self.enabled = game, command, False, True
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<Button-1>", lambda event: self.command(self.game) if self.enabled else None)

    def draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 160)
        rounded(self, 1, 1, w-2, 108, fill="#19283a" if self.selected else PANEL,
                outline=GOLD if self.selected else LINE, width=1.5)
        roman = "I" if self.game == "kh1" else "II"
        self.create_text(w-26, 68, text=roman, font=("Georgia", 44), fill="#40516a" if self.selected else "#2b3a50", anchor="e")
        self.create_text(19, 27, text="KINGDOM HEARTS " + roman, font=(FONT, 13, "bold"), fill=TEXT, anchor="w")
        self.create_text(19, 49, text="FINAL MIX", font=(FONT, 9), fill=GOLD if self.selected else MUTED, anchor="w")
        self.create_text(19, 84, text=GAMES[self.game]["id"], font=(MONO, 10), fill=MINT, anchor="w")
        if self.selected:
            rounded(self, w-78, 15, w-16, 34, radius=7, fill="#3c3830", outline="")
            self.create_text(w-47, 25, text="ACTIVE", font=(FONT, 8, "bold"), fill=GOLD)


class PathField(tk.Frame):
    def __init__(self, parent, title, hint, kind="folder", link=None):
        super().__init__(parent, bg=BG)
        self.value = tk.StringVar()
        self.kind, self.enabled = kind, True
        heading = tk.Frame(self, bg=BG)
        heading.pack(fill="x", pady=(0, 5))
        self.title = label(heading, title, size=8, color=MUTED, bold=True)
        self.title.pack(side="left")
        if link:
            tk.Button(heading, text="Open mod page  ↗", command=lambda: webbrowser.open(link),
                      bg=BG, fg=GOLD, activebackground=BG, activeforeground=TEXT, bd=0,
                      font=(FONT, 8), cursor="hand2", padx=0, pady=0, highlightthickness=0).pack(side="right")
        row = tk.Frame(self, bg=FIELD, highlightbackground=LINE, highlightthickness=1)
        row.pack(fill="x")
        entry_frame = tk.Frame(row, bg=FIELD)
        entry_frame.pack(side="left", fill="both", expand=True)
        self.entry = tk.Entry(entry_frame, textvariable=self.value, bg=FIELD, fg=TEXT, insertbackground=GOLD,
                              font=(FONT, 10), relief="flat", bd=0, highlightthickness=0)
        self.entry.pack(fill="both", expand=True, padx=12, pady=10)
        self.placeholder = tk.Label(entry_frame, text=hint, bg=FIELD, fg="#60748f", font=(FONT, 9), anchor="w")
        self.placeholder.bind("<Button-1>", lambda event: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda event: (row.config(highlightbackground=GOLD), self.placeholder.place_forget()))
        self.entry.bind("<FocusOut>", lambda event: (row.config(highlightbackground=LINE), self.update_hint()))
        self.value.trace_add("write", lambda *args: self.update_hint())
        self.buttons = []
        if kind == "file":
            self.buttons.append(button(row, "ZIP / File", lambda: self.browse(False), width=9))
            self.buttons.append(button(row, "Folder", lambda: self.browse(True), width=6))
        else:
            self.buttons.append(button(row, "Choose folder", lambda: self.browse(True), width=13))
        for widget in reversed(self.buttons):
            widget.pack(side="right", padx=(0, 3), pady=3)
        self.update_hint()

    def update_hint(self):
        if not self.value.get() and self.entry.focus_get() != self.entry:
            self.placeholder.place(x=12, y=11)
        else:
            self.placeholder.place_forget()

    def browse(self, folder):
        if not self.enabled:
            return
        if folder:
            value = filedialog.askdirectory(parent=self, title=self.title.cget("text"), mustexist=True)
        else:
            value = filedialog.askopenfilename(parent=self, title=self.title.cget("text"),
                                               filetypes=[("Mod files", "*.zip *.kh1pcpatch *.kh2pcpatch"), ("Alle Dateien", "*.*")])
        if value:
            self.value.set(value)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.entry.config(state="normal" if enabled else "disabled", disabledbackground=FIELD, disabledforeground=MUTED)
        for widget in self.buttons:
            widget.config(state="normal" if enabled else "disabled")


class PatcherApp(tk.Tk):
    def __init__(self, auto_setup=True):
        super().__init__()
        self.title("Kingdom Hearts · Switch Patcher")
        self.configure(bg=BG)
        self.geometry(f"{min(1180, self.winfo_screenwidth()-50)}x{min(900, self.winfo_screenheight()-70)}")
        self.minsize(980, 700)
        try:
            if os.name == "nt":
                self.iconbitmap(str(RESOURCE_ROOT / "assets/app.ico"))
            else:
                self.app_icon = tk.PhotoImage(file=str(RESOURCE_ROOT / "assets/app.png"))
                self.iconphoto(True, self.app_icon)
        except tk.TclError:
            pass
        self.runner = JobRunner()
        self.tool_setup = ToolSetup()
        self.auto_tools = auto_setup and os.name == "nt"
        self.setup_busy, self.setup_error = False, ""
        self.tools_ready = not self.auto_tools
        self.setup_last_phase = None
        self.setup_after = None
        self.saved = load_settings()
        self.game = self.saved.get("game", "kh2")
        if self.game not in GAMES:
            self.game = "kh2"
        self.voice_language = tk.StringVar(value="de")
        profiles = self.saved.get("profiles", {})
        self.profiles = profiles if isinstance(profiles, dict) else {}
        self.action, self.mode, self.page = "build", "all", "build"
        self.busy, self.stopping, self.started_at = False, False, None
        self.last_root, self.last_diagnostic = None, None
        self.tools_open = False
        self._layout()
        self._restore_profile()
        self.output.value.set(str(self.saved.get("output", "")))
        self.tools.value.set(str(self.saved.get("tools", "")))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_after = self.after(100, self.poll)
        self.refresh_start_state()
        if self.auto_tools:
            self.setup_after = self.after(150, self.begin_tool_setup)

    def _layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        sidebar = tk.Frame(self, bg=SIDE, width=180)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.pack_propagate(False)
        logo = tk.Canvas(sidebar, width=180, height=172, bg=SIDE, highlightthickness=0)
        logo.pack(fill="x", pady=(24, 8))
        logo.create_polygon(61,27,75,39,90,15,105,39,119,27,112,58,68,58, fill=GOLD)
        logo.create_line(70,65,110,65, fill=GOLD, width=2)
        logo.create_text(90,102,text="KINGDOM\nHEARTS",font=(FONT,14,"bold"),fill=TEXT,justify="center")
        logo.create_text(90,145,text="SWITCH PATCHER",font=(FONT,8),fill=MUTED)
        self.nav = {}
        for key, text in (("build", "01   Build patch"), ("diagnose", "02   Diagnostics"), ("log", "≡    Live log")):
            widget = tk.Button(sidebar, text=text, anchor="w", command=lambda p=key: self.show_page(p),
                               bg=SIDE, fg=MUTED, activebackground="#22334a", activeforeground=TEXT,
                               font=(FONT,10), bd=0, relief="flat", cursor="hand2", padx=18, pady=15, highlightthickness=0)
            widget.pack(fill="x", padx=9, pady=3)
            self.nav[key] = widget
        bottom = tk.Frame(sidebar, bg=SIDE)
        bottom.pack(side="bottom", fill="x", padx=23, pady=26)
        label(bottom, "●  Files stay local", 9, MINT).pack(anchor="w", pady=(0,9))
        label(bottom, "KH1 v2  ·  KH2 v9", 8, MUTED).pack(anchor="w")
        label(bottom, "Desktop " + VERSION, 8, "#62748c").pack(anchor="w", pady=(5,0))

        main = tk.Frame(self, bg=BG)
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=(24,20))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        header = tk.Frame(main, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0,20))
        label(header, "KINGDOM HEARTS  /  NATIVE SWITCH", 8, GOLD, True).pack(anchor="w")
        self.heading = label(header, "Your games. Your language.", 25, TEXT, True)
        self.heading.pack(anchor="w", pady=(9,5))
        self.subtitle = label(header, "German and Japanese voices, plus movies, in one tool.", 10, MUTED)
        self.subtitle.pack(anchor="w")
        center = tk.Frame(main, bg=BG)
        center.grid(row=1, column=0, sticky="nsew")
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)
        self.form_page = tk.Frame(center, bg=BG)
        self.form_page.grid(row=0, column=0, sticky="nsew")
        self.form_page.grid_columnconfigure(0, weight=1)
        self.form_page.grid_rowconfigure(2, weight=1)
        cards = tk.Frame(self.form_page, bg=BG)
        cards.grid(row=0, column=0, sticky="ew")
        cards.grid_columnconfigure((0,1), weight=1, uniform="games")
        self.cards = {}
        for i, game in enumerate(GAMES):
            self.cards[game] = GameCard(cards, game, self.select_game)
            self.cards[game].grid(row=0,column=i,sticky="ew",padx=(0,7) if i==0 else (7,0))
        modes = tk.Frame(self.form_page, bg=BG)
        modes.grid(row=1, column=0, sticky="ew", pady=(9,15))
        label(modes, "CONTENT", 8, MUTED, True).pack(side="left", padx=(0,16))
        self.mode_buttons = {}
        for key, title in (("all","Voices + Movies"),("voices","Voices"),("movies","Movies")):
            widget = button(modes, title, lambda k=key:self.select_mode(k))
            widget.pack(side="left", padx=(0,5))
            self.mode_buttons[key] = widget
        self.voice_language_bar = tk.Frame(modes, bg=BG)
        self.voice_language_bar.pack(side="right")
        label(self.voice_language_bar, "VOICES", 8, MUTED, True).pack(side="left", padx=(0,8))
        self.voice_language_buttons = {}
        for key, title in (("de", "German"), ("ja", "Japanese")):
            widget = button(self.voice_language_bar, title, lambda value=key:self.select_voice_language(value))
            widget.pack(side="left", padx=(0,5))
            self.voice_language_buttons[key] = widget
        self.canvas = tk.Canvas(self.form_page, bg=BG, highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky="nsew")
        self.scrollbar = tk.Scrollbar(self.form_page, command=self.canvas.yview, bg=SIDE, troughcolor=BG, width=10)
        self.scrollbar.grid(row=2,column=1,sticky="ns")
        self.canvas.configure(yscrollcommand=self.sync_form_scroll)
        self.fields = tk.Frame(self.canvas, bg=BG)
        self.fields_window = self.canvas.create_window((0,0), window=self.fields, anchor="nw")
        self.fields.bind("<Configure>", lambda e:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.resize_form)
        self.bind_all("<MouseWheel>", self.scroll_form, add="+")
        self.fields.grid_columnconfigure(0, weight=1)
        self.romfs = PathField(self.fields, "GAME ROMFS", "Select the extracted ROMFS folder")
        self.voice = PathField(self.fields, "VOICE MOD", "Voice mod as a ZIP file or folder", "file",
                               link="https://github.com/Rikux3/kh1fm-undub-ger-pc" )
        # The source link follows the selected game instead of opening an unrelated page.
        for child in self.voice.winfo_children()[0].winfo_children():
            if isinstance(child, tk.Button):
                child.configure(command=self.open_voice_mod)
        self.movies = PathField(self.fields, "GERMAN MOVIES · STEAM", "Select German movies (STEAM)", "file",
                                link="https://www.nexusmods.com/kingdomhearts2finalmix/mods/42?tab=files")
        self.output = PathField(self.fields, "OUTPUT FOLDER", "New, separate patch folders will be created here")
        for i, widget in enumerate((self.romfs,self.voice,self.movies,self.output)):
            widget.grid(row=i,column=0,sticky="ew",pady=(0,12))
        self.tools_toggle = tk.Button(self.fields, text="▸  Tools · automatic download to bin", command=self.toggle_tools,
                                      bg=BG, fg=MUTED, activebackground=BG, activeforeground=GOLD, bd=0,
                                      font=(FONT,9), anchor="w", cursor="hand2", pady=4, highlightthickness=0)
        self.tools_toggle.grid(row=4,column=0,sticky="ew",pady=(0,7))
        self.tools = PathField(self.fields,"FFMPEG FOLDER · OPTIONAL", "Folder containing ffmpeg.exe and ffprobe.exe")
        self.tools.grid(row=5,column=0,sticky="ew",pady=(0,10))
        self.tools.grid_remove()
        self.scope_note = label(self.fields,"",9,MUTED,wraplength=750,justify="left")
        self.scope_note.grid(row=6,column=0,sticky="ew",pady=(0,8))
        self.log_page = tk.Frame(center, bg=BG)
        self.log_page.grid(row=0,column=0,sticky="nsew")
        self.log_page.grid_rowconfigure(1,weight=1)
        self.log_page.grid_columnconfigure(0,weight=1)
        bar = tk.Frame(self.log_page,bg=BG)
        bar.grid(row=0,column=0,sticky="ew",pady=(0,12))
        label(bar,"LIVE LOG",8,GOLD,True).pack(side="left")
        button(bar,"Save log",self.export_log).pack(side="right")
        self.log = tk.Text(self.log_page,bg=FIELD,fg="#b8c7da",insertbackground=GOLD,bd=0,highlightthickness=1,
                           highlightbackground=LINE,font=(MONO,10),wrap="word",padx=17,pady=15,state="disabled")
        self.log.grid(row=1,column=0,sticky="nsew")
        scroll = tk.Scrollbar(self.log_page,command=self.log.yview,width=11)
        scroll.grid(row=1,column=1,sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.tag_configure("error",foreground=RED)
        self.log.tag_configure("notice",foreground=GOLD)
        self.log_page.grid_remove()

        footer = tk.Frame(main,bg=BG)
        footer.grid(row=2,column=0,sticky="ew",pady=(18,0))
        toolrow = tk.Frame(footer, bg=BG)
        toolrow.pack(fill="x", pady=(0,9))
        self.tool_status = label(toolrow, "FFmpeg + ffprobe · set up at startup" if self.auto_tools else "FFmpeg + ffprobe · bin / custom folder / PATH", 8, MUTED)
        self.tool_status.pack(side="left")
        self.tool_retry = tk.Button(toolrow, text="Retry download", command=self.begin_tool_setup,
                                    bg=BG, fg=GOLD, activebackground=BG, activeforeground=TEXT,
                                    font=(FONT,8), bd=0, cursor="hand2", highlightthickness=0)
        tk.Frame(footer,bg=LINE,height=1).pack(fill="x",pady=(0,14))
        statusrow=tk.Frame(footer,bg=BG)
        statusrow.pack(fill="x")
        self.status=label(statusrow,"●  Ready to build your patch",11,TEXT,True)
        self.status.pack(side="left")
        self.percent=label(statusrow,"Ready",9,MUTED)
        self.percent.pack(side="right")
        self.progress=tk.Canvas(footer,height=7,bg=BG,highlightthickness=0)
        self.progress.pack(fill="x",pady=(12,9))
        self.progress.bind("<Configure>",lambda e:self.paint_progress())
        self.progress_value=0
        detailrow=tk.Frame(footer,bg=BG)
        detailrow.pack(fill="x",pady=(0,13))
        self.detail=label(detailrow,"Select a game and files, then build the patch.",9,MUTED)
        self.detail.pack(side="left")
        self.elapsed=label(detailrow,"",9,MUTED)
        self.elapsed.pack(side="right")
        actions=tk.Frame(footer,bg=BG)
        actions.pack(fill="x")
        self.open_button=button(actions,"Open output",self.open_output,state="disabled")
        self.open_button.pack(side="left",padx=(0,6))
        self.diagnostic_button=button(actions,"Open diagnostics",self.open_diagnostic,state="disabled")
        self.diagnostic_button.pack(side="left")
        self.start_button=button(actions,"Build patch   ›",self.start,primary=True,width=22)
        self.start_button.pack(side="right")
        self.cancel_button=button(actions,"Cancel",self.cancel,state="disabled")
        self.cancel_button.pack(side="right",padx=8)
        self.show_page("build")

    def scroll_form(self,event):
        widget=self.winfo_containing(event.x_root,event.y_root)
        while widget:
            if widget==self.form_page:
                self.canvas.yview_scroll(-int(event.delta/120),"units")
                return
            widget=getattr(widget,"master",None)

    def sync_form_scroll(self,first,last):
        self.scrollbar.set(first,last)
        if float(first)<=0 and float(last)>=1:self.scrollbar.grid_remove()
        else:self.scrollbar.grid()

    def resize_form(self,event):
        self.canvas.itemconfigure(self.fields_window,width=event.width)
        if hasattr(self,"scope_note"):self.scope_note.config(wraplength=max(200,event.width-8))

    def _remember_profile(self):
        self.profiles[self.game]={"romfs":self.romfs.value.get(),"mod":self.voice.value.get(),
                                  "movies":self.movies.value.get(),"mode":self.mode,
                                  "voice_language":self.voice_language.get()}

    def _restore_profile(self):
        profile=self.profiles.get(self.game,{})
        if not isinstance(profile,dict):profile={}
        self.romfs.value.set(str(profile.get("romfs","")))
        self.voice.value.set(str(profile.get("mod","")))
        self.movies.value.set(str(profile.get("movies","")))
        language=profile.get("voice_language","de")
        self.voice_language.set(language if language in ("de","ja") else "de")
        self.mode="voices" if self.game=="kh1" else profile.get("mode","all")
        if self.mode not in ("all","voices","movies"):self.mode="all"
        self.refresh_form()

    def select_game(self,game):
        if self.busy or game==self.game:return
        self._remember_profile()
        self.game=game
        self._restore_profile()

    def select_mode(self,mode):
        if self.busy or (self.game=="kh1" and mode!="voices"):return
        self.mode=mode
        self.refresh_form()

    def select_voice_language(self,language):
        if self.busy or language not in ("de","ja"):return
        self.voice_language.set(language)
        self.refresh_form()

    def refresh_form(self):
        for game,card in self.cards.items():
            card.selected=game==self.game
            card.enabled=not self.busy
            card.draw()
        for mode,widget in self.mode_buttons.items():
            widget.config(bg="#394037" if mode==self.mode else PANEL,fg=GOLD if mode==self.mode else MUTED,
                          state="disabled" if self.busy or (self.game=="kh1" and mode!="voices") else "normal")
        needs_voices=self.game=="kh1" or self.mode in ("all","voices")
        for language,widget in self.voice_language_buttons.items():
            widget.config(bg="#394037" if language==self.voice_language.get() else PANEL,
                          fg=GOLD if language==self.voice_language.get() else MUTED,
                          state="disabled" if self.busy else "normal")
        self.voice_language_bar.pack(side="right") if needs_voices and not (
            self.action=="diagnose" and self.game=="kh1") else self.voice_language_bar.pack_forget()
        needs_movies=self.game=="kh2" and self.mode in ("all","movies")
        self.voice.grid() if needs_voices and not (self.action=="diagnose" and self.game=="kh1") else self.voice.grid_remove()
        self.movies.grid() if needs_movies else self.movies.grid_remove()
        self.tools_toggle.grid() if self.game=="kh2" else self.tools_toggle.grid_remove()
        self.tools.grid() if self.game=="kh2" and self.tools_open else self.tools.grid_remove()
        language_name="JAPANESE VOICES" if self.voice_language.get()=="ja" else "GERMAN VOICES"
        source_name="TIEULINK" if self.game=="kh2" or self.voice_language.get()=="ja" else "RIKUX3"
        self.voice.title.config(text=language_name+" · "+source_name)
        extension=".kh1pcpatch" if self.game=="kh1" else ".kh2pcpatch"
        self.voice.placeholder.config(text=("Japanese voices · TieuLink · "+extension) if self.voice_language.get()=="ja"
                                      else ("German patch · ZIP or folder · "+extension))
        self.voice.update_hint()
        note=("KH1: German voices. The intro and credits remain in English."
              if self.voice_language.get()=="de" else "KH1: Japanese voices. Movie files are handled separately.")
        if self.game=="kh2":
            note=("KH2: German voices · German movies (STEAM) can be selected separately."
                  if self.voice_language.get()=="de" else "KH2: Japanese voices · movie files remain a separate selection.")
        if self.mode=="movies":note="KH2: Patch movie files only; voice selection is disabled."
        self.scope_note.config(text=note)
        self.start_button.config(text="Run diagnostics   ›" if self.action=="diagnose" else "Build patch   ›")
        self.refresh_start_state()

    def refresh_start_state(self):
        pending = self.setup_busy or (self.auto_tools and not self.tools_ready and not self.setup_error)
        blocked = self.busy or (self.game=="kh2" and pending)
        self.start_button.config(state="disabled" if blocked else "normal")
        self.tool_retry.config(state="disabled" if self.busy or self.setup_busy else "normal")

    def begin_tool_setup(self):
        self.setup_after = None
        if self.tool_setup.running:
            return
        self.setup_busy, self.setup_error, self.tools_ready = True, "", False
        self.setup_last_phase = None
        self.tool_status.config(text="FFmpeg + ffprobe · setting up …", fg=GOLD)
        self.tool_retry.pack_forget()
        self.refresh_start_state()
        self.append_log("FFmpeg and ffprobe: automatic setup in " + str(APP_ROOT / "bin"), "notice")
        self.tool_setup.start(APP_ROOT / "bin")

    def poll_tool_setup(self):
        for _ in range(100):
            try:event = self.tool_setup.events.get_nowait()
            except queue.Empty:break
            phase = event.get("phase")
            if phase == "log":
                self.append_log(event.get("text", ""))
                continue
            if phase == "finished":
                self.setup_busy = False
                self.tools_ready = bool(event.get("success"))
                self.setup_error = "" if self.tools_ready else event.get("error", "Unknown download error.")
                if self.tools_ready:
                    text = "FFmpeg + ffprobe ready · bin"
                    self.tool_status.config(text=text, fg=MINT)
                    self.tool_retry.pack_forget()
                    self.append_log(text + (" · downloaded and verified" if event.get("downloaded") else " · existing tools verified"), "notice")
                else:
                    self.tool_status.config(text="FFmpeg download failed · retry or choose a custom folder", fg=RED)
                    self.tool_retry.pack(side="right")
                    self.append_log("Tool setup failed: " + self.setup_error, "error")
                if not self.busy:
                    self.progress_value = 0
                    self.percent.config(text="Ready" if self.tools_ready else "Download error")
                    self.status.config(text="●  Ready to build your patch" if self.tools_ready else "●  Tools are not ready", fg=TEXT if self.tools_ready else RED)
                    self.detail.config(text="Select a game and files, then build the patch." if self.tools_ready else "See the live log for details. KH1 does not require these tools.")
                    self.paint_progress()
                self.refresh_start_state()
                continue
            if phase != self.setup_last_phase:
                self.setup_last_phase = phase
                self.append_log(event.get("label", "Setting up FFmpeg"))
            detail = "Automatic setup in bin · first download is about 110 MB"
            if event.get("received"):
                received = event["received"] / 1024**2
                total = event.get("total", 0) / 1024**2
                detail = f"{received:.1f} / {total:.1f} MB" if total else f"{received:.1f} MB downloaded"
                self.tool_status.config(text="FFmpeg + ffprobe · Download · " + detail, fg=GOLD)
            if not self.busy:
                self.progress_value = event.get("value", 0)
                self.status.config(text="●  " + event.get("label", "FFmpeg einrichten"), fg=GOLD)
                self.detail.config(text=detail)
                self.percent.config(text=str(round(self.progress_value)) + " %")
                self.paint_progress()

    def toggle_tools(self):
        self.tools_open=not self.tools_open
        self.tools_toggle.config(text=("▾" if self.tools_open else "▸")+"  Werkzeuge · automatischer Download nach bin")
        self.refresh_form()

    def open_voice_mod(self):
        if self.game=="kh1":
            url=("https://www.nexusmods.com/kingdomheartsfinalmix/mods/6?tab=files" if self.voice_language.get()=="ja"
                 else "https://github.com/Rikux3/kh1fm-undub-ger-pc")
        else:
            mod_id="16" if self.voice_language.get()=="ja" else "42"
            url=f"https://www.nexusmods.com/kingdomhearts2finalmix/mods/{mod_id}?tab=files"
        webbrowser.open(url)

    def show_page(self,page):
        if page!="log":
            if self.busy and page!=self.action:return
            self.action=page
        self.page=page
        for key,widget in self.nav.items():widget.config(bg="#223147" if key==page else SIDE,fg=GOLD if key==page else MUTED)
        if page=="log":
            self.form_page.grid_remove();self.log_page.grid()
            self.heading.config(text="Every step, in view.")
            self.subtitle.config(text="Progress and messages for the current job.")
        else:
            self.log_page.grid_remove();self.form_page.grid()
            self.heading.config(text="Inspect files. Verify safely." if page=="diagnose" else "Your games. Your language.")
            self.subtitle.config(text="Create a diagnostic report to help investigate issues." if page=="diagnose" else "German or Japanese voices and movies in one tool.")
            self.refresh_form()

    def append_log(self,text,tag=None):
        text=translate_log_line(str(text))
        at_bottom=self.log.yview()[1]>=0.97
        self.log.config(state="normal")
        self.log.insert("end",text+"\n",tag or ("error" if "ERROR" in text or "Traceback" in text else ""))
        if int(self.log.index("end-1c").split(".")[0])>5000:self.log.delete("1.0","500.0")
        if at_bottom:self.log.see("end")
        self.log.config(state="disabled")

    def paint_progress(self):
        self.progress.delete("all")
        width=max(1,self.progress.winfo_width())
        rounded(self.progress,0,0,width,7,3,fill=LINE,outline="")
        if self.progress_value>0:rounded(self.progress,0,0,max(7,width*self.progress_value/100),7,3,fill=MINT if self.progress_value==100 else GOLD,outline="")

    def set_busy(self,busy):
        self.busy=busy
        for field in (self.romfs,self.voice,self.movies,self.output,self.tools):field.set_enabled(not busy)
        self.start_button.config(state="disabled" if busy else "normal")
        self.cancel_button.config(state="normal" if busy else "disabled")
        self.refresh_form()

    def start(self):
        if self.busy:return
        if self.game=="kh2":
            if self.setup_busy or (self.auto_tools and not self.tools_ready and not self.setup_error):return
            if self.auto_tools and not self.tools_ready:
                try:
                    tools=find_tools(self.tools.value.get())
                    validate_tools(Path(tools["ffmpeg"]).parent)
                except ValueError:
                    messagebox.showerror("FFmpeg is missing", "Retry the download with ‘Retry download’ or choose a working FFmpeg folder under ‘Tools’.\n\n"+self.setup_error, parent=self)
                    return
        spec=JobSpec(game=self.game,action=self.action,mode=self.mode,voice_language=self.voice_language.get(),romfs=self.romfs.value.get(),
                     mod=self.voice.value.get(),movies=self.movies.value.get(),output_base=self.output.value.get(),tools=self.tools.value.get())
        try:spec.validate()
        except ValueError as error:
            messagebox.showerror("Select files",str(error),parent=self);return
        self._remember_profile()
        try:save_settings({"game":self.game,"profiles":self.profiles,"output":self.output.value.get(),"tools":self.tools.value.get()})
        except OSError:pass
        self.log.config(state="normal");self.log.delete("1.0","end");self.log.config(state="disabled")
        self.last_diagnostic=None
        self.diagnostic_button.config(state="disabled")
        self.stopping=False
        self.started_at=time.monotonic()
        self.progress_value=2
        self.paint_progress()
        self.status.config(text="●  Starting job",fg=GOLD)
        self.detail.config(text=GAMES[self.game]["name"])
        self.percent.config(text="2 %")
        try:self.runner.start(spec)
        except Exception as error:
            self.status.config(text="●  Could not start",fg=RED)
            self.detail.config(text=str(error));return
        self.set_busy(True)
        self.append_log(GAMES[self.game]["name"]+" · "+GAMES[self.game]["id"],"notice")

    def cancel(self):
        if not self.busy or self.stopping:return
        self.stopping=True
        self.cancel_button.config(state="disabled")
        self.status.config(text="●  Cancelling job",fg=GOLD)
        threading.Thread(target=self.runner.cancel,daemon=True).start()

    def poll(self):
        self.poll_tool_setup()
        for _ in range(200):
            try:event=self.runner.events.get_nowait()
            except queue.Empty:break
            kind=event.get("type")
            if kind=="log":self.append_log(event.get("text",""))
            elif kind=="started":
                self.last_root=Path(event["root"]);self.open_button.config(state="normal")
            elif kind=="progress" and not self.stopping:
                self.progress_value=max(self.progress_value,float(event["value"]))
                self.status.config(text="●  "+event["label"],fg=GOLD)
                self.percent.config(text=str(round(self.progress_value))+" %");self.paint_progress()
            elif kind=="finished":self.finished(event)
        if self.busy and self.started_at:
            seconds=int(time.monotonic()-self.started_at)
            self.elapsed.config(text=f"{seconds//60:02d}:{seconds%60:02d}")
        self.poll_after=self.after(100,self.poll)

    def finished(self,event):
        self.set_busy(False)
        self.last_root=Path(event["root"]) if event.get("root") else None
        self.last_diagnostic=Path(event["diagnostic"]) if event.get("diagnostic") else None
        self.open_button.config(state="normal" if self.last_root else "disabled")
        self.diagnostic_button.config(state="normal" if self.last_diagnostic else "disabled")
        if event.get("success"):
            self.progress_value=100
            self.status.config(text="●  "+("Diagnostics complete" if event.get("diagnosis") else "Your patch is ready"),fg=MINT)
            self.detail.config(text="Diagnostics and logs are in the output folder." if event.get("diagnosis") else "Copy the atmosphere folder to your SD card.")
            self.percent.config(text="100 %")
            self.append_log("DONE: "+str(event.get("output","")),"notice")
        else:
            cancelled=event.get("cancelled")
            self.status.config(text="●  "+("Job cancelled" if cancelled else "Patch stopped"),fg=GOLD if cancelled else RED)
            message=str(event.get("error","Unknown error"))
            self.detail.config(text="See the live log and diagnostic report for details.")
            self.percent.config(text="Cancelled" if cancelled else "Error")
            self.append_log(message,"error")
            self.show_page("log")
        self.paint_progress()

    def open_path(self,path):
        if not path or not Path(path).exists():return
        try:
            if os.name=="nt":os.startfile(str(path))
            else:subprocess.Popen(["open" if sys.platform=="darwin" else "xdg-open",str(path)])
        except OSError as error:messagebox.showerror("Open folder",str(error),parent=self)

    def open_output(self):self.open_path(self.last_root)
    def open_diagnostic(self):self.open_path(self.last_diagnostic.parent if self.last_diagnostic else None)

    def export_log(self):
        path=filedialog.asksaveasfilename(parent=self,title="Save log",defaultextension=".log",initialfile="KH-Patcher-Log.log")
        if path:
            try:Path(path).write_text(self.log.get("1.0","end-1c"),encoding="utf-8")
            except OSError as error:messagebox.showerror("Save file",str(error),parent=self)

    def close(self):
        if self.busy:
            if not messagebox.askyesno("Cancel the running job?","The patcher is still working. Cancel the job and close the window?",parent=self,default="no"):return
            self.cancel()
            self.after(200,self.wait_close)
        elif self.tool_setup.running:
            self.tool_setup.cancel.set()
            self.tool_status.config(text="Stopping tool setup …",fg=GOLD)
            self.after(100,self.wait_close)
        else:self.destroy()

    def wait_close(self):
        self.tool_setup.cancel.set()
        if self.runner.running or self.tool_setup.running:self.after(200,self.wait_close)
        else:self.destroy()

    def destroy(self):
        self.tool_setup.cancel.set()
        if self.setup_after:
            try:self.after_cancel(self.setup_after)
            except tk.TclError:pass
        if getattr(self,"poll_after",None):
            try:self.after_cancel(self.poll_after)
            except tk.TclError:pass
        super().destroy()


def main():
    if os.name=="nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):pass
    PatcherApp().mainloop()


if __name__=="__main__":main()
