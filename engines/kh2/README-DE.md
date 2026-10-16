# KH2 Final Mix – Deutsche Stimmen und Filme für Switch, v9

Version 9 wählt für Vorbis-Audio ausdrücklich den **libvorbis-Decoder**.
Die letzte v8-Diagnose zeigt einen FFmpeg-Prozessabsturz beim Dekodieren von
Berserkers Soundplatz 5; die identische Sprachbank der zweiten Berserker-Variante
bestand denselben Durchlauf. Die genaue Ursache im Windows-Prozess ist noch offen.
Bei einem erkannten Prozessabsturz startet das Werkzeug den gesamten betroffenen
Audioschritt einmal in einem neuen Prozess mit FFmpegs CPU-Basispfad neu.
Normale Medienfehler, Zeitüberschreitungen und ein zweiter Absturz brechen weiterhin ab.
Jeder Absturz wird protokolliert, auch wenn die Wiederholung erfolgreich ist.

PCM-Zwischenschritt, 1.024-Byte-MS-ADPCM-Blöcke, WAVE-Normalisierung, Scars
Kanalanpassung und die gesammelte Stimmenvorprüfung sind weiter enthalten.
Das Werkzeug kombiniert **German voices 2.1** von **TieuLink** mit **German movies (STEAM)**
für die native Switch-Fassung von KH2 Final Mix.

**Neu entpacken, START.cmd aus dem v9-Ordner starten und Auswahl 5 verwenden.**
Beim Start muss **v9** angezeigt werden. Nach erfolgreichen Prüfungen
entsteht ein gemeinsamer `atmosphere`-Ordner mit Stimmen und Filmen.
Die Spielarchive, Sprachaufnahmen und Filme sind nicht im Werkzeug enthalten.

## Vorbereitung

