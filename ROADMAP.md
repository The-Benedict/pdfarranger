# Roadmap (draft v0.3, 2026-11-23)

Goal: match macOS Preview's page manipulation and markup, then go beyond it with
tools built for managing digital sheet music. Built on PDF Arranger 1.14.0 (GPL-3.0).

Status key: ☐ not started · ◐ in progress · ☑ done · ❓ needs a decision

## Decisions so far
- **Base:** PDF Arranger, after weighing the alternatives (see the end of this file).
- **Output is flattened.** Stamps and markup are drawn into the page content. Other
  apps (forScore etc.) don't need to edit or undo them; they add their own layers anyway.
  This removes the hardest part of the original plan (editable PDF annotations with appearance streams).
- **Top priorities:** (1) part extractor, (2) merge selected PDFs with bookmarks,
  (3) music symbol stamps, (4) rehearsal marks and measure numbers, (5) customizable
  batch stamping (size, location, rotation, font, color, opacity…).
- **Keep the Python package named `pdfarranger`.** Rebrand only the display name, app ID and
  config directory at release time, so upstream merges stay easy.

---

## Phase 0: Workspace ☑
- ☑ Windows/MSYS2 (UCRT64) setup script, PyCharm run configs, dev guide, CLAUDE.md
- ☑ Baseline: 89 unit tests green on upstream 1.14.0
- ☑ Fork created, kit pushed, GitHub Actions enabled on the fork
- ☑ App runs from PyCharm on Ben's desktop

## Next up: Page view ◐ (design: `docs/design/page-view.md`)
- ☐ Single-page view with a thumbnail sidebar (Preview/Okular style), as a mode of the main window
- ☐ Shared page-compositing function (`pagerender.draw_page`) used by thumbnails and the page view
- ☐ This view becomes the canvas for the page editor (Phase 2) and the markup tools (Phase 3)

## Phase 1: Stamp engine (foundation for priorities 3–5 and later markup)
- ☐ `Stamp` model on `Page`: content (text, SMuFL music symbol, image, or simple shape),
  font, size, color, opacity, rotation, and position given as an anchor
  (corner/edge/center) plus an offset, so one stamp definition lands
  correctly on pages of different sizes
- ☐ Rendering: cairo draws each page's stamps into a small PDF (fonts embedded), which is then
  overlaid with pikepdf. This reuses the mechanism upstream already uses for overlay layers
- ☐ Live preview of stamps on thumbnails
- ☐ Survives rotate, crop, split, duplicate, copy/paste, undo/redo, and the multiprocessing export
- ☐ Unit tests: geometry, plus export round-trips (render the result and check the stamp's position)

## Phase 2: Priority features
- ☐ **Batch stamping dialog**: apply a stamp to selected pages / every page / odd /
  even / a page range; saved presets ("RCB library stamp", "Part header", "Page n of m");
  tokens such as `{page}`, `{pages}`, `{part}`, `{title}`, `{file}`
- ☐ **Music symbol palette**: Bravura (SMuFL, OFL license) bundled; searchable glyph
  picker (breath mark, fermata, accidentals, dynamics, glasses, V.S., cut brackets, vi-de)
- ☐ **Minimal page editor** (built on the page view): click to place
  stamps, drag/resize/rotate them, delete, nudge with the arrow keys (seeded from `DrawingAreaWidget`)
- ☐ **Rehearsal marks and measure numbers**: a click-to-place tool with auto-increment
  (A, B, C… / 1, 2, 3…), a boxed, circled or plain style, and a style preset per ensemble
- ☐ **Part extractor**: define parts by page range or existing bookmark; batch-export
  each part with a filename template like `{Title} - {Part}.pdf`; save and reuse the part map for a
  title's later editions
- ☐ **Merge with bookmarks**: combine selected PDFs into one file, with a bookmark per source file
  (name taken from the file name or metadata, editable before export) and nested
  source bookmarks kept beneath it. Upstream only preserves existing outlines

## Phase 3: Preview-parity markup (built on the stamp engine + page editor)
- ☐ Shapes: rectangle, ellipse, line, arrow; stroke/fill color, width, opacity
- ☐ Free text boxes, edited in place
- ☐ Freehand ink with smoothing
- ☐ Highlight / underline / strikeout on text-bearing PDFs
- ☐ Interactive crop: drag a rectangle; apply it to this page or all selected pages
- ☐ Signature / reusable image stamps

## Phase 4: More sheet-music tools (unprioritized backlog)
- ☐ Scan cleanup: deskew, despeckle, threshold to black-and-white, normalize page size
- ☐ Imposition: 2-up tabloid, booklet order, foldout spreads
- ☐ Concert folder builder (program order, cover page, table of contents, bookmarks)
- ☐ Page-turn planner
- ☐ Music metadata (composer, arranger, instrument, catalog no.) → XMP, plus rename/export by metadata
- ☐ Excerpt builder (clip regions into an audition packet)
- ☐ Export presets for forScore / MobileSheets

## Phase 5: Stress testing & release hardening
- ☐ Large-file performance: 300+ page scanned scores, 1 GB inputs, memory ceiling, render times
- ☐ Hundreds of stamps per document; batch stamping across 50+ part files
- ☐ Check exports in Acrobat Reader, Preview, Chrome, Firefox, Edge, forScore, and MobileSheets
- ☐ Malformed/encrypted/odd PDFs (fuzzing corpus); PDFs with existing annotations or forms
- ☐ Property-based tests (Hypothesis) for the geometry invariants
- ☐ GUI tests (dogtail) extended for new tools, running in Linux CI
- ☐ Windows installer from the fork; rebrand (name, app ID, icon, config dir)

---

## Open decisions ❓
1. **Product name**: only needed before the first release.
2. **Re-editing after save**: should stamps stay editable in *our* app after you save and
   re-open the file (this needs a small project/sidecar file), or is editing only within the session enough?
3. **Upstream contributions**: offer generally useful pieces (merge with bookmarks,
   page editor) back to PDF Arranger, or stay independent?

## Alternatives considered (2026-11-22)
| Project | Why not the base |
|---|---|
| PDFsam Basic (Java) | Strong merge-with-bookmarks and split-by-bookmarks, but no visual page editing; Java |
| PDF4QT (C++/Qt) | Real editing and annotation tools, but a large C++ codebase |
| Stirling-PDF (Java, web) | Has batch stamping, but it's a self-hosted web app, not a desktop editor |
| Xournal++ (C++/GTK) | Excellent freehand markup, but weak page management; C++ |
| PyMuPDF-based tools | Powerful library, but AGPL licensing and no mature GUI to build on |

PDF Arranger wins because it's Python, its page model already does overlay layers, merging, and outline
preservation, and the flattened-output decision fits its export pipeline directly.
