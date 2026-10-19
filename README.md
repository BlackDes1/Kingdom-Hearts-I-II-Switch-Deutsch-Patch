# Kingdom Hearts Switch Patcher

**German or Japanese voices for Kingdom Hearts I and II Final Mix, with optional German Steam movies for KH2.**

This patcher adapts selected PC voice and movie files to the extracted archives from your Switch game and creates a verified `atmosphere` folder with the correct Program ID.

**Desktop patcher 1.2**

## Supported content

| Game | Voices | Movies | Program ID |
|---|---|---|---|
| Kingdom Hearts I Final Mix | German or Japanese | Not included | `010090C022978001` |
| Kingdom Hearts II Final Mix | German or Japanese | German Steam movies, selected separately | `010090C022978003` |

KH1 and KH2 movie files are independent of the voice selection. Japanese voice mods do not replace movie files. For KH2, you can combine Japanese voices with **German movies (STEAM)**. The patcher leaves your original files unchanged.

## Download the German and Japanese patches

Download mods from their original pages. **Game files, voices, and movies are not included in this patcher.** Nexus Mods may require an account to download files.

### Kingdom Hearts I Final Mix

- **German:** [Rikux3 – kh1fm-undub-ger-pc](https://github.com/Rikux3/kh1fm-undub-ger-pc). Use the [source snapshot verified for this patcher](https://github.com/Rikux3/kh1fm-undub-ger-pc/archive/e17e035f10fe6d61954051fb21371c0452aefdb4.zip) or the matching extracted mod folder.
- **Japanese:** [TieuLink – Japanese voices](https://www.nexusmods.com/kingdomheartsfinalmix/mods/6?tab=files). In the **Files** tab, download **Japanese voices** (version 1.0). The patcher accepts the `.kh1pcpatch` file, a ZIP containing that patch file, or an extracted folder.

### Kingdom Hearts II Final Mix

All three files are available from the **Files** tab on the relevant Nexus page:

| Language / content | Download | Version | Mod page |
|---|---|---:|---|
| German voices | **German voices** | 2.1, Steam support | [Nexus Mods](https://www.nexusmods.com/kingdomhearts2finalmix/mods/42?tab=files) |
| Japanese voices | **Japanese voices** | 2.1 | [Nexus Mods](https://www.nexusmods.com/kingdomhearts2finalmix/mods/16?tab=files) |
| German Steam movies | **German movies (STEAM)** | 1.0 | [Nexus Mods](https://www.nexusmods.com/kingdomhearts2finalmix/mods/42?tab=files) |

Voice files use the `.kh2pcpatch` format. The patcher can read the file directly, a ZIP containing one `.kh2pcpatch`, or an extracted mod folder. Select **Voices + Movies** to build both, or **Voices** to build only the voice patch. Movie patching requires the separate **German movies (STEAM)** file.

## Requirements

- 64-bit Windows 10 or 11.
- [Python 3.10 or newer](https://www.python.org/downloads/windows/) with Tkinter. Tkinter is normally included in the official Windows installer.
- The extracted ROMFS for the relevant Switch game, from your own copy.
- The German or Japanese mod you want to use.
- A writable patcher folder and enough free disk space for the new mod output.
- An internet connection for the first download of the KH2 media tools.
- A Switch setup that can load Atmosphère LayeredFS mods.

No additional Python packages or `pip` installation are required.

## Start the patcher and install FFmpeg automatically

1. Extract the entire patcher package to a writable folder.
2. Double-click **START.pyw**. You can also run **START.cmd**.
3. On startup, the app checks for `bin/ffmpeg.exe` and `bin/ffprobe.exe`.
4. If either tool is missing or the installed pair is not usable, the patcher downloads **FFmpeg 9.0.2 Essentials from Gyan.dev**.
5. After checking the SHA-256 checksum and required codecs, it installs `ffmpeg.exe` and `ffprobe.exe` in the shared **bin folder next to START.pyw**.

The first download is about 110 MB. The GUI shows download progress and size. Valid tools in `bin` are reused on later launches. If a download fails, select **Retry download**; the live log shows the error details. Alternatively, choose a custom FFmpeg folder under **Tools**. The build needs the `libvorbis` decoder and the `libvorbis` and `libx264` encoders. KH1 does not require FFmpeg.

Download source: [Gyan.dev – FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/), linked from the [official FFmpeg download page](https://ffmpeg.org/download.html). The build package's license and README are kept beside the tools. The archive and temporary installation files are removed after setup.

## Build a patch

1. Select **Kingdom Hearts I** or **Kingdom Hearts II**.
2. Under **Voices**, select **German** or **Japanese**. Use **Open mod page** to visit the matching original download page.
3. For KH2, choose **Voices + Movies**, **Voices**, or **Movies**.
4. Select the extracted **ROMFS** for that game. Use the same game version you plan to play; the folder must contain the required `.hed` and `.pkg` archives.
5. Select the matching voice mod and, for KH2, the movie package if needed.
6. Choose an output folder outside ROMFS and the mod folders, then click **Build patch**.
7. Copy the completed `atmosphere` folder from the new job folder to your SD card. The mod files go here:

   - KH1: `atmosphere/contents/010090C022978001/romfs/`
   - KH2: `atmosphere/contents/010090C022978003/romfs/`

Each run gets its own output folder. **Voices + Movies** creates one combined KH2 mod folder. Progress, elapsed time, and messages appear in the app. Selected paths and voice language are saved separately for each game. Use **Cancel** to stop a running job.

## Diagnostics and verification

Use **Diagnostics** to run an inspection job. A voice mod is not required for a KH1-only diagnostic. Reports, logs, and, when applicable, small archive samples are saved in the job folder. Full game archives, voices, and movies are never copied into diagnostic ZIPs.

The patcher checks the expected file names in the Switch ROMFS, the size and SHA-256 hash of imported voice files, and unchanged archive resources. If files are missing or names do not match, the build stops and saves a diagnostic report. A successful archive check does not replace a test on the Switch.

Run the tests from the project folder:

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m unittest discover -s engines/kh1/tests -v
py -3 -m unittest discover -s engines/kh2/tests -v
```

Japanese imports were checked with synthetic test archives; the real Nexus mod archives are not included in this package. Windows and Switch hardware testing must be done with your own files. See [validation.json](validation.json) for the detailed validation record.

## Credits and licenses

- **Rikux3**: KH1 mod and German voices.
- **TieuLink**: Japanese voice mods for KH1 and KH2, plus the KH2 German voice and movie patches.
- **RubyX_Coded** and **Fotia**: contributions to KH2 battle voices.
- **FFmpeg project and Gyan.dev**: media tools and Windows builds.

The patcher code is licensed under **Apache-2.0**; see [LICENSE](LICENSE). The automatically downloaded FFmpeg build is separately licensed under **GPLv3 or later**. Rights to the games and external mod files remain with their respective owners. See [NOTICE](NOTICE) and the engine folders for additional notes.