Von [derselben Nexus-Modseite](https://www.nexusmods.com/kingdomhearts2finalmix/mods/42?tab=files)
beide Dateien herunterladen:

| Nexus-Datei | Verwendung |
|---|---|
| **German voices 2.1**, ca. 673,8 MB | Hauptpatch mit Original-SCDs, Kampfstimmen und Titelgrafiken |
| **German movies (STEAM) 1.0**, ca. 1,5 GB | Zwölf deutsche Filmdateien |

EPIC-Filmpakete werden ebenfalls erkannt. Die optionalen **Ps2 Untertitel**
gehören nicht zu diesem Stimmen-/Filmimport.

Benötigt werden außerdem **Python 3.10 oder neuer**, dein vollständiger
**KH2-ROMFS / Program 3** mit `kh2_*.hed`/`.pkg` und Originalfilmen unter `Switch`
sowie **FFmpeg und ffprobe** für den Filmteil. FFmpeg muss für eine eventuelle
Bildumwandlung den Encoder `libx264` enthalten. Für die Stimmen werden der
**libvorbis-Decoder und -Encoder** benötigt. Der Decoder wird vor seinem ersten
Einsatz abgefragt; ein fehlender Decoder stoppt den Bau mit einem Hinweis.

FFmpeg ist über [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
erhältlich. Unter Windows die Windows-Builds öffnen, ein vollständiges,
vorzugsweise statisches Paket entpacken und **ffmpeg.exe** sowie **ffprobe.exe**
in den Unterordner **bin** dieses Werkzeugs legen. Dynamische Builds benötigen
auch ihre DLL-Dateien. Alternativ beide Programme über `PATH` bereitstellen.

## Stimmen und Filme gemeinsam bauen

1. Dieses ZIP vollständig in einen beschreibbaren Ordner entpacken.
2. **START.cmd** starten und **5** wählen oder die Eingabetaste drücken.
3. Deinen **KH2-ROMFS-Hauptordner / Program 3** auswählen. Nicht nur dessen
   Unterordner `Switch` und nicht den KH1-ROMFS von Program 1 auswählen.
4. Die **German voices 2.1**-ZIP auswählen. Die darin enthaltene `.kh2pcpatch`
   wird automatisch geöffnet; einzeln entpackt ist sie ebenfalls nutzbar.
5. Die ZIP **German movies (STEAM)** auswählen.
6. KH2-Program-ID bestätigen, optional die Spielversion eintragen und einen
   Ausgabeort außerhalb der Eingabeordner wählen.
7. Den vollständigen Bau und alle anschließenden Prüfungen abwarten.
8. Den einen erzeugten **atmosphere**-Ordner auf die SD-Karte kopieren.

Im neuen Ordner `KH2-Switch-Komplett-...` entsteht bei Erfolg:

`atmosphere/contents/010090C022978003/romfs/`

Dort liegen die geänderten HED/PKG-Paare und die Filme an ihren tatsächlich
gefundenen Switch-Pfaden. Die Sammlungs-ID `010090C022978000` wird auf die
KH2-Program-ID `010090C022978003` umgesetzt. Eine abweichende tatsächliche
Program-ID kann eingegeben werden.

Der gemeinsame Ordner wird erst bereitgestellt, wenn beide Teile erfolgreich
geprüft sind. Eingaben und vorhandene Ausgabeordner werden nicht überschrieben.
Temporäre Dateien werden aufgeräumt. Zum Zusammenführen werden geprüfte Dateien
bewegt; Filme werden nicht doppelt umgewandelt. Der freie Platz wird vorher geprüft.

## Korrekturen anhand deiner Diagnosen

Dein Hauptpatch enthält **6.380 Original-SCDs** und **325 remastered-Dateien**.
Die früheren Versionen konnten diesen Original-SCD-Aufbau nicht bauen.
Das Werkzeug unterstützt:

- Original-SCD-Ersatz mit neuen Archivköpfen, korrekter Verschlüsselung,
  Kompression, Ausrichtung und HED-Längen. Eingebaute Dateien werden erneut
  dekodiert und per SHA-256 verglichen.
- Native Archivzuordnung über die HED-Hashes aller gefundenen KH2-Pakete.
  Bei mehrfachen PC-Varianten hat die zum nativen Archiv passende Variante
  Vorrang; die Wahl wird im Bericht dokumentiert.
- Neue Original-SCD-Einträge, wenn der PC-Patch solche Dateien ergänzt.
  Bestehende HED-Zeilen behalten ihre Reihenfolge.
- Verschachtelte HD-Namen wie `voice/battle/us/sora`, ohne sie als Teil des
  übergeordneten BAR-Dateinamens zu behandeln.
- PNG-Titelgrafiken für vorhandene native Bildplätze. Zusätzliche PC-Titelbilder
  mit Index 9–11 haben in deinen nativen Titeldaten keine Slots und werden
  ausdrücklich als PC-Ressourcen ohne Switch-Ziel ausgewiesen.
- Bedingte Roxas-Pfadkorrektur von `B_EX930.a.us` auf `B_EX390.a.us`, nur wenn
  das native Ziel exakt die erwartete Stimme `voice/battle/us/n_roxas` enthält.
- Die bestätigten Stitch-Namen `voice/battle/us/stitch` → `voice/battle/us/stusch`
  in den fünf Objekten `N_HB040_BTL.a.fr/gr/it/sp/us`.
- Die doppelten `tt_walla1`-/`tt_walla2`-Quellen auf die tatsächlich vorhandenen
  `voice/battle/jp/`-Slots. Die dort eingesetzten Daten stammen aus dem deutschen
  Mod; das Kürzel `jp` gehört zum nativen Ressourcennamen.
- Die falsch abgelegte Minnie-Kopie aus `N_CM000_BTL.a.us` wird mit dem passenden
  Eintrag in `N_DC010_BTL.a.us` zusammengeführt. Marluxias Stimme bleibt erhalten.
  Bei widersprüchlichen Kopien stoppt der Bau mit einer konkreten Fehlermeldung.

SCD-Versionen **3 und 4** mit **Ogg Vorbis oder MS-ADPCM** werden strukturell
geprüft. Stimmen mit bereits passendem Codec und passender Kanalzahl bleiben
erhalten. Bei abweichendem Codec oder Kanalaufbau wandelt FFmpeg nur die
betroffenen Soundplätze in das native Format um und prüft die vollständige
Dekodierung. Die Cue-/Infotabellen und die
deutschen Aufnahmen bleiben die Grundlage; die Umwandlung wird protokolliert.
Bei deinen gelieferten Proben betrifft dies 29 Tifa-Spuren, 26 Spuren in
`tt_walla1` und Sephiroths Bank sowie Scars `lk107_skar`-Spur. Eine Neukodierung
kann die Klangqualität verändern. Standard-MS-ADPCM kann den letzten Block
mit kurzer Stille auffüllen; die Sprachdaten werden vollständig kodiert.

Bei Mono → Quad wird die deutsche Stimme mit gleichem Pegel auf vorne links
und rechts verteilt; die hinteren Kanäle bleiben stumm. Das Ergebnis enthält
die geforderten vier Vorbis-Kanäle. Vorhandene Cue-/Infotabellen bleiben
erhalten. MS-ADPCM als Umwandlungsziel ist auf Mono und Stereo beschränkt.

Gültige Abtastraten wie 20.000, 22.050 oder 48.024 Hz werden erhalten. Fehlende
native Soundplätze, unbekannte Codecs oder Codec-/Kanalwechsel bei
geloopten Spuren erzeugen eine Diagnose. Andere native Ressourcen werden beim
Archivvergleich auf unveränderten Inhalt geprüft. FFmpeg wird daher auch für
den reinen Stimmenbau benötigt, sobald ein Codec- oder Kanalwechsel erforderlich ist.

Unterstützt sind diese Original-SCDs und vorhandene HD-SCDs/PNGs. Andere
Originaldateiarten, RAW-Änderungen oder notwendiger PC-Code benötigen weiterhin
eine gesonderte Anpassung.

## Filme

Steam-Filme werden unter `STEAM/juefigs/KH2ReSource/zmovie/en/` erkannt, auch
wenn die ZIP umbenannt wurde. Alle zwölf Filme werden benötigt:
`ena`, `hca`, `hcb`, `lop`, `me2`, `me3`, `me4`, `me6`, `mea`, `opn`, `sec`, `sec2`.

Kompatible deutsche MP4s werden unverändert übernommen. Unterstützte Abweichungen
werden anhand der nativen Vorlage angepasst: H.264/YUV420P mit Baseline-, Main-
oder High-Profil und gegebenenfalls AAC-LC mit ein oder zwei Kanälen. Passende
Spuren werden ohne Neukodierung kopiert. Jede fertige MP4 wird vollständig dekodiert.

Bei einer Abweichung zwischen durchschnittlicher und nomineller Bildrate von
höchstens rund zwei Bildern über die Laufzeit wird die nominelle Rate verwendet.
Nach deinen gelieferten Filmmetadaten brauchen dadurch elf Filme keine
Neukodierung; `ena.mp4` benötigt die Anpassung an das native Main-Profil und
die Bildrate. Die nativen B-Frame-Vorgaben werden bei der Umwandlung berücksichtigt.

Größere Laufzeitunterschiede, ungewöhnliche Startzeitversätze oder abweichende
Seitenverhältnisse werden gemeldet. Es wird nicht automatisch geschnitten oder
eine unbekannte Synchronisation geschätzt. Neukodierung kann Qualität verändern.

## Berichte und Fehler

Im gemeinsamen Modus bei Fehlern **KH2-Gesamt-Diagnose.zip** aus dem Ausgabeordner
im Chat hochladen. Darin liegen die bis zum Fehler entstandenen Stimmen- und
Film-Diagnosen. Kleine Stimmen-/Bildproben können enthalten sein; vollständige
Filme oder PKGs werden nicht aufgenommen.

`build-result.json` fasst beide Teile zusammen. `voice-result.json` dokumentiert
Archivbindungen, gewählte PC-Varianten, neue SCDs und ausgelassene PC-Titelbilder.
`movie-result.json` enthält Formate, Umwandlungen und Hashes. Bei einem
Stimmenfehler wird die betroffene Ressource für Diagnoseproben priorisiert.
Zuordnungsfehler nennen die betroffenen Quelldateien auch direkt im Fehlerlog.
Die Stimmenvorprüfung setzt die Prüfung nach dateibezogenen Formatfehlern fort
und sammelt alle betroffenen Pfade in `validation_errors`. Solange Fehler
vorliegen, beginnt kein Archivbau. Fehlendes FFmpeg oder Ein-/Ausgabefehler
stoppen den Lauf sofort. Erfolgreich geprüfte SCDs bleiben im Fehlerbericht
nachvollziehbar; kleine Proben mehrerer fehlgeschlagener Ressourcen werden
innerhalb des Diagnosebudgets priorisiert.
FFmpeg-Fehler erhalten zusätzliche JSON-Protokolle unter `logs/voices/`:
Version/Build, Quelldatei, Soundplatz, Verarbeitungsschritt, Kommando,
Rückgabecode und Fehlermeldung, Versuchszähler und gegebenenfalls erfolgreiche
Wiederholung (`recovered`). Diese Protokolle sind in der Gesamt-Diagnose
enthalten; temporäre PCM-Dateien werden aufgeräumt. Erfolgsberichte enthalten
`audio_vorbis_decoder` und `audio_process_crashes_recovered`.
Das Fenster bleibt offen; zusätzlich gibt es `Fehler-....log` neben dem Werkzeug.

| Auswahl | Aktion |
|---|---|
| 1 | Stimmen-Diagnose |
| 2 | Nur Stimmen und zugehörige Ressourcen bauen |
| 3 | Nur Filme bauen |
| 4 | Film-Diagnose ohne Modbau |
| **5** | **Stimmen und Filme gemeinsam bauen – Standard** |

Eine erfolgreiche reine Film-Diagnose ist noch kein Film-Bau. Dafür Auswahl
3 oder 5 verwenden.

## Ohne Dialoge

```powershell
py -3 kh2_switch.py --build-all --romfs "D:\KH2\romfs" --mod "D:\Downloads\German voices-42-2-1-1718316309.zip" --movies "D:\Downloads\German movies (STEAM)-42-1-0-1718319693.zip" --output "D:\KH2-Komplett-neu"
```

`--mod` und `--movies` akzeptieren auch entpackte Modordner mit erhaltener
Struktur. `--ffmpeg`, `--ffprobe`, `--title-id` und `--version` sind optional.
Einzelmodi: `--diagnose`, `--build`, `--build-movies`, `--diagnose-movies`.

## Prüfstand

**81 automatisierte Tests** prüfen Archivbau, neue Original-SCDs, die acht
HD-Zuordnungsfehler, SCD 3/4, Codec-/Kanalwechsel, deutsche Testtöne, PNGs, Fehlerfälle
und den gemeinsamen Steam-Bau. Darunter sind der gemeldete Windows-Absturzcode,
die einmalige Wiederholung mit anschließend echtem Archivbau und vollständiger
Dekodierung sowie die Kanalreihenfolge für ein bis acht Kanäle im Vergleich
mit einem unabhängigen Decoder. Die Medien dieser Tests werden selbst erzeugt.

Die Zuordnung wurde außerdem mit **allen 6.705 Quelldateieinträgen**, den sechs
echten HEDs und **6.353 tatsächlichen Archivköpfen** wiederholt. Die acht Fehler
von v5 wurden reproduziert; v9 hat bei dieser Metadatenprüfung keine
Zuordnungsfehler. Es verbleiben 6.526 ausgewählte Ziele: 6.225 Original-SCDs,
195 HD-Stimmen und 106 PNGs. 18 PC-Titelbilder besitzen keine nativen Slots;
161 doppelte Quellen werden zusammengeführt.

**19 echte Audiopaare und zwei PNG-Paare** aus deinen Diagnosen wurden
neu gebaut und per Hash-/Ressourcenvergleich geprüft. Dafür wurden 13
gelieferte native RAW-Assets verwendet; sieben weitere Assets wurden aus den
gelieferten Original-/HD-Daten und Testplatzhaltern für nicht mitgelieferte
sonstige Ressourcen rekonstruiert. **257 native, 258 deutsche und 123 umgewandelte
Audiostreams** wurden vollständig mit FFmpeg dekodiert.

Alle 23 Berserker-Spuren, die beiden Leon-Banken mit jeweils 22 Spuren und alle
29 Tifa-Spuren bestehen die neue Umwandlung. Der tatsächliche Windows-Absturz
ließ sich hier unter Linux/FFmpeg 6.1.1 nicht reproduzieren; seine Behandlung
wurde mit dem gemeldeten Fehlercode simuliert und mit echtem FFmpeg fortgesetzt.
Der neue Pfad ist ein lokal getesteter Workaround; dessen Windows-Test steht aus.

Die vollständigen KH2-PKGs, sämtliche Modnutzdaten und ganzen Filme wurden hier
nicht bereitgestellt. Der komplette Bau mit deinem ROMFS erfolgt lokal.
Ein Test auf der Switch steht aus. Details: `validation.json`,
`validation-real-samples.json` und `validation-mapping.json`.

```powershell
py -3 -m unittest discover -s tests -v
```

Ohne FFmpeg/ffprobe über PATH werden entsprechende Testklassen übersprungen.

## Quellen und Lizenz

- [Gewünschter Mod von TieuLink](https://www.nexusmods.com/kingdomhearts2finalmix/mods/42)
- [KHPCPatchManager / OpenKH: Archivverarbeitung](https://github.com/AntonioDePau/KHPCPatchManager)
- [OpenKh: SCD-Format](https://openkh.dev/kh2/file/type/scd.html)
- [vgmstream: SCD-Formatreferenz](https://github.com/vgmstream/vgmstream/blob/master/src/meta/sqex_scd.c)
- [OpenKh: Objektbezeichnungen](https://openkh.dev/kh2/dictionary/obj.html)
- [Dokumentierte Switch-Program-Zuordnung](https://www.nexusmods.com/kingdomheartsfinalmix/mods/123)
- [FFmpeg](https://ffmpeg.org/ffmpeg.html) und [ffprobe](https://ffmpeg.org/ffprobe.html)
- [FFmpegs libvorbis-Decoder](https://ffmpeg.org/doxygen/trunk/libvorbisdec_8c_source.html)
  und [Vorbis-Kanalreihenfolge](https://xiph.org/vorbis/doc/Vorbis_I_spec.html)

Programmcode: Apache-2.0; siehe LICENSE und NOTICE. Keine Spielarchive,
Sprachaufnahmen, Filme oder FFmpeg-Binärdateien werden mitgeliefert.
