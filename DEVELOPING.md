# Developing this fork (Windows 11 + PyCharm)

This fork extends [PDF Arranger](https://github.com/pdfarranger/pdfarranger) with
Preview-style markup (text, shapes, interactive crop) and tools for digital
sheet music. See [ROADMAP.md](ROADMAP.md) for the feature plan.

## 1. One-time setup

### Prerequisites

| Tool | Notes |
|---|---|
| Git for Windows | Or PyCharm's bundled Git integration |
| PyCharm (Community or Pro) | 2024.1 or newer |
| MSYS2 | The setup script installs it with `winget` if it's missing |

**Why MSYS2?** PDF Arranger is a GTK 3 app. On Windows, GTK, Poppler, and the
Python bindings for both (`gi`, `cairo`) only exist as MSYS2 packages, and
pikepdf has to be built against the same toolchain. The python.org Python
**cannot** run this app. Everything runs on `C:\msys64\mingw64\bin\python.exe`
(this matches how upstream builds its Windows installer).

### Steps

1. **Fork** `pdfarranger/pdfarranger` on GitHub to your account.
2. **Clone** your fork to a short path with no spaces and outside OneDrive. Keep the
   folder name `pdfarranger` so the shared run configurations find their module:
   ```powershell
   mkdir C:\Development; cd C:\Development
   git clone https://github.com/<you>/pdfarranger.git
   cd pdfarranger
   git remote add upstream https://github.com/pdfarranger/pdfarranger.git
   ```
   (Or use PyCharm: *File → Project from Version Control*, directory `C:\Development\pdfarranger`.)
3. **Run the setup script** from a normal PowerShell window in the repo root:
   ```powershell
   powershell -ExecutionPolicy Bypass -File dev\windows\setup.ps1
   ```
   It installs the MSYS2 packages, creates `.venv`, builds the translations, and
   runs the unit tests (89 tests, all passing on a clean upstream checkout).
   It is safe to re-run. Use `-SkipUpdate` to skip `pacman -Syu` and `-SkipTests` to skip the tests.
4. **Open the folder in PyCharm** and set the interpreter:
   *Settings → Project → Python Interpreter → Add Interpreter → Add Local Interpreter →
   Select existing → Python →* `C:\Development\pdfarranger\.venv\bin\python.exe`
   (the script prints the exact path; on some MSYS2 versions it's `.venv\Scripts\python.exe`).
   If PyCharm prompts to install `packaging` tools, decline (see the pip warning below).
5. **Mark directories**: right-click `tests` → *Mark Directory as → Test Sources Root*;
   right-click `build` → *Mark Directory as → Excluded*.
6. Pick **PDF Arranger (sample PDF)** in the run-configuration dropdown and press ▶ or 🐞.

### Shared run configurations (`.idea/runConfigurations/`)

| Name | What it does |
|---|---|
| PDF Arranger | `python -m pdfarranger` |
| PDF Arranger (sample PDF) | Same, opening `tests/test.pdf` |
| PDF Arranger (GTK Inspector) | Same, with `GTK_DEBUG=interactive`. This opens GTK's live widget inspector, which is very useful for UI work (like browser devtools for GTK) |
| Build → Build (setup.py build) | Compiles translations into `build/mo` (re-run after editing `po/`) |
| Build → Build Windows installer (.msi) | `setup_win32.py bdist_msi` (run *setup.py build* first) |
| Tests → core / exporter / outlines | The cross-platform unit tests, using PyCharm's test runner |

If a configuration shows a red ✗, open *Edit Configurations…* and re-select the
project interpreter. That happens when the folder isn't named `pdfarranger`.

### How the venv works (and the one rule to remember)

`.venv` is created with `--system-site-packages`, so it *sees* everything pacman
installed into MSYS2, and it adds a small `msys2_gtk_dlls.pth` hook that puts
`C:\msys64\mingw64\bin` on the DLL search path for every process (app, tests,
debugger, multiprocessing export workers).

> ⚠️ **Never `pip install` a package with compiled code into this venv.** MSYS2's
> Python can't use PyPI's Windows wheels. New runtime dependencies come from
> pacman: `pacman -S mingw-w64-x86_64-python-<name>` in the *MSYS2 MINGW64*
> shell, then add them to `$Packages` in `dev/windows/setup.ps1` so the setup stays
> reproducible. Pure-Python dev tools (like `pygobject-stubs`, which the script
> installs for Gtk autocompletion) are fine.

## 2. Daily workflow

- **Branches:** `main` tracks your fork's releases. Do feature work on `feature/<name>` branches.
- **Sync with upstream** regularly, so bug fixes and translations keep flowing in:
  ```powershell
  git fetch upstream
  git checkout main
  git merge upstream/main
  git push
  ```
  Keep new features in **new modules** where possible (see the map below). The
  fewer lines changed in upstream files, the easier these merges stay.
- **CI:** GitHub Actions is disabled on new forks. Enable it under your fork's *Actions*
  tab. The existing workflow then runs the full test suite on Linux for every push,
  including the GUI tests, which can't run on Windows.
- **Tests:** add unit tests next to the existing ones in `tests/`. Model and export
  logic should be testable without opening a window.

## 3. Code map (upstream 1.14.0, ~8.5k lines)

| File | Role | Relevance to fork features |
|---|---|---|
| `pdfarranger/pdfarranger.py` (3.4k) | `PdfArranger` Gtk.Application: main window, actions/menus, open/save, clipboard, zoom | Register new actions and the page-editor entry point here |
| `pdfarranger/core.py` | `Page` / `LayerPage` models, `Sides`/`Dims` geometry, `PDFDoc` (Poppler + temp copies), `PDFRenderer` thumbnail thread | **Markup lives on `Page`**. Must survive `rotate()`, `split()`, `duplicate()`, `serialize()` |
| `pdfarranger/exporter.py` | Builds the output PDF with pikepdf: copy pages, apply rotate/scale/crop, add overlay/underlay layers | **Markup export hooks in after the geometry transforms.** There are two code paths (`_copy_n_transform` and the pikepdf ≥ 8 `_transform_job`), and both need it |
| `pdfarranger/pageutils.py` | Dialogs: scale, crop/hide, merge (N-up), blank page, paste-as-layer; `DrawingAreaWidget` (a single page drawn large with draggable rectangle handles) | `DrawingAreaWidget` is the natural seed for an interactive crop tool and the **page editor** canvas |
| `pdfarranger/iconview.py` | Thumbnail grid: cell renderer, cursor, rubber-band selection, panning | Draw markup on top of thumbnails in `CellRendererImage` |
| `pdfarranger/undo.py` | Undo/redo as snapshots of the page list (memento) | Markup gets undo for free **if** `Page.duplicate()` deep-copies it |
| `pdfarranger/metadata.py` | Document metadata editor | Sheet-music metadata (composer, instrument, catalog no.) |
| `pdfarranger/exporter_outlines.py` | Bookmarks/outlines on export | Part and movement bookmarks |
| `pdfarranger/splitter.py`, `search.py` | Split-page dialog, text search | |
| `data/pdfarranger.ui`, `data/menu.ui` | GtkBuilder UI and menus | Toolbar and menu entries |
| `tests/` | `test_core.py`, `test_exporter.py`, `test_exporter_outlines.py` (cross-platform); `test.py` (dogtail GUI, Linux only) | |

### Architectural facts to design around

- **Non-destructive model.** The app never edits PDFs in place. Each `Page` records
  *(source file, page #, angle, scale, crop, hide, layer pages)*; Poppler renders
  thumbnails from the original, and pikepdf applies everything at export.
  New features (markup, stamps, interactive crop) should follow the same pattern:
  store them on the model, preview them at render time, and apply them at export.
- **Crop is fractional.** `Sides(left, right, top, bottom)` holds fractions (0–1) of
  the page, not points. Store markup geometry in the same normalized,
  unrotated source-page space, so rotation and cropping don't need special cases.
- **Export runs in a separate process** (`multiprocessing`, spawn mode). Anything
  stored on `Page` must be picklable.
- **No single-page editing view exists yet.** Upstream is purely a thumbnail grid
  plus modal dialogs, so markup tools need a new page-editor view.
- **GTK 3** is in maintenance mode, but upstream is staying on it. Don't attempt a GTK 4 port;
  it would make upstream merges impossible.
- **License: GPL-3.0.** The fork stays GPL-3.0, keeps the copyright headers, and adds yours to new files.
  Before distributing, change the app ID (`com.github.jeromerobert.pdfarranger`)
  and the config directory. Otherwise the fork shares settings with, and conflicts with, a real
  PDF Arranger install.
