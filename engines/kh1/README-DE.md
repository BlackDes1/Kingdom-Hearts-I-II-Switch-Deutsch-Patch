# Kingdom Hearts I: Deutsche Stimmen auf Switch – v2

Dieses Paket baut den deutschen Stimmen-Mod von **Rikux3** in die Archive der
**nativen Switch-1-Version von KINGDOM HEARTS -HD 1.5+2.5 ReMIX-** ein.
Die bereitgestellte Switch-Diagnose wurde ausgewertet. Der Umbau ist mit sechs
echten Archivproben und 27 SCD-Dateien geprüft; der vollständige Build auf deinem
PC und der anschließende Test im Spiel stehen noch aus.

## Jetzt den Mod bauen

1. Dieses ZIP vollständig in einen neuen, beschreibbaren Ordner entpacken.
2. Den Rikux3-Mod als ZIP herunterladen:
   [geprüfter Quellenstand](https://github.com/Rikux3/kh1fm-undub-ger-pc/archive/e17e035f10fe6d61954051fb21371c0452aefdb4.zip).
   **Das Mod-ZIP bei der Dateiauswahl auswählen, nicht die Diagnose-ZIP.**
3. **START.cmd** doppelklicken und **2 – Testmod bauen** wählen.
4. Denselben KH1-ROMFS-Ordner wie bei der Diagnose auswählen.
5. Die Program-ID und gegebenenfalls die Spielversion angeben. Die bekannte
   Sammlungs-ID `010090C022978000` wird automatisch auf **KH1 / Program 1**,
   also `010090C022978001`, umgesetzt.
6. Das heruntergeladene Mod-ZIP und einen Ausgabeordner auswählen.
7. Nach erfolgreicher Prüfung den erzeugten `atmosphere`-Ordner auf die SD-Karte
   kopieren. Der KH1-Mod liegt in:
   `atmosphere/contents/010090C022978001/romfs/`.
8. KH1 starten und frühe Dialoge, Zwischensequenzen und Kampfstimmen testen.

Python 3.10 oder neuer wird benötigt. Der Diagnose-Lauf von v1 hat Python auf
deinem Rechner bereits erfolgreich verwendet. Für den Bau nutzt v2 den
Python-Archivschreiber im Paket.

## Was deine Diagnose gezeigt hat

| Archiv | Benötigte Ressourcen im Index vorhanden | Deutsche Audiodateien |
|---|---:|---:|
| `kh1_first` | 35 / 35 | 268 |
| `kh1_third` | 47 / 47 | 200 |
| `kh1_fourth` | 288 / 288 | 1.582 |
| `kh1_fifth` | 22 / 22 | 276 |
| **Gesamt** | **392 / 392** | **2.326** |

Die Endpositionen der vier HED-Indizes entsprechen genau den Dateigrößen der
zugehörigen PKGs. Die Diagnose enthält sechs vollständige Asset-Proben aus
`kh1_first`, `kh1_third` und `kh1_fourth`. Darin wurden **27 native SCD-Dateien**
mit insgesamt **93 Audiostreams** gelesen und mit den deutschen Gegenstücken
verglichen. Kanalzahlen und Codec-Kennungen stimmen überein.

Diese sechs Proben wurden mit den deutschen Stimmen neu aufgebaut. Alle 27
Audiodateien wurden anschließend aus den erzeugten Archiven gelesen und ihre
SHA256-Prüfsummen mit dem Originalmod verglichen. Die ursprünglichen Daten und
Referenzen der Spielressourcen blieben erhalten.

`kh1_fifth` enthält einen leeren HED-Eintrag mit dem Platzhalter `0xFDFDFDFD`
im Feld für die Originallänge. Die Prüfung von v1 hat diesen Eintrag fälschlich
abgelehnt. **v2 erhält diesen Eintrag vollständig** und passt seine Position
beim Neuaufbau an die zugehörige Archivposition an.

Die HED-Übereinstimmung bestätigt die 392 Ressourcennamen. Die vollständige
Prüfung aller 2.326 Audiodateinamen und der gesamten PKG-Inhalte erfolgt beim
Build mit deinem ROMFS. Die Diagnose allein enthält nicht alle Spieldateien.

## Ablauf und Prüfungen

- Es werden nur die Audiodateien des geprüften Quellenstands verwendet.
  Jede der 2.326 Dateien wird anhand ihres SHA256 geprüft.
- Der Archivschreiber ersetzt die passenden SCD-Payloads in deinen Switch-PKGs.
  Die ursprünglichen Kompressionsmodi, Audio-Namen, Referenzen und die
  Reihenfolge werden beibehalten. Die logischen Offsets werden für die neuen
  Dateigrößen aktualisiert.
- Andere PKG-Ressourcen werden bytegleich kopiert. Leere HED-Zeilen und die
  Originalreihenfolge der Indizes bleiben erhalten.
- Nach dem Schreiben werden die vier Archivpaare erneut geprüft: Alle
  eingebauten Stimmen werden gelesen und ihre Prüfsummen verglichen; andere
  Ressourcen werden byteweise gegen den Dump geprüft.
- Deine ursprünglichen Dump-Dateien bleiben erhalten. Der fertige
  `atmosphere`-Ordner wird erst nach der Prüfung aller vier Archivpaare erstellt.
- Das Ergebnis enthält `build-result.json` und vier kleine Archivprotokolle
  unter `logs`. Der Bericht nennt die eingegebene ID und die tatsächliche
  KH1-Program-ID und kennzeichnet den noch ausstehenden Hardwaretest.

## Benötigte Dateien und Speicherplatz

Der ROMFS-Ordner muss diese vier Archivpaare enthalten:

```text
kh1_first.hed    kh1_first.pkg
kh1_third.hed    kh1_third.pkg
kh1_fourth.hed   kh1_fourth.pkg
kh1_fifth.hed    kh1_fifth.pkg
```

Die übrigen Dateien deiner Sammlung, darunter `kh1_second` und die Videos,
werden für den Stimmen-Build nicht verändert. Die vier benötigten PKGs deines
Dumps umfassen zusammen ca. 3,16 GiB. Das Programm prüft vor dem Start den
freien Speicher; für den neuen Build und die vorübergehend entpackten
Mod-Dateien sollten mindestens etwa 4 GiB frei sein. Die Original-PKGs werden
nicht zusätzlich in einen Arbeitsordner kopiert.

In DBI erreichst du KH1 über **Installed games → KH 1.5+2.5 → Open files on MTP →
Program 1**. Verwende den ROMFS-Stand, mit dem du tatsächlich spielen wirst.

## Umfang

**Intro und Abspann bleiben auf Englisch**, wie im verlinkten Originalmod.
Das Paket enthält selbst keine Spielarchive oder Sprachaufnahmen. Es baut den
Mod aus deinem eigenen Dump und dem heruntergeladenen Rikux3-Mod.

## Fehler und Diagnose

Das Startfenster bleibt offen. Bei einem Fehler entsteht eine Datei
`Fehler-....log` neben dem Programm. Angefangene Arbeitsdaten bleiben zur
Fehlersuche im neuen Ausgabeordner erhalten; es wird kein erfolgreich
gekennzeichneter Mod ausgegeben.

Bei Bedarf erstellt **Auswahl 1** eine neue kleine Diagnose mit den HED-Indizes,
der Dateiliste und höchstens acht PKG-Proben von jeweils maximal 2 MiB. Der
Fehler mit dem leeren Eintrag in `kh1_fifth` ist in v2 korrigiert.

## Aufruf ohne Dialoge

```powershell
py -3 kh1_switch.py --build --romfs "D:\KH1\romfs" --mod "D:\Downloads\kh1fm-undub-ger-pc-main.zip" --output "D:\KH1-German-Switch-v2" --title-id 010090C022978001
py -3 kh1_switch.py --diagnose --romfs "D:\KH1\romfs" --output "D:\KH1-Diagnose-v2.zip" --title-id 010090C022978001
```

Für `--mod` ist auch ein entpackter Modordner möglich. Vorhandene Ausgabeordner
oder Diagnose-ZIPs werden nicht überschrieben.

## Prüfstand

- Alle 2.326 deutschen Quelldateien sind geprüft.
- Vier echte Switch-Indizes und alle 392 benötigten Asset-Hashes sind geprüft.
- Sechs echte Switch-Asset-Proben sind mit 27 deutschen SCDs umgebaut und
  anschließend inhaltlich geprüft; 93 Audiostreams haben passende Kanal-/Codec-Metadaten.
- Die beigefügten Regressionstests prüfen unter anderem den leeren nativen
  Index-Eintrag, falsche Audionamen, beschädigte Modquellen, komprimierte
  Audiodaten, die Program-ID und das Beibehalten anderer Ressourcen.
- **Der vollständige Build mit allen vier PKGs und ein Test auf der Switch
  stehen noch aus.**

```powershell
py -3 -m unittest discover -s tests -v
```

Details zu den echten Proben stehen in `validation-native.json`.

## Quellen

- [Rikux3: deutscher Stimmen-Mod](https://github.com/Rikux3/kh1fm-undub-ger-pc)
- [AntonioDePau: dokumentierte Archivverarbeitung](https://github.com/AntonioDePau/KHPCPatchManager)
- [Fotia13: französischer Mod mit Switch-Anleitung und KH1-Program-ID](https://www.nexusmods.com/kingdomheartsfinalmix/mods/123)

Die beigefügte Apache-2.0-Lizenz gilt für den Programmcode, nicht für externe
Spiel- oder Audiodateien. Siehe `NOTICE`.
